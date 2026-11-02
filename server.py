#!/usr/bin/env python3
"""Local PC workbench for the authorized Central Kitchen content APIs.

Run: python3 server.py
Open: http://127.0.0.1:8789

Authentication tokens are stored only in a local owner-readable (0600) session
file. Passwords and SMS codes are never written to disk.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".kiro" / "crew" / "state"
SESSION_FILE = STATE_DIR / "central-kitchen-pc-session.json"
BASE = "https://lbb-gw.tojoycloud.com"
DIGITAL_BASE = "https://zhixiaotian-partner.laobanyun818.com/prod-api"
DIGITAL_CONFIG = Path(os.environ.get(
    "DIGITAL_BOSS_CONFIG",
    str(Path.home() / ".kiro" / "crew" / "workspace" / "boss-reverse" / "aigc-image-test" / "config.json"),
))
PORT = int(os.environ.get("CENTRAL_KITCHEN_PORT", "8796"))
SESSION: dict[str, object] = {"token": None, "mobile": None, "captcha": None, "materials": None}
JOBS: dict[str, dict[str, object]] = {}
JOBS_LOCK = threading.Lock()
REMIX_PROMPT = "根据视频进行改编。提示词为：改编一下附件的视频，要跟原视频有区别，包括背景/人物等，但要保留主要动作和镜头节奏，最终保留原视频音频。"
SCRATCH = Path(os.environ.get("KIROCREW_SCRATCH", str(Path.home() / ".kiro" / "crew" / "scratch")))
REMIX_OUTPUT_DIR = SCRATCH / "video-remix" / "central-kitchen-pc"


def load_persisted_token() -> None:
    """Load only a locally-created, owner-readable token file."""
    try:
        if not SESSION_FILE.exists():
            return
        os.chmod(SESSION_FILE, 0o600)
        value = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return
        token = value.get("token")
        mobile = value.get("mobile")
        if isinstance(token, str) and token.strip():
            SESSION["token"] = token.strip()
        if isinstance(mobile, str) and mobile.strip():
            SESSION["mobile"] = mobile.strip()
    except (OSError, ValueError, json.JSONDecodeError):
        # A malformed local state file cannot authenticate a request.
        SESSION["token"] = None


def persist_token(token: str, mobile: str | None = None) -> None:
    """Atomically persist the token with owner-only file permissions."""
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    temporary = SESSION_FILE.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump({"token": token, "mobile": mobile, "savedAt": int(time.time() * 1000)}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, SESSION_FILE)
        os.chmod(SESSION_FILE, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def clear_persisted_token() -> None:
    SESSION.update({"token": None, "mobile": None, "captcha": None, "materials": None})
    try:
        SESSION_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def clear_persisted_token() -> None:
    SESSION.update({"token": None, "mobile": None, "captcha": None, "materials": None})
    try:
        SESSION_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def load_digital_token() -> str:
    """Read the existing local Digital Boss credential without exposing it to the browser."""
    configured = os.environ.get("DIGITAL_BOSS_TOKEN", "").strip()
    if configured:
        return configured.removeprefix("Bearer ").strip()
    try:
        value = json.loads(DIGITAL_CONFIG.read_text(encoding="utf-8"))
        token = str(value.get("digital_boss_token", "")).strip()
        if token and not token.startswith("在这里"):
            return token.removeprefix("Bearer ").strip()
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    raise RuntimeError("本机没有找到数字老板登录态，请先在数字老板账号中登录一次")


def jwt_subject(token: str) -> str | None:
    """Read only the non-secret subject claim for account binding; signature is not changed."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8"))
        subject = payload.get("sub")
        return str(subject) if subject else None
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def digital_request(path: str, token: str, method: str = "GET", body: dict | None = None, query: dict | None = None) -> dict:
    url = DIGITAL_BASE + path
    if query:
        url += "?" + urlencode(query)
    request = Request(url, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("AppAuthorization", "Bearer " + token)
    request.add_header("Content-Type", "application/json")
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    if encoded is not None:
        request.data = encoded
    try:
        with urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"数字老板接口请求失败：{path}（{exc}）") from exc
    code = str(result.get("code", ""))
    if code in {"401", "000401"}:
        raise RuntimeError("数字老板登录状态已失效，请重新登录数字老板")
    if code == "403":
        raise RuntimeError(str(result.get("msg") or "数字老板账号没有执行此操作的权限"))
    if code not in {"", "200", "000000"}:
        raise RuntimeError(str(result.get("msg") or result.get("message") or f"数字老板接口失败（{code}）"))
    if result.get("success") is False or result.get("error") is True:
        raise RuntimeError(str(result.get("msg") or result.get("message") or "数字老板接口返回失败"))
    return result


def result_data(result: dict) -> dict:
    data = result.get("data")
    return data if isinstance(data, dict) else result


def nested_value(value: object, names: tuple[str, ...]) -> object | None:
    if isinstance(value, dict):
        for name in names:
            if value.get(name):
                return value[name]
        for child in value.values():
            found = nested_value(child, names)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = nested_value(child, names)
            if found:
                return found
    return None


def bind_digital_account(token: str) -> str:
    central_token = str(SESSION.get("token") or "")
    central_mobile = str(SESSION.get("mobile") or "").strip() or (jwt_subject(central_token) or "")
    digital_mobile = jwt_subject(token)
    if central_mobile and digital_mobile and central_mobile != digital_mobile:
        raise RuntimeError("当前中央厨房手机号与数字老板登录账号不一致，已停止提交，避免扣错账号算力")
    account = central_mobile or digital_mobile
    if not account:
        raise RuntimeError("无法确认数字老板算力账号，请重新登录中央厨房后再提交")
    return account


load_persisted_token()
TARGET_TABS = {
    "AI营销视频": ["客户痛点", "价值优势", "功能讲解"],
    "案例展示": ["成功案例"],
    "剪辑素材": ["空镜特效", "产品展示", "现场实拍"],
    "参考脚本": ["客户痛点", "价值优势"],
}


def api_post(path: str, payload: dict, token: str | None = None) -> dict:
    now = str(int(time.time() * 1000))
    # The Central Kitchen login protocol requires the body and header timestamps
    # to be identical. Both SMS issuance and verification use this helper.
    payload = dict(payload)
    if "t" in payload:
        payload["t"] = int(now)
    headers = {
        "Content-Type": "application/json",
        "p": "1",
        "c": "3",
        "v": "3.5.2",
        "t": now,
    }
    if token:
        headers["token"] = token
        headers["access_token"] = token
    request = Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def api_error(result: dict) -> tuple[int, dict]:
    return HTTPStatus.BAD_REQUEST, {"ok": False, "code": result.get("code"), "message": result.get("msg", "请求失败")}


def token_expired(result: dict) -> bool:
    return str(result.get("code", "")) == "000401" or "未登录" in str(result.get("msg", ""))


def ensure_authenticated(result: dict) -> None:
    if token_expired(result):
        clear_persisted_token()
        raise RuntimeError("登录状态已失效，请重新登录")



def name_of(row: dict) -> str:
    return str(row.get("categoryName") or row.get("moduleName") or row.get("name") or row.get("title") or "")


def find(rows: list[dict], wanted: str) -> dict | None:
    return next((row for row in rows if wanted == name_of(row)), None)


def compact(item: dict) -> dict:
    keys = ("id", "materialId", "theMaterialId", "title", "link", "materialType", "materialDesc", "size", "width", "height", "duration", "date", "createTime", "browseCountStr", "downloadCountStr", "cover", "coverUrl", "coverImage", "coverImageUrl", "thumbnail", "thumbnailUrl", "image", "imageUrl", "videoCover", "videoCoverUrl")
    return {key: item.get(key) for key in keys}


def collect_materials(token: str) -> dict:
    modules = api_post("/tbh-api/full-ip-content/appModule", {}, token)
    ensure_authenticated(modules)
    if modules.get("code") != "000000":
        raise RuntimeError(modules.get("msg", "模块读取失败"))
    module = find(modules.get("data") or [], "老板对接专区")
    if not module:
        raise RuntimeError("未找到“老板对接专区”模块")

    output = {"module": name_of(module), "updatedAt": int(time.time() * 1000), "groups": []}
    for first in module.get("categoryList") or []:
        top_name = name_of(first)
        if top_name not in TARGET_TABS:
            continue
        first_id = first.get("categoryId")
        second_response = api_post("/tbh-api/full-ip-content/appCategory", {"categoryId": first_id}, token)
        ensure_authenticated(second_response)
        if second_response.get("code") != "000000":
            continue
        second_rows = second_response.get("data") or []
        group = {"name": top_name, "id": first_id, "tabs": []}
        for desired_name in TARGET_TABS[top_name]:
            second = find(second_rows, desired_name)
            if not second:
                group["tabs"].append({"name": desired_name, "items": [], "available": False})
                continue
            tab = {"name": desired_name, "id": second.get("categoryId"), "items": [], "available": True}
            for material_type, material_label in ((1, "视频"), (2, "图片"), (3, "文案")):
                listing = api_post("/tbh-api/full-ip-content/appList", {
                    "page": 1,
                    "size": 50,
                    "firstColumnId": first_id,
                    "secondColumnId": second.get("categoryId"),
                    "materialType": material_type,
                    "sortType": 1,
                }, token)
                ensure_authenticated(listing)
                if listing.get("code") == "000000":
                    for raw_item in (listing.get("data") or {}).get("list") or []:
                        row = compact(raw_item)
                        detail_id = raw_item.get("id")
                        if detail_id and material_type in (1, 3):
                            detail = api_post("/tbh-api/full-ip-content/appDetail", {"id": detail_id}, token)
                            ensure_authenticated(detail)
                            detail_items = (detail.get("data") or {}).get("list") or []
                            if material_type == 3 and detail_items:
                                row.update(compact(detail_items[0]))
                            if material_type == 1:
                                row["videoCount"] = len(detail_items)
                                if detail_items:
                                    row.update(compact(detail_items[0]))
                            row["id"] = detail_id
                        row["kind"] = material_label
                        tab["items"].append(row)
            group["tabs"].append(tab)
        output["groups"].append(group)
    return output


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\\x00-\\x1f]", "_", value).strip(" .")
    return (cleaned or "视频改编成品")[:120]


def set_job(job_id: str, **changes: object) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(changes)


def download_generated_video(url: str, token: str, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    request = Request(url)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("AppAuthorization", "Bearer " + token)
    with urlopen(request, timeout=180) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if destination.stat().st_size < 1024:
        destination.unlink(missing_ok=True)
        raise RuntimeError("数字老板返回的成品文件为空或过小")


def wait_aigc_material(token: str, task_id: object, job_id: str) -> str:
    for attempt in range(360):
        result = digital_request(f"/system/video-mix/aigc-material/task/{task_id}", token)
        data = result_data(result)
        result_url = nested_value(result, ("downloadUrl", "videoUrl", "outputUrl", "resultUrl", "fileUrl", "url"))
        status = str(data.get("status") or result.get("status") or "").lower()
        set_job(job_id, progress=min(95, 10 + attempt // 3), providerStatus=status or "改编中")
        if result_url:
            return str(result_url)
        if status in {"failed", "error", "failure", "cancelled", "canceled", "3"}:
            raise RuntimeError(str(data.get("errorMessage") or data.get("message") or "数字老板参考视频改编失败"))
        time.sleep(5)
    raise RuntimeError("数字老板参考视频改编超过 30 分钟未完成")


def wait_aigc_video(token: str, work_id: object, job_id: str) -> str:
    for attempt in range(360):
        result = digital_request(f"/mp/aigc/works/{work_id}", token)
        data = result_data(result)
        result_url = nested_value(data, ("downloadUrl", "videoUrl", "outputUrl", "resultUrl"))
        status = str(data.get("status") or result.get("status") or "").lower()
        set_job(job_id, progress=min(95, 10 + attempt // 3), providerStatus=status or "生成中")
        if result_url:
            return str(result_url)
        if status in {"failed", "error", "fail", "cancelled", "canceled"}:
            raise RuntimeError(str(data.get("errorMessage") or data.get("message") or "数字老板 AI 生视频失败"))
        time.sleep(5)
    raise RuntimeError("数字老板 AI 生视频超过 30 分钟未完成")


def wait_video_mix(token: str, history_id: object, job_id: str) -> str:
    for attempt in range(360):
        result = digital_request(f"/system/video-mix/{history_id}", token)
        data = result_data(result)
        result_url = nested_value(data, ("firstVideoUrl", "videoUrl", "downloadUrl", "outputUrl", "resultUrl"))
        status = str(data.get("status") or result.get("status") or "").lower()
        set_job(job_id, progress=min(95, 10 + attempt // 3), providerStatus=status or "混剪中")
        if result_url:
            return str(result_url)
        if status in {"failed", "error", "failure", "cancelled", "canceled", "3"}:
            raise RuntimeError(str(data.get("errorMessage") or data.get("message") or "数字老板视频混剪失败"))
        time.sleep(5)
    raise RuntimeError("数字老板视频混剪超过 30 分钟未完成")


def run_remix_job(job_id: str, payload: dict) -> None:
    try:
        set_job(job_id, status="running", progress=5, message="正在校验数字老板账号")
        token = load_digital_token()
        account = bind_digital_account(token)
        kind = payload.get("kind")
        prompt = str(payload.get("prompt") or "").strip()
        title = str(payload.get("title") or "未命名物料").strip()
        set_job(job_id, account=account, message="已绑定数字老板账号，正在提交任务")

        if kind == "script":
            response = digital_request("/mp/aigc/video/create", token, "POST", {
                "prompt": prompt,
                "mode": "text-to-video",
                "ratio": "16:9",
                "duration": 10,
                "resolution": "720p",
            })
            data = result_data(response)
            work_id = nested_value(data, ("id", "workId", "taskId"))
            if not work_id:
                raise RuntimeError("数字老板未返回 AI 生视频任务编号")
            set_job(job_id, providerTaskId=str(work_id), powerCost=data.get("powerCost"), message="脚本已提交，正在生成视频")
            result_url = wait_aigc_video(token, work_id, job_id)
        elif kind == "video":
            video_url = str(payload.get("videoUrl") or "").strip()
            if not video_url.startswith(("http://", "https://")):
                raise RuntimeError("所选视频没有可用的远程地址")
            declared_duration = float(payload.get("duration") or 0)
            if declared_duration > 15.2:
                raise RuntimeError(f"参考视频约 {declared_duration:g} 秒，超过数字老板模型单段 15.2 秒限制；当前账号没有云盘视频切片权限，暂时无法全自动分段改编")
            imported = None
            import_error = None
            for attempt in range(3):
                try:
                    imported = digital_request("/system/cloud/import-url", token, "POST", {
                        "url": video_url,
                        "fileName": safe_filename(title),
                        "reuseOnDuplicate": True,
                        "autoAiAnalysis": False,
                    })
                    import_error = None
                    break
                except RuntimeError as exc:
                    import_error = exc
                    if "服务暂时不可用" not in str(exc) or attempt == 2:
                        raise
                    time.sleep(4 * (attempt + 1))
            if imported is None:
                raise RuntimeError(str(import_error or "数字老板云盘导入失败"))
            # import-url returns fileId in data and downloadUrl at the top level.
            # The reference-video generator consumes the cloud file ID, not the
            # original Central Kitchen URL.
            file_id = nested_value(imported, ("fileId",))
            if not file_id:
                # Some deployments return the cloud record asynchronously on a
                # duplicate import. Retry once with a unique filename, without
                # retrying the AI generation submission itself.
                retry_name = safe_filename(title) + "_" + job_id[:8]
                imported = digital_request("/system/cloud/import-url", token, "POST", {
                    "url": video_url,
                    "fileName": retry_name,
                    "reuseOnDuplicate": False,
                    "autoAiAnalysis": False,
                })
                file_id = nested_value(imported, ("fileId",))
            if not file_id:
                response_shape = {
                    "topKeys": list(imported.keys()),
                    "dataKeys": list(imported.get("data", {}).keys()) if isinstance(imported.get("data"), dict) else type(imported.get("data")).__name__,
                }
                raise RuntimeError(f"数字老板云盘导入成功但没有返回参考视频 fileId（{response_shape}）")
            set_job(job_id, importedFileId=file_id, message="参考视频已进入数字老板云盘，正在提交改编")
            video_format = int(payload.get("videoFormat") or 2)
            response = digital_request("/system/video-mix/aigc-material/generate", token, "POST", {
                "prompt": prompt,
                "mode": "video-to-video",
                "ratio": "9:16" if video_format == 1 else "16:9",
                "resolution": "720p",
                "referenceVideoFileId": int(file_id),
            })
            task_id = nested_value(response, ("taskId", "jobId", "id"))
            if not task_id:
                raise RuntimeError("数字老板未返回参考视频改编任务编号")
            data = result_data(response)
            set_job(job_id, providerTaskId=str(task_id), powerCost=data.get("powerCost") or response.get("powerCost"), message="改编任务已提交，正在生成视频")
            result_url = wait_aigc_material(token, task_id, job_id)
        else:
            raise RuntimeError("不支持的创作类型")

        output = REMIX_OUTPUT_DIR / (job_id + "-" + safe_filename(title) + ".mp4")
        set_job(job_id, progress=98, message="正在下载成品")
        download_generated_video(result_url, token, output)
        set_job(job_id, status="succeeded", progress=100, message="视频已生成，可下载", output=str(output), remoteUrl=result_url)
    except Exception as exc:
        set_job(job_id, status="failed", progress=100, message=str(exc))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        # The workbench can be opened through the dashboard preview shell, whose
        # origin differs from this loopback server. Limit this to local development.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def send_json(self, status: int, data: dict) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size > 32_768:
            raise ValueError("请求过大")
        return json.loads(self.rfile.read(size).decode("utf-8") or "{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/session":
            mobile = str(SESSION.get("mobile") or "")
            masked = (mobile[:3] + "****" + mobile[-4:]) if len(mobile) == 11 else None
            self.send_json(HTTPStatus.OK, {"ok": True, "loggedIn": bool(SESSION["token"]), "hasMaterials": bool(SESSION["materials"]), "account": masked})
            return
        if path == "/api/remix-status":
            query = parse_qs(urlparse(self.path).query)
            job_id = (query.get("id") or [""])[0]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id) or {})
            if not job:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "找不到视频任务"})
                return
            output = job.pop("output", None)
            if output and Path(str(output)).exists():
                job["downloadPath"] = f"/api/remix-download?id={job_id}"
            self.send_json(HTTPStatus.OK, {"ok": True, "data": job})
            return
        if path == "/api/remix-download":
            query = parse_qs(urlparse(self.path).query)
            job_id = (query.get("id") or [""])[0]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id) or {})
            output = Path(str(job.get("output") or ""))
            if not output.exists() or output.parent != REMIX_OUTPUT_DIR:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", f'attachment; filename="{output.name}"')
            self.send_header("Content-Length", str(output.stat().st_size))
            self.end_headers()
            with output.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    self.wfile.write(chunk)
            return
        if path == "/api/materials":
            if not SESSION["token"]:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "message": "请先登录中央厨房"})
                return
            if not SESSION["materials"]:
                try:
                    SESSION["materials"] = collect_materials(str(SESSION["token"]))
                except Exception as exc:
                    self.send_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "message": str(exc)})
                    return
            self.send_json(HTTPStatus.OK, {"ok": True, "data": SESSION["materials"]})
            return
        if path == "/api/material-detail":
            if not SESSION["token"]:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "message": "登录状态已失效，请重新登录"})
                return
            query = parse_qs(urlparse(self.path).query)
            raw_id = (query.get("id") or [""])[0]
            if not raw_id.isdigit():
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "缺少有效的物料编号"})
                return
            result = api_post("/tbh-api/full-ip-content/appDetail", {"id": int(raw_id)}, str(SESSION["token"]))
            ensure_authenticated(result)
            if result.get("code") != "000000":
                status, out = api_error(result)
                self.send_json(status, out)
                return
            data = result.get("data") or {}
            items = []
            for item in data.get("list") or []:
                row = compact(item)
                row["kind"] = "视频" if item.get("materialType") == 1 else ("图片" if item.get("materialType") == 2 else "文案")
                items.append(row)
            self.send_json(HTTPStatus.OK, {"ok": True, "data": {"items": items, "materialType": data.get("materialType")}})
            return
        if path == "/media/verified-video":
            video = ROOT.parent / "boss-reverse" / "central-kitchen-downloads" / "赚大钱的合作_大多来自陌生人_mp4_第二个视频.mp4"
            if not video.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(video.stat().st_size))
            self.end_headers()
            with video.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    self.wfile.write(chunk)
            return
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.body()
            if path == "/api/captcha":
                result = api_post("/tbh-api/public/auth/img-code", {})
                if result.get("code") != "000000":
                    status, out = api_error(result)
                    self.send_json(status, out)
                    return
                data = result.get("data") or {}
                SESSION["captcha"] = data.get("imgId")
                image = data.get("imgCode", "")
                self.send_json(HTTPStatus.OK, {"ok": True, "image": image})
                return
            if path == "/api/send-code":
                mobile = str(payload.get("mobile", ""))
                result = api_post("/tbh-api/public/auth/verify-code", {
                    "mobile": mobile,
                    "imgId": SESSION.get("captcha"),
                    "imgCode": payload.get("captcha", ""),
                    "t": int(time.time() * 1000),
                })
                if result.get("code") != "000000":
                    status, out = api_error(result)
                    self.send_json(status, out)
                    return
                self.send_json(HTTPStatus.OK, {"ok": True, "message": "验证码已发送"})
                return
            if path == "/api/login":
                mobile = str(payload.get("mobile", ""))
                verify_code = str(payload.get("verifyCode", ""))
                result = api_post("/tbh-api/public/auth/login", {
                    "type": 1,
                    "mobile": mobile,
                    "verifyCode": verify_code,
                    "t": int(time.time() * 1000),
                })
                if result.get("code") != "000000" or not (result.get("data") or {}).get("token"):
                    status, out = api_error(result)
                    self.send_json(status, out)
                    return
                SESSION["token"] = result["data"]["token"]
                SESSION["mobile"] = mobile
                persist_token(str(SESSION["token"]), mobile)
                SESSION["materials"] = None
                self.send_json(HTTPStatus.OK, {"ok": True, "message": "登录成功"})
                return
            if path == "/api/logout":
                token = str(SESSION["token"]) if SESSION.get("token") else None
                if token:
                    try:
                        api_post("/tbh-api/logout", {}, token)
                    except Exception:
                        # The local session must still be cleared if the upstream
                        # logout call is unavailable or the token already expired.
                        pass
                clear_persisted_token()
                self.send_json(HTTPStatus.OK, {"ok": True, "message": "已退出登录"})
                return
            if path == "/api/remix":
                if not SESSION.get("token"):
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "message": "中央厨房登录状态已失效，请重新登录"})
                    return
                kind = str(payload.get("kind") or "")
                prompt = str(payload.get("prompt") or "").strip()
                title = str(payload.get("title") or "未命名物料").strip()
                if kind not in {"script", "video"} or not prompt:
                    self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "缺少有效的提示词或创作类型"})
                    return
                if kind == "video" and not str(payload.get("videoUrl") or "").startswith(("http://", "https://")):
                    self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "所选视频没有可用的远程地址"})
                    return
                job_id = uuid.uuid4().hex
                with JOBS_LOCK:
                    JOBS[job_id] = {"status": "queued", "progress": 0, "message": "任务已排队", "kind": kind, "title": title}
                threading.Thread(target=run_remix_job, args=(job_id, payload), daemon=True).start()
                self.send_json(HTTPStatus.OK, {"ok": True, "data": {"id": job_id, "status": "queued"}})
                return
                if not SESSION["token"]:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "message": "请先登录中央厨房"})
                    return
                SESSION["materials"] = collect_materials(str(SESSION["token"]))
                self.send_json(HTTPStatus.OK, {"ok": True, "message": "物料已更新"})
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})


if __name__ == "__main__":
    print(f"Central Kitchen PC workbench: http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
