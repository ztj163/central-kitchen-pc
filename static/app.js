const PAGE_MAP = {
  "AI营销视频": "ai-marketing-videos.html",
  "案例展示": "cases.html",
  "剪辑素材": "editing-materials.html",
  "参考脚本": "scripts.html"
};

const state = { data: null, activeTab: null };

const API_BASE = "http://127.0.0.1:8796";
const VIDEO_REMIX_PROMPT = "根据视频进行改编。提示词为：改编一下附件的视频，要跟原视频有区别，包括背景/人物等，但要保留主要动作和镜头节奏，最终保留原视频音频。";

async function request(url, options = {}) {
  try {
    const response = await fetch(API_BASE + url, {
      headers: { "Content-Type": "application/json" },
      ...options
    });
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.message || "请求失败");
    return body;
  } catch (error) {
    if (error instanceof TypeError) throw new Error("本地中央厨房服务连接失败，请从 http://127.0.0.1:8796 打开页面后重试。");
    throw error;
  }
}

function header(active) {
  return `<header class="header">
    <a class="brand" href="ai-marketing-videos.html"><span class="brand-mark">B</span><span>老板对接<small>全员 IP 中央厨房</small></span></a>
    <nav class="header-nav"><a class="${active === "AI营销视频" ? "active" : ""}" href="ai-marketing-videos.html">中央厨房</a></nav>
    <div class="user-status"><i class="dot"></i><span id="session-status">检测登录状态</span><button class="logout-button" id="logout" type="button" hidden>退出</button></div>
  </header>`;
}

function videoModal() {
  return `<div class="modal" id="video-modal"><div class="modal-box"><div class="modal-top"><strong id="video-title">视频播放</strong><button class="close" aria-label="关闭">×</button></div><video id="video-player" controls autoplay></video></div></div>`;
}

function scriptText(item) {
  const title = String(item.title || "").trim();
  const normalizedTitle = title.replace(/^标题\s*[:：]\s*/, "").trim();
  let content = String(item.materialDesc || "").trim();
  const scriptMarker = content.match(/脚本\s*[:：]/);
  if (scriptMarker && scriptMarker.index !== undefined) {
    content = content.slice(scriptMarker.index + scriptMarker[0].length).trim();
  } else {
    content = content.replace(/^标题\s*[:：]\s*/, "").trim();
    if (normalizedTitle && content.startsWith(normalizedTitle)) content = content.slice(normalizedTitle.length).trim();
  }
  return content || "暂无脚本正文";
}

function cleanScriptContent(item) { return escapeHtml(scriptText(item)); }

function materialCard(item, groupName) {
  const title = escapeHtml(item.title || "未命名物料");
  const desc = escapeHtml(item.materialDesc || (item.kind === "文案" ? "可直接用于短视频创作、口播与营销素材二次编辑。" : "来自全员 IP 中央厨房的授权营销物料。"));
  const meta = `${escapeHtml(item.date || "中央厨房")}${item.duration ? ` · ${Math.round(item.duration)} 秒` : ""}`;
  const isVideo = item.kind === "视频";
  const hasMultipleVideos = isVideo && Number(item.videoCount || 0) > 1;
  const selectButton = groupName === "参考脚本" && item.kind === "文案"
    ? `<div class="card-actions"><button class="button secondary select-material" type="button" data-select-kind="脚本" data-select-title="${escapeAttr(item.title || "未命名脚本")}" data-select-prompt="${escapeAttr(scriptText(item))}">选择此脚本</button></div>`
    : "";
  if (item.kind === "文案") return `<article class="card text-card"><div class="card-body"><span class="kind">文案</span><div class="card-title">${title}</div><div class="script-content">${cleanScriptContent(item)}</div>${selectButton}<div class="card-meta"><span>${meta}</span><span>${escapeHtml(item.browseCountStr || "")}</span></div></div></article>`;
  const cover = item.coverUrl || item.coverImageUrl || item.videoCoverUrl || item.coverImage || item.videoCover || item.cover || item.thumbnailUrl || item.thumbnail || item.imageUrl || item.image || "";
  const image = item.kind === "图片" && item.link
    ? `<img src="${escapeAttr(item.link)}" alt="${title}">`
    : (isVideo && item.link
      ? `<video class="video-cover" src="${escapeAttr(item.link)}" ${cover ? `poster="${escapeAttr(cover)}"` : ""} muted playsinline preload="metadata" aria-label="${title}"></video><span class="play">▶</span>`
      : (isVideo ? `<span class="play">▶</span>` : "◈"));
  return `<article class="card ${isVideo ? "playable" : ""}" ${isVideo && item.link ? `data-video="${escapeAttr(item.link)}" data-detail-id="${escapeAttr(item.id || "")}" data-title="${escapeAttr(item.title || "视频播放")}"` : ""}><div class="card-media ${isVideo ? "video" : ""}"><span class="kind">${escapeHtml(item.kind || "物料")}</span>${image}</div><div class="card-body"><div class="card-title">${title}</div><div class="card-desc">${desc}</div>${selectButton}<div class="card-meta"><span>${meta}</span><span>${escapeHtml(item.downloadCountStr || "")}</span></div></div></article>`;
}

function escapeHtml(text) { const box = document.createElement("div"); box.textContent = String(text); return box.innerHTML; }
function escapeAttr(text) { return String(text).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); }

function selectionModal() {
  return `<div class="modal" id="selection-modal"><div class="confirm-box"><div class="modal-top"><strong id="selection-title">确认选择此物料吗？</strong><button class="close" type="button" aria-label="关闭">×</button></div><p id="selection-name"></p><div class="confirm-actions"><button class="button secondary" type="button" id="selection-cancel">取消</button><button class="button" type="button" id="selection-confirm">确定</button></div></div></div>`;
}

function remixStatusModal() {
  return `<div class="modal" id="remix-status-modal"><div class="confirm-box remix-status-box"><div class="modal-top"><strong>天九数字老板正在生成</strong><button class="close" type="button" aria-label="关闭">×</button></div><div class="remix-status-content"><span class="spinner" id="remix-spinner"></span><strong id="remix-status-message">任务已排队</strong><div class="remix-progress"><i id="remix-progress-bar"></i></div><p id="remix-status-detail">正在使用已登录账号的算力处理。</p><a id="remix-download" class="button" hidden target="_blank" rel="noopener">下载生成视频</a></div></div></div>`;
}

function openRemixStatus() {
  const modal = document.querySelector("#remix-status-modal");
  if (modal) modal.classList.add("show");
}

function updateRemixStatus(data) {
  const modal = document.querySelector("#remix-status-modal");
  if (!modal) return;
  const message = modal.querySelector("#remix-status-message");
  const detail = modal.querySelector("#remix-status-detail");
  const bar = modal.querySelector("#remix-progress-bar");
  const spinner = modal.querySelector("#remix-spinner");
  const download = modal.querySelector("#remix-download");
  message.textContent = data.message || "正在处理";
  detail.textContent = data.providerStatus ? `数字老板状态：${data.providerStatus}` : "正在使用已登录账号的算力处理。";
  bar.style.width = `${Math.max(0, Math.min(100, Number(data.progress || 0)))}%`;
  if (data.status === "succeeded") {
    spinner.hidden = true;
    download.hidden = false;
    download.href = API_BASE + (data.downloadPath || "");
  } else if (data.status === "failed") {
    spinner.hidden = true;
    download.hidden = true;
    detail.textContent = data.message || "生成失败";
    detail.classList.add("error-text");
  } else {
    download.hidden = true;
  }
}

async function watchRemixJob(jobId) {
  openRemixStatus();
  for (;;) {
    try {
      const result = await request(`/api/remix-status?id=${encodeURIComponent(jobId)}`);
      const data = result.data || {};
      updateRemixStatus(data);
      if (data.status === "succeeded" || data.status === "failed") return;
    } catch (error) {
      updateRemixStatus({ status: "failed", message: error.message, progress: 100 });
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 3000));
  }
}

function wireRemixStatusModal() {
  const modal = document.querySelector("#remix-status-modal");
  if (!modal) return;
  modal.querySelector(".close")?.addEventListener("click", () => modal.classList.remove("show"));
}

async function submitSelection(button) {
  const kind = button.dataset.selectKind === "脚本" ? "script" : "video";
  const payload = {
    kind,
    title: button.dataset.selectTitle || "未命名物料",
    prompt: kind === "script" ? (button.dataset.selectPrompt || "") : VIDEO_REMIX_PROMPT,
    videoUrl: button.dataset.selectVideo || "",
    videoFormat: button.dataset.selectFormat || "2",
    duration: button.dataset.selectDuration || ""
  };
  if (kind === "video" && !payload.videoUrl) throw new Error("当前视频没有可用的参考地址");
  button.disabled = true;
  button.dataset.originalText = button.textContent;
  button.textContent = "提交中…";
  const result = await request("/api/remix", { method: "POST", body: JSON.stringify(payload) });
  button.textContent = kind === "script" ? "已选择此脚本" : "已选择";
  button.closest(".card, .video-list-item")?.classList.add("selected");
  return result.data.id;
}

function wireSelectionModal() {
  const modal = document.querySelector("#selection-modal");
  if (!modal) return;
  let current = null;
  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".select-material");
    if (button) {
      current = button;
      const kind = button.dataset.selectKind || "物料";
      document.querySelector("#selection-title").textContent = `确认选择此${kind}吗？`;
      document.querySelector("#selection-name").textContent = button.dataset.selectTitle || "";
      modal.classList.add("show");
      return;
    }
    if (event.target === modal || event.target.closest("#selection-cancel") || event.target.closest(".close")) {
      modal.classList.remove("show");
      current = null;
      return;
    }
    if (event.target.closest("#selection-confirm") && current) {
      const selected = current;
      current = null;
      modal.classList.remove("show");
      try {
        const jobId = await submitSelection(selected);
        await watchRemixJob(jobId);
      } catch (error) {
        selected.disabled = false;
        selected.textContent = selected.dataset.originalText || (selected.dataset.selectKind === "脚本" ? "选择此脚本" : "选择它");
        alert(error.message);
      }
    }
  });
}

function wireVideoModal() {
  const modal = document.querySelector("#video-modal");
  if (!modal) return;
  const player = modal.querySelector("video");
  document.addEventListener("click", (event) => {
    if (event.target.closest(".select-material")) return;
    const target = event.target.closest(".playable");
    if (target?.dataset.detailId) {
      const query = new URLSearchParams({ id: target.dataset.detailId, title: target.dataset.title || "视频列表" });
      location.href = `video-list.html?${query.toString()}`;
      return;
    }
    if (target?.dataset.video) {
      modal.querySelector("#video-title").textContent = target.dataset.title;
      player.src = target.dataset.video;
      modal.classList.add("show");
      player.play().catch(() => {});
    }
    if (event.target === modal || event.target.closest(".close")) {
      player.pause(); player.removeAttribute("src"); player.load(); modal.classList.remove("show");
    }
  });
}

function wireVideoCovers() {
  document.querySelectorAll(".video-cover:not([data-cover-ready]), .video-list-media video:not([data-cover-ready])").forEach((video) => {
    video.dataset.coverReady = "true";
    video.addEventListener("loadedmetadata", () => {
      if (Number.isFinite(video.duration) && video.duration > 0.15) video.currentTime = 0.15;
    }, { once: true });
    video.addEventListener("seeked", () => video.pause(), { once: true });
  });
}

async function syncSession() {
  const status = document.querySelector("#session-status");
  const logout = document.querySelector("#logout");
  try {
    const session = await request("/api/session");
    status.textContent = session.loggedIn ? "已登录 · 可刷新物料" : "未登录";
    if (logout) {
      logout.hidden = !session.loggedIn;
      logout.onclick = async () => {
        logout.disabled = true;
        try {
          await request("/api/logout", { method: "POST", body: "{}" });
          location.href = "index.html";
        } catch (error) {
          logout.disabled = false;
          alert(error.message);
        }
      };
    }
    return session;
  } catch {
    status.textContent = "状态不可用";
    if (logout) logout.hidden = true;
    return { loggedIn: false };
  }
}

async function getMaterials() {
  if (state.data) return state.data;
  const result = await request("/api/materials");
  state.data = result.data;
  return state.data;
}

function groupFor(name) { return state.data?.groups?.find((group) => group.name === name); }

async function renderGroup(name) {
  const host = document.querySelector("#material-host");
  const tabs = document.querySelector("#sub-tabs");
  const loginTip = `<div class="empty"><div><strong>登录后加载中央厨房物料</strong><p>输入手机号并完成验证码登录后，系统会自动读取本页签下的全部视频、图片与文案。</p><p style="margin-top:16px"><a class="button" href="index.html">去登录</a></p></div></div>`;
  const plannedTabs = {"AI营销视频":["客户痛点","价值优势","功能讲解"],"案例展示":["成功案例"],"剪辑素材":["空镜特效","产品展示","现场实拍"],"参考脚本":["客户痛点","价值优势"]}[name] || [];
  tabs.classList.add("is-loading");
  tabs.setAttribute("aria-busy", "true");
  tabs.querySelectorAll("button").forEach(button => { button.disabled = true; });
  host.innerHTML = `<div class="loading-state"><span class="spinner"></span><strong>正在加载${escapeHtml(name)}物料</strong><p>正在读取视频、图片和文案，请稍候。</p></div>`;
  try {
    const data = await getMaterials();
    const group = groupFor(name);
    if (!group) { host.innerHTML = `<div class="empty"><div><strong>当前账号暂无“${name}”权限或内容</strong><p>请刷新物料，或确认中央厨房的栏目配置。</p></div></div>`; return; }
    const availableTabs = group.tabs;
    state.activeTab = state.activeTab && availableTabs.some(t => t.name === state.activeTab) ? state.activeTab : availableTabs[0]?.name;
    tabs.innerHTML = availableTabs.map(tab => `<button class="${tab.name === state.activeTab ? "active" : ""}" data-subtab="${escapeAttr(tab.name)}" ${tab.available ? "" : "disabled"}>${escapeHtml(tab.name)}</button>`).join("");
    const active = availableTabs.find(tab => tab.name === state.activeTab);
    host.innerHTML = active?.items?.length ? `<div class="material-grid">${active.items.map(item => materialCard(item, name)).join("")}</div>` : `<div class="empty"><div><strong>暂未发布物料</strong><p>“${escapeHtml(state.activeTab || "当前") }”页签暂未返回可展示的内容。</p></div></div>`;
    wireVideoCovers();
    tabs.querySelectorAll("button[data-subtab]").forEach(button => button.addEventListener("click", () => { state.activeTab = button.dataset.subtab; renderGroup(name); }));
  } catch (error) {
    state.data = null;
    if (/登录/.test(error.message)) {
      tabs.innerHTML = plannedTabs.map((tab, index) => `<button class="${index === 0 ? "active" : ""}" disabled>${escapeHtml(tab)}</button>`).join("");
      host.innerHTML = loginTip;
    } else {
      host.innerHTML = `<div class="empty"><div><strong>物料读取失败</strong><p>${escapeHtml(error.message)}</p><p style="margin-top:16px"><button class="button secondary" onclick="location.reload()">重新加载</button></p></div></div>`;
    }
  } finally {
    tabs.classList.remove("is-loading");
    tabs.removeAttribute("aria-busy");
  }
}

async function initContentPage(name, subtitle) {
  document.body.insertAdjacentHTML("afterbegin", `<main class="shell">${header(name)}<section class="hero"><div><div class="eyebrow">BOSSLINK · CONTENT CENTER</div><h1>${name}</h1><p>${subtitle}</p></div><button class="button secondary" id="refresh">刷新物料</button></section><nav class="top-tabs">${Object.entries(PAGE_MAP).map(([label, file]) => `<a class="${label === name ? "active" : ""}" href="${file}">${label}</a>`).join("")}</nav><div class="sub-tabs" id="sub-tabs"></div><section class="content" id="material-host"></section><p class="footer-note">物料由全员 IP 中央厨房接口实时读取。视频点击后在浏览器弹窗内播放；登录令牌仅以本机 0600 权限保存，服务端确认失效后会提示重新登录。</p></main>${videoModal()}${selectionModal()}${remixStatusModal()}`);
  await syncSession();
  await renderGroup(name);
  document.querySelector("#refresh").addEventListener("click", async () => {
    try { await request("/api/refresh-materials", { method: "POST", body: "{}" }); state.data = null; await renderGroup(name); } catch (error) { alert(error.message); }
  });
  wireVideoModal();
  wireSelectionModal();
  wireRemixStatusModal();
}

async function initVideoListPage() {
  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  const title = params.get("title") || "视频列表";
  document.body.insertAdjacentHTML("afterbegin", `<main class="shell">${header("AI营销视频")}<section class="hero detail-hero"><div><div class="eyebrow">BOSSLINK · VIDEO LIST</div><h1>${escapeHtml(title)}</h1><p id="video-list-description">该物料包含多个视频，点击下方视频即可播放。</p></div><a class="button secondary" href="ai-marketing-videos.html">返回上一级</a></section><section class="content" id="video-list-host"><div class="empty"><div><strong>正在加载视频列表</strong><p>正在读取该物料的全部视频。</p></div></div></section><p class="footer-note">视频列表来自中央厨房物料详情接口。</p></main>${selectionModal()}${remixStatusModal()}`);
  await syncSession();
  const host = document.querySelector("#video-list-host");
  if (!id) {
    host.innerHTML = `<div class="empty"><div><strong>缺少物料编号</strong><p>无法读取该视频列表。</p></div></div>`;
    return;
  }
  try {
    const result = await request(`/api/material-detail?id=${encodeURIComponent(id)}`);
    const items = result.data?.items || [];
    const description = document.querySelector("#video-list-description");
    if (description && items.length <= 1) description.hidden = true;
    if (!items.length) {
      host.innerHTML = `<div class="empty"><div><strong>暂无视频</strong><p>该物料暂未返回可播放的视频。</p></div></div>`;
      return;
    }
    host.innerHTML = `<div class="video-list">${items.map((item, index) => {
      const itemTitle = escapeHtml(item.title || `${title} · 视频 ${index + 1}`);
      const cover = item.coverUrl || item.coverImageUrl || item.videoCoverUrl || item.coverImage || item.videoCover || item.cover || item.thumbnailUrl || item.thumbnail || item.imageUrl || item.image || "";
      const orientation = Number(item.height || 0) > Number(item.width || 0) ? "portrait" : "landscape";
      const usage = item.downloadCountStr || item.browseCountStr || "使用次数";
      return `<article class="video-list-item ${orientation}"><div class="video-list-media"><video controls muted preload="metadata" playsinline ${cover ? `poster="${escapeAttr(cover)}"` : ""} src="${escapeAttr(item.link || "")}"></video></div><div class="video-list-info"><h2>${itemTitle}</h2><div class="video-list-meta"><span>${escapeHtml(usage)}</span><button class="button secondary select-material" type="button" data-select-kind="物料" data-select-title="${itemTitle}" data-select-video="${escapeAttr(item.link || "")}" data-select-prompt="${escapeAttr(VIDEO_REMIX_PROMPT)}" data-select-format="${orientation === "portrait" ? "1" : "2"}" data-select-duration="${escapeAttr(item.duration || "")}">选择它</button></div></div></article>`;
    }).join("")}</div>`;
    wireVideoCovers();
    wireSelectionModal();
    wireRemixStatusModal();
  } catch (error) {
    host.innerHTML = `<div class="empty"><div><strong>视频列表读取失败</strong><p>${escapeHtml(error.message)}</p><p style="margin-top:16px"><a class="button secondary" href="ai-marketing-videos.html">返回上一页</a></p></div></div>`;
  }
}

async function initOverview() {
  document.body.insertAdjacentHTML("afterbegin", `<main class="shell">${header("中央厨房")}<section class="hero"><div><div class="eyebrow">CONTENT OPERATIONS WORKBENCH</div><h1>全员 IP 中央厨房</h1><p>统一浏览老板对接专区的营销视频、案例、剪辑素材与参考脚本。登录后自动同步所有页签物料。</p></div><a class="button" href="index.html">手机号登录</a></section><nav class="top-tabs">${Object.entries(PAGE_MAP).map(([name, file]) => `<a href="${file}">${name}</a>`).join("")}</nav><section id="material-host" class="content"></section><p class="footer-note">已验证样本：客户痛点列表第 2 个视频已下载完成。登录后可读取并浏览全部分类内容。</p></main>${videoModal()}`);
  const session = await syncSession();
  const host = document.querySelector("#material-host");
  if (!session.loggedIn) host.innerHTML = `<div class="empty"><div><strong>请先登录以加载实时物料</strong><p>当前页面可展示中央厨房的四个物料专区。完成手机验证码登录后，系统会自动同步全部分类。</p><p style="margin-top:16px"><a class="button" href="index.html">去登录</a></p></div></div>`;
  else { state.data = await getMaterials(); const groups = state.data.groups || []; host.innerHTML = `<div class="material-grid">${groups.map(group => `<a class="card" href="${PAGE_MAP[group.name]}"><div class="card-media"><span style="font-size:40px">◈</span></div><div class="card-body"><div class="card-title">${escapeHtml(group.name)}</div><div class="card-desc">${group.tabs.map(t => escapeHtml(t.name)).join(" · ")}</div><div class="card-meta"><span>${group.tabs.reduce((n,t) => n + t.items.length,0)} 条物料</span><span>进入专区 →</span></div></div></a>`).join("")}</div>`; }
  wireVideoModal();
}
