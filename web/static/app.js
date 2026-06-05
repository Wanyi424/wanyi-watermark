(function () {
"use strict";

const KEY_STORE = "wanyi_dashscope_key";   // localStorage key（契约，勿改）
const SF_KEY_STORE = "wanyi_siliconflow_key";
let serverKeyConfigured = false;
let serverBackendDefault = "dashscope";
let serverDashscopeConfigured = false;
let serverSiliconflowConfigured = false;
let lastTranscript = "";
let lastTranscriptTitle = "";
let galleryImages = [];   // 当前图集（供下载菜单 / 一键下载全部使用）
const lightboxState = {
    scale: 1,
    minScale: 1,
    maxScale: 6,
    x: 0,
    y: 0,
    pointers: new Map(),
    dragStart: null,
    pinchStart: null,
    loadId: 0,
};

// --- 竞态防护 & 区域状态 ---
let currentParseId = 0;
let currentExtractId = 0;
let parseAbortCtrl = null;
let extractAbortCtrl = null;
let currentParseData = null;
let currentHistoryEntryId = null;

const $ = (id) => document.getElementById(id);
const PLATFORM_LABEL = { douyin: "抖音", xiaohongshu: "小红书", generic: "通用平台" };
const PLATFORM_ICON  = { douyin: "fa-brands fa-tiktok", xiaohongshu: "fa-solid fa-book-open", generic: "fa-solid fa-globe" };

/* ---------- 工具 ---------- */
function fmtDuration(ms) {
    if (!Number.isFinite(ms)) return "-";
    return ms >= 1000 ? (ms / 1000).toFixed(2) + "s" : Math.round(ms) + "ms";
}
function firstUrl(text) {
    const m = String(text || "").match(/https?:\/\/[^\s"'，。、）)】>]+/);
    return m ? m[0] : "";
}
function shortPreview(text, limit) {
    const s = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    return s.length > limit ? s.slice(0, limit) + "..." : s;
}
function resultResourceSummary(data) {
    if (!data || typeof data !== "object") return "未知";
    if (Array.isArray(data.images)) return data.images.length + " 张图片";
    if (data.url) return "1 个视频/媒体直链";
    return "0 个资源";
}
function createParseFlowLogger(rawInput) {
    const c = window.console || {};
    const traceId = "web-" + Date.now().toString(36) + "-" + Math.random().toString(16).slice(2, 8);
    const started = performance.now();
    let last = started;
    let closed = false;
    const rows = [];
    const label = "[解析流程 " + traceId + "]";

    if (c.groupCollapsed) c.groupCollapsed(label + " 点击「获取信息」");
    else if (c.info) c.info(label + " 点击「获取信息」");

    function mark(step, extra) {
        const now = performance.now();
        const row = Object.assign({
            "步骤": step,
            "本步耗时": fmtDuration(now - last),
            "累计耗时": fmtDuration(now - started),
        }, extra || {});
        rows.push(row);
        if (c.info) c.info(label + " " + step, row);
        last = now;
    }

    function finish(status, extra) {
        if (closed) return;
        mark("流程结束：" + status, extra);
        if (c.table) c.table(rows);
        if (c.groupEnd) c.groupEnd();
        closed = true;
    }

    mark("读取输入并生成追踪 ID", {
        "追踪ID": traceId,
        "输入长度": String(rawInput || "").length,
        "链接预览": shortPreview(firstUrl(rawInput) || rawInput, 140),
    });
    return { traceId, mark, finish };
}
function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
}
function escapeAttr(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;"); }
function jsStr(s) { return JSON.stringify(String(s == null ? "" : s)).replace(/</g, "\\u003c"); }
function getKey() { return localStorage.getItem(KEY_STORE) || ""; }
function getSfKey() { return localStorage.getItem(SF_KEY_STORE) || ""; }
function getEffectiveBackend() { return serverBackendDefault || "dashscope"; }
function hasKey() {
    const be = getEffectiveBackend();
    if (be === "siliconflow") return serverSiliconflowConfigured || !!getSfKey();
    return serverDashscopeConfigured || !!getKey();
}

function proxyUrl(u, download, filename) {
    let s = "/api/proxy?url=" + encodeURIComponent(u);
    if (download) s += "&download=1";
    if (filename) s += "&filename=" + encodeURIComponent(filename);
    return s;
}

/* ---------- Toast ---------- */
function toast(msg, type) {
    const wrap = $("toastWrap");
    const el = document.createElement("div");
    el.className = "toast" + (type === "err" ? " err" : "");
    const icon = type === "err" ? "fa-circle-exclamation" : "fa-circle-check";
    el.innerHTML = '<i class="fa-solid ' + icon + '" aria-hidden="true"></i><span></span>';
    el.querySelector("span").textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 260); }, 2000);
}

/* ---------- API 胶囊 ---------- */
function refreshChip() {
    const chip = $("apiChip"), text = $("apiChipText");
    if (hasKey()) {
        chip.classList.add("ok");
        const be = getEffectiveBackend();
        const label = be === "siliconflow" ? "硅基流动" : "百炼";
        const isServer = be === "siliconflow" ? (serverSiliconflowConfigured && !getSfKey()) : (serverDashscopeConfigured && !getKey());
        text.textContent = isServer ? "API 已就绪（服务端·" + label + "）" : "API 已配置（" + label + "）";
    } else { chip.classList.remove("ok"); text.textContent = "API 未配置"; }
}

/* ---------- API Key 弹窗 ---------- */
function openModal() {
    const be = getEffectiveBackend();
    const isSf = be === "siliconflow";
    $("apiKeyInput").value = isSf ? getSfKey() : getKey();
    $("apiKeyInput").type = "password";
    $("keyToggle").querySelector("i").className = "fa-solid fa-eye";
    $("modalKeyDesc").innerHTML = isSf
        ? '「提取文案」当前后端为<strong>硅基流动 SenseVoice</strong>，请填写 <code>SILICONFLOW_API_KEY</code>。密钥仅保存在<strong>本浏览器</strong>。'
        : '「提取文案」当前后端为<strong>阿里云百炼</strong>，请填写 <code>DASHSCOPE_API_KEY</code>。密钥仅保存在<strong>本浏览器</strong>。';
    $("modalKeyLink").href = isSf ? "https://cloud.siliconflow.cn/" : "https://help.aliyun.com/zh/model-studio/get-api-key";
    $("modalKeyLink").textContent = isSf ? "硅基流动控制台" : "阿里云百炼控制台";
    onKeyInput();
    $("modalBg").classList.add("show");
    setTimeout(() => $("apiKeyInput").focus(), 50);
}
function closeModal() { $("modalBg").classList.remove("show"); }
function onModalBgClick(e) { if (e.target === $("modalBg")) closeModal(); }
function onKeyInput() {
    const v = $("apiKeyInput").value.trim();
    const box = $("keyStatus");
    const be = getEffectiveBackend();
    const isSf = be === "siliconflow";
    const stored = isSf ? getSfKey() : getKey();
    const svrOk = isSf ? serverSiliconflowConfigured : serverDashscopeConfigured;
    if (v) {
        box.classList.add("on");
        box.querySelector(".ks-dot i").className = "fa-solid fa-circle-check";
        $("keyStatusTitle").textContent = "API Key 已填写";
        $("keyStatusSub").textContent = "保存后即可使用「提取文案」";
    } else if (svrOk) {
        box.classList.add("on");
        box.querySelector(".ks-dot i").className = "fa-solid fa-server";
        $("keyStatusTitle").textContent = "服务端已配置密钥";
        $("keyStatusSub").textContent = "无需填写也可提取文案";
    } else {
        box.classList.remove("on");
        box.querySelector(".ks-dot i").className = "fa-solid fa-circle-exclamation";
        $("keyStatusTitle").textContent = "未配置 API Key";
        $("keyStatusSub").textContent = "仅可使用「获取信息」，无法提取文案";
    }
    $("modalClear").classList.toggle("show", !!stored);
}
function toggleKeyVisible() {
    const inp = $("apiKeyInput"), i = $("keyToggle").querySelector("i");
    if (inp.type === "password") { inp.type = "text"; i.className = "fa-solid fa-eye-slash"; }
    else { inp.type = "password"; i.className = "fa-solid fa-eye"; }
}
function clearKeyInput() { $("apiKeyInput").value = ""; onKeyInput(); $("apiKeyInput").focus(); }
function saveKey() {
    const v = $("apiKeyInput").value.trim();
    const be = getEffectiveBackend();
    const store = be === "siliconflow" ? SF_KEY_STORE : KEY_STORE;
    if (v) { localStorage.setItem(store, v); toast("API Key 已保存"); }
    else { localStorage.removeItem(store); toast("已清空本地密钥"); }
    refreshChip(); closeModal();
}
function clearStoredKey() {
    const be = getEffectiveBackend();
    const store = be === "siliconflow" ? SF_KEY_STORE : KEY_STORE;
    localStorage.removeItem(store);
    $("apiKeyInput").value = ""; onKeyInput(); refreshChip(); toast("已清除已保存的密钥");
}

/* ---------- 帮助 / 灯箱 ---------- */
function openHelp() { $("helpBg").classList.add("show"); }
function closeHelp() { $("helpBg").classList.remove("show"); }
function onHelpBgClick(e) { if (e.target === $("helpBg")) closeHelp(); }
function lightboxRect() { return $("lightboxStage").getBoundingClientRect(); }
function applyLightboxTransform() {
    $("lightboxImg").style.transform = "translate(" + lightboxState.x + "px, " + lightboxState.y + "px) scale(" + lightboxState.scale + ")";
}
function clampLightboxPan() {
    const img = $("lightboxImg");
    const rect = lightboxRect();
    const w = img.naturalWidth * lightboxState.scale;
    const h = img.naturalHeight * lightboxState.scale;
    if (!w || !h) return;
    lightboxState.x = w <= rect.width ? (rect.width - w) / 2 : Math.min(0, Math.max(rect.width - w, lightboxState.x));
    lightboxState.y = h <= rect.height ? (rect.height - h) / 2 : Math.min(0, Math.max(rect.height - h, lightboxState.y));
}
function fitLightboxImage() {
    const img = $("lightboxImg");
    if (!img.naturalWidth || !img.naturalHeight) return;
    const rect = lightboxRect();
    const pad = Math.min(56, Math.max(18, rect.width * .04));
    const fit = Math.min(
        (rect.width - pad * 2) / img.naturalWidth,
        (rect.height - pad * 2) / img.naturalHeight,
        1
    );
    lightboxState.minScale = Math.max(.05, fit);
    lightboxState.maxScale = Math.max(4, fit * 8);
    lightboxState.scale = lightboxState.minScale;
    lightboxState.x = (rect.width - img.naturalWidth * lightboxState.scale) / 2;
    lightboxState.y = (rect.height - img.naturalHeight * lightboxState.scale) / 2;
    applyLightboxTransform();
}
function zoomLightboxTo(nextScale, clientX, clientY) {
    const img = $("lightboxImg");
    if (!img.naturalWidth || !img.naturalHeight) return;
    const rect = lightboxRect();
    const oldScale = lightboxState.scale;
    const newScale = Math.min(lightboxState.maxScale, Math.max(lightboxState.minScale, nextScale));
    const px = (clientX - rect.left - lightboxState.x) / oldScale;
    const py = (clientY - rect.top - lightboxState.y) / oldScale;
    lightboxState.scale = newScale;
    lightboxState.x = clientX - rect.left - px * newScale;
    lightboxState.y = clientY - rect.top - py * newScale;
    clampLightboxPan();
    applyLightboxTransform();
}
function zoomLightboxBy(factor) {
    const rect = lightboxRect();
    zoomLightboxTo(lightboxState.scale * factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
}
function sameImageSrc(a, b) {
    if (!a || !b) return false;
    try { return new URL(a, window.location.href).href === new URL(b, window.location.href).href; }
    catch (e) { return a === b; }
}
function updateLightboxBounds() {
    const img = $("lightboxImg");
    if (!img.naturalWidth || !img.naturalHeight) return;
    const rect = lightboxRect();
    const pad = Math.min(56, Math.max(18, rect.width * .04));
    const fit = Math.min(
        (rect.width - pad * 2) / img.naturalWidth,
        (rect.height - pad * 2) / img.naturalHeight,
        1
    );
    lightboxState.minScale = Math.max(.05, fit);
    lightboxState.maxScale = Math.max(4, fit * 8);
}
function swapLightboxToLoaded(src, oldNaturalWidth, oldNaturalHeight, token) {
    const img = $("lightboxImg");
    const rect = lightboxRect();
    const centerX = (rect.width / 2 - lightboxState.x) / lightboxState.scale;
    const centerY = (rect.height / 2 - lightboxState.y) / lightboxState.scale;
    const oldScale = lightboxState.scale;
    img.onload = () => {
        if (token !== lightboxState.loadId || !$("lightbox").classList.contains("show")) return;
        updateLightboxBounds();
        const ratioX = oldNaturalWidth ? img.naturalWidth / oldNaturalWidth : 1;
        const ratioY = oldNaturalHeight ? img.naturalHeight / oldNaturalHeight : 1;
        const scaleRatio = oldNaturalWidth && oldNaturalHeight
            ? Math.min(oldNaturalWidth / img.naturalWidth, oldNaturalHeight / img.naturalHeight)
            : 1;
        lightboxState.scale = Math.min(lightboxState.maxScale, Math.max(lightboxState.minScale, oldScale * scaleRatio));
        lightboxState.x = rect.width / 2 - centerX * ratioX * lightboxState.scale;
        lightboxState.y = rect.height / 2 - centerY * ratioY * lightboxState.scale;
        clampLightboxPan();
        applyLightboxTransform();
    };
    img.src = src;
    if (img.complete) requestAnimationFrame(img.onload);
}
function preloadLightboxFull(fullSrc, token) {
    const img = $("lightboxImg");
    if (!fullSrc || sameImageSrc(img.currentSrc || img.getAttribute("src"), fullSrc)) return;
    const oldNaturalWidth = img.naturalWidth;
    const oldNaturalHeight = img.naturalHeight;
    const hi = new Image();
    hi.onload = () => {
        if (token !== lightboxState.loadId || !$("lightbox").classList.contains("show")) return;
        if (sameImageSrc(img.currentSrc || img.getAttribute("src"), fullSrc)) return;
        swapLightboxToLoaded(hi.src, oldNaturalWidth, oldNaturalHeight, token);
    };
    hi.src = fullSrc;
}
function openLightbox(previewSrc, fullSrc) {
    const img = $("lightboxImg");
    const box = $("lightbox");
    const token = ++lightboxState.loadId;
    lightboxState.pointers.clear();
    lightboxState.dragStart = null;
    lightboxState.pinchStart = null;
    box.classList.add("show");
    document.body.classList.add("lb-open");
    img.onload = () => {
        if (token !== lightboxState.loadId) return;
        fitLightboxImage();
        preloadLightboxFull(fullSrc, token);
    };
    if (!sameImageSrc(img.currentSrc || img.getAttribute("src"), previewSrc)) img.src = previewSrc;
    if (img.complete) requestAnimationFrame(img.onload);
}
function closeLightbox() {
    lightboxState.loadId++;
    $("lightbox").classList.remove("show");
    $("lightboxStage").classList.remove("dragging");
    document.body.classList.remove("lb-open");
    lightboxState.pointers.clear();
    lightboxState.dragStart = null;
    lightboxState.pinchStart = null;
}

document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if ($("dlMenu").classList.contains("show")) return closeDlMenu();
    if ($("lightbox").classList.contains("show")) return closeLightbox();
    if ($("historyDrawer").classList.contains("open")) return closeHistoryDrawer();
    if ($("copyBg").classList.contains("show")) return closeCopyFallback();
    if ($("modalBg").classList.contains("show")) return closeModal();
    if ($("helpBg").classList.contains("show")) return closeHelp();
});

/* ---------- 提示条 ---------- */
function showAlert(msg, opts) {
    opts = opts || {};
    const box = $("alertBox");
    box.className = "alert show " + (opts.type === "warn" ? "warn" : "err");
    box.querySelector(".a-icon").className = "fa-solid " + (opts.type === "warn" ? "fa-triangle-exclamation" : "fa-circle-exclamation") + " a-icon";
    $("alertTitle").textContent = opts.title || (opts.type === "warn" ? "请注意" : "出错了");
    $("alertMsg").textContent = msg;
}
function clearAlert() { $("alertBox").classList.remove("show"); }

/* ---------- 输入交互 ---------- */
function onInput() { $("inputClear").classList.toggle("show", $("linkInput").value.length > 0); }
function clearInput() {
    $("linkInput").value = ""; onInput(); clearAlert();
    // 使旧的飞行中请求失效
    ++currentParseId;
    ++currentExtractId;
    if (parseAbortCtrl) { parseAbortCtrl.abort(); parseAbortCtrl = null; }
    if (extractAbortCtrl) { extractAbortCtrl.abort(); extractAbortCtrl = null; }
    // 恢复按钮状态
    setLoading("parseBtn", false, '<i class="fa-solid fa-magnifying-glass"></i> 获取信息');
    setLoading("extractBtn", false, '<i class="fa-solid fa-closed-captioning"></i><span class="label">提取文案</span><span class="need-key">需 Key</span>');
    $("mediaZone").innerHTML = "";
    $("extractZone").innerHTML = "";
    currentParseData = null;
    currentHistoryEntryId = null;
    $("linkInput").focus();
    window.scrollTo({ top: 0, behavior: "smooth" });
}
function onKeydown(e) { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); doParse(); } }
function scrollTopFocus() { window.scrollTo({ top: 0, behavior: "smooth" }); setTimeout(() => $("linkInput").focus(), 300); }
async function pasteFromClipboard() {
    try {
        const t = await navigator.clipboard.readText();
        if (t) { $("linkInput").value = t.trim(); onInput(); clearAlert(); toast("已粘贴"); }
        else toast("剪贴板为空", "err");
    } catch (e) { toast("无法读取剪贴板，请手动粘贴", "err"); $("linkInput").focus(); }
}

/* ---------- loading ---------- */
function setLoading(btnId, loading, label) {
    const btn = $(btnId);
    if (loading) {
        btn.dataset.label = btn.innerHTML;
        $("parseBtn").disabled = true; $("extractBtn").disabled = true;
        btn.innerHTML = '<span class="spinner-inline" aria-hidden="true"></span> 处理中…';
    } else {
        $("parseBtn").disabled = false; $("extractBtn").disabled = false;
        btn.innerHTML = label || btn.dataset.label || btn.innerHTML;
    }
}

/* ---------- 复制（含非安全上下文回退） ---------- */
async function copyText(text, btn) {
    let ok = false;
    try {
        if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; }
    } catch (e) { ok = false; }
    if (!ok) {
        try {
            const ta = document.createElement("textarea");
            ta.value = text; ta.setAttribute("readonly", "");
            ta.style.position = "fixed"; ta.style.top = "0"; ta.style.left = "0"; ta.style.opacity = "0";
            document.body.appendChild(ta); ta.focus(); ta.select();
            ok = document.execCommand("copy");
            document.body.removeChild(ta);
        } catch (e) { ok = false; }
    }
    if (ok) {
        if (btn) { const old = btn.innerHTML; btn.innerHTML = '<i class="fa-solid fa-check"></i> 已复制'; setTimeout(() => { btn.innerHTML = old; }, 1300); }
        toast("已复制到剪贴板");
    } else {
        showCopyFallback(text);
    }
}
function copyEl(elId, btn) { copyText($(elId).textContent, btn); }

/* ---------- 手动复制降级（非安全上下文） ---------- */
function showCopyFallback(text) {
    const ta = $("copyFallbackText");
    ta.value = text;
    $("copyBg").classList.add("show");
    setTimeout(() => { ta.focus(); ta.select(); }, 60);
}
function closeCopyFallback() { $("copyBg").classList.remove("show"); }
function onCopyBgClick(e) { if (e.target === $("copyBg")) closeCopyFallback(); }
function reselectCopyText() { const ta = $("copyFallbackText"); ta.focus(); ta.select(); }

/* ---------- 骨架屏 ---------- */
function showMediaSkeleton() {
    $("mediaZone").innerHTML =
        '<div class="card skeleton"><div class="card-body">' +
        '<div class="sk-head"><div class="sk-spinner" aria-hidden="true"></div>' +
        '<div class="sk-text"><strong>正在解析链接</strong><span>正在请求源站并提取无水印资源…</span></div></div>' +
        '<div class="sk-line" style="width:92%"></div><div class="sk-line" style="width:78%"></div><div class="sk-line" style="width:85%"></div>' +
        '</div></div>';
}

/* ---------- 信息块（标题/文案，完整常显） ---------- */
function fieldBlock(label, value, opts) {
    opts = opts || {};
    const copyBtn = opts.copyable
        ? '<button type="button" class="text-copy" data-copy="' + escapeAttr(value) + '"><i class="fa-regular fa-copy"></i> 复制</button>'
        : '';
    const valCls = "val" + (opts.title ? " title" : (opts.boxed === false ? "" : " boxed"));
    return '<div class="field"><div class="lbl"><span class="lbl-text">' + escapeHtml(label) + '</span>' +
        '<span class="spacer"></span>' + copyBtn + '</div>' +
        '<div class="' + valCls + '">' + escapeHtml(value) + '</div></div>';
}
function metaBlocks(data) {
    let html = "";
    if (data.title) html += fieldBlock("标题", data.title, { copyable: true, title: true });
    const caption = data.caption || data.desc;
    if (caption) html += fieldBlock(data.caption ? "文案" : "正文", caption, { copyable: true });
    return html ? '<div class="meta">' + html + '</div>' : "";
}

/* ---------- 渲染：媒体区 ---------- */
function renderMediaZone(data) {
    currentParseData = data;
    const platform = data.platform || "generic";
    const platLabel = PLATFORM_LABEL[platform] || platform;
    const platIcon = PLATFORM_ICON[platform] || "fa-solid fa-circle-nodes";
    const isImage = data.type === "image";
    const typeLabel = isImage ? "图文图集" : "无水印视频";
    const typeIcon = isImage ? "fa-solid fa-images" : "fa-solid fa-film";

    let tags = '<div class="tags">' +
        '<span class="tag"><i class="' + platIcon + '"></i> ' + escapeHtml(platLabel) + '</span>' +
        '<span class="tag accent"><i class="' + typeIcon + '"></i> ' + typeLabel + '</span>';
    if (isImage && data.image_count) tags += '<span class="tag neutral"><i class="fa-solid fa-layer-group"></i> ' + data.image_count + ' 张</span>';
    tags += '</div>';

    let core = "";
    if (!isImage && data.url) core = renderVideo(data);
    else if (isImage && Array.isArray(data.images) && data.images.length) core = renderGallery(data.images, data);
    else core = '<div class="field"><div class="val boxed" style="color:var(--muted)">未获取到可用的资源直链。</div></div>';

    $("mediaZone").innerHTML =
        '<div class="card"><div class="card-head">' +
        '<span class="ch-icon"><i class="' + typeIcon + '"></i></span>' +
        '<div><div class="ch-title">解析结果</div><div class="ch-sub">' + escapeHtml(platLabel) + ' · ' + typeLabel + '</div></div>' +
        '</div><div class="card-body">' + tags + core + metaBlocks(data) + '</div></div>';
}

// 视频：内嵌播放 + 直链复制 + 下载
function renderVideo(data) {
    const real = data.url;
    const name = (data.video_id || data.note_id || "video") + ".mp4";
    const playSrc = proxyUrl(real);
    const dlHref = proxyUrl(real, true, name);
    return '<div class="section-lbl"><i class="fa-solid fa-circle-play"></i> 无水印视频</div>' +
        '<div class="video-stage" id="videoStage">' +
        '<video controls preload="metadata" playsinline src="' + escapeAttr(playSrc) + '" onerror="onVideoError()"></video>' +
        '<div class="video-fail"><i class="fa-solid fa-triangle-exclamation"></i>视频暂时无法在线播放，可点击下方「下载」保存后观看。</div>' +
        '</div>' +
        '<div class="linkbox">' +
        '<input value="' + escapeAttr(real) + '" readonly aria-label="无水印视频直链">' +
        '<button type="button" class="icon-btn" title="复制直链" data-copy="' + escapeAttr(real) + '"><i class="fa-regular fa-copy"></i></button>' +
        '<a class="icon-btn primary" title="下载视频" href="' + escapeAttr(dlHref) + '"><i class="fa-solid fa-download"></i> 下载</a>' +
        '</div>';
}
function onVideoError() { const s = $("videoStage"); if (s) s.classList.add("failed"); }

// 图集
function renderGallery(images, data) {
    const base = (data.note_id || data.video_id || "image");
    galleryImages = images.map((img, i) => {
        const png = img.url_png || "";
        const webp = img.url_webp || "";
        const single = img.url || "";
        return {
            png: png, webp: webp, single: single,
            preview: webp || png || single,
            full: png || single || webp,
            name: base + "_" + (i + 1),
            multi: !!(png && webp),
        };
    });

    let html = '<div class="gallery-head">' +
        '<div class="section-lbl"><i class="fa-solid fa-images"></i> 图集 <span class="count">（' + images.length + ' 张）</span></div>' +
        '<button type="button" class="btn btn-secondary btn-sm dl-all" data-action="dlall"><i class="fa-solid fa-download"></i> 下载全部</button>' +
        '</div><div class="gallery">';

    galleryImages.forEach((im, i) => {
        let opMain;
        if (im.multi) {
            opMain = '<button type="button" class="op dl" data-dlmenu="' + i + '" title="选择下载格式"><i class="fa-solid fa-download"></i> 下载 <i class="fa-solid fa-caret-down caret"></i></button>';
        } else {
            const only = im.png || im.single || im.webp;
            const ext = im.png ? ".png" : (im.webp ? ".webp" : ".jpg");
            opMain = '<a class="op single" href="' + escapeAttr(proxyUrl(only, true, im.name + ext)) + '" title="下载图片"><i class="fa-solid fa-download"></i> 下载</a>';
        }
        const opCopy = '<button type="button" class="op copy" title="复制原图直链" data-copy="' + escapeAttr(im.full) + '"><i class="fa-regular fa-copy"></i></button>';

        const previewSrc = proxyUrl(im.preview);
        const fullSrc = proxyUrl(im.full);
        html += '<figure class="shot">' +
            '<button type="button" class="thumb" data-lightbox-preview="' + escapeAttr(previewSrc) + '" data-lightbox-full="' + escapeAttr(fullSrc) + '" aria-label="放大第 ' + (i + 1) + ' 张">' +
            '<span class="idx">' + (i + 1) + '</span>' +
            '<img src="' + escapeAttr(previewSrc) + '" loading="lazy" alt="图 ' + (i + 1) + '" data-fallback="1">' +
            '<span class="zoom"><i class="fa-solid fa-magnifying-glass-plus"></i></span>' +
            '<span class="img-fail"><i class="fa-regular fa-image"></i>预览加载失败<br>可直接下载</span>' +
            '</button>' +
            '<div class="ops">' + opMain + opCopy + '</div>' +
            '</figure>';
    });
    html += '</div>';
    return html;
}

/* ---------- 下载格式二级菜单（body 级浮层定位） ---------- */
function openDlMenu(triggerEl, idx) {
    const im = galleryImages[idx]; if (!im || !triggerEl) return;
    const menu = $("dlMenu");
    let inner = "";
    if (im.png) inner += '<a class="png" href="' + escapeAttr(proxyUrl(im.png, true, im.name + ".png")) + '" data-dlclose="1"><i class="fa-solid fa-gem"></i> PNG 高清 <span class="hint">无损</span></a>';
    if (im.webp) inner += '<a class="webp" href="' + escapeAttr(proxyUrl(im.webp, true, im.name + ".webp")) + '" data-dlclose="1"><i class="fa-solid fa-feather"></i> WebP 轻量 <span class="hint">体积小</span></a>';
    menu.innerHTML = inner;
    menu.style.visibility = "hidden";
    menu.classList.add("show");
    const r = triggerEl.getBoundingClientRect();
    const mw = menu.offsetWidth, mh = menu.offsetHeight, pad = 8;
    let left = r.left, top = r.bottom + 6;
    if (left + mw > window.innerWidth - pad) left = window.innerWidth - mw - pad;
    if (left < pad) left = pad;
    if (top + mh > window.innerHeight - pad) top = r.top - mh - 6;
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    menu.style.visibility = "visible";
}
function closeDlMenu() { $("dlMenu").classList.remove("show"); }

/* ---------- 一键下载全部 ---------- */
async function downloadAll() {
    if (!galleryImages.length) return;
    closeDlMenu();
    toast("开始下载 " + galleryImages.length + " 张图片…");
    for (let i = 0; i < galleryImages.length; i++) {
        const im = galleryImages[i];
        const url = im.png || im.single || im.webp;
        if (!url) continue;
        const ext = im.png ? ".png" : (im.single ? ".jpg" : ".webp");
        const a = document.createElement("a");
        a.href = proxyUrl(url, true, im.name + ext);
        a.download = im.name + ext;
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        await new Promise((res) => setTimeout(res, 350));
    }
    toast("已触发全部下载，请在下载目录查看");
}

/* ---------- 提取区渲染 ---------- */
function renderExtractZone(state, data) {
    const zone = $("extractZone");
    if (state === "idle") { zone.innerHTML = ""; return; }

    if (state === "loading") {
        zone.innerHTML =
            '<div class="card extract-card"><div class="card-body">' +
            '<div class="sk-head"><div class="sk-spinner" aria-hidden="true"></div>' +
            '<div class="sk-text"><strong>正在识别视频文案</strong><span>下载音频并进行语音识别，通常需要数十秒…</span></div></div>' +
            '<div class="extract-actions"><button type="button" class="btn btn-ghost btn-sm" data-action="cancel-extract">' +
            '<i class="fa-solid fa-xmark"></i> 取消</button></div>' +
            '</div></div>';
        return;
    }

    if (state === "success") {
        lastTranscript = data.text || "";
        lastTranscriptTitle = data.title || "视频文案";
        const platform = data.platform || "generic";
        const platLabel = PLATFORM_LABEL[platform] || platform;
        const count = (data.text || "").length;

        let body = '<div class="tags">' +
            '<span class="tag accent"><i class="fa-solid fa-closed-captioning"></i> 文案提取</span>' +
            '<span class="tag"><i class="' + (PLATFORM_ICON[platform] || "fa-solid fa-globe") + '"></i> ' + escapeHtml(platLabel) + '</span></div>';

        body += '<div class="field"><div class="lbl"><span class="lbl-text"><i class="fa-solid fa-quote-left" style="color:var(--brand)"></i> 识别文案</span>' +
            '<span class="spacer"></span><span class="word-count"><i class="fa-solid fa-pen-nib"></i> ' + count + ' 字</span></div>' +
            '<div class="transcript" id="transcriptText">' + escapeHtml(data.text || "（未识别到内容）") + '</div>' +
            '<div class="transcript-foot">' +
            '<button type="button" class="btn btn-secondary btn-sm" data-copy-el="transcriptText"><i class="fa-regular fa-copy"></i> 复制文案</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-action="dlmd"><i class="fa-brands fa-markdown"></i> 下载 Markdown</button>' +
            '</div></div>';

        zone.innerHTML =
            '<div class="card"><div class="card-head">' +
            '<span class="ch-icon accent"><i class="fa-solid fa-closed-captioning"></i></span>' +
            '<div><div class="ch-title">文案提取结果</div><div class="ch-sub">' + escapeHtml(platLabel) + ' · 语音识别</div></div>' +
            '</div><div class="card-body">' + body + '</div></div>';
        return;
    }

    if (state === "failure") {
        const errMsg = (data && data.error) || "提取失败";
        zone.innerHTML =
            '<div class="card extract-card"><div class="card-body">' +
            '<div class="extract-error">' +
            '<div class="extract-error-head"><i class="fa-solid fa-circle-exclamation"></i> 文案提取失败</div>' +
            '<div class="extract-error-msg">' + escapeHtml(errMsg) + '</div>' +
            '<div class="extract-actions">' +
            '<button type="button" class="btn btn-secondary btn-sm" data-action="retry-extract"><i class="fa-solid fa-rotate-right"></i> 重试</button>' +
            '</div></div></div></div>';
        return;
    }
}

function downloadTranscriptMd() {
    const md = "# " + (lastTranscriptTitle || "视频文案") + "\n\n" + lastTranscript + "\n";
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const safeName = (lastTranscriptTitle || "transcript").replace(/[\\/:*?"<>|]/g, "_").slice(0, 40) || "transcript";
    a.download = safeName + ".md";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
    toast("Markdown 已下载");
}

/* ---------- 历史记录（抽屉式） ---------- */
const HISTORY_KEY = "wanyi_history";
const HISTORY_MAX = 30;

function loadHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); }
    catch (e) { return []; }
}
function saveHistory(entries) {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, HISTORY_MAX)));
}
function addHistoryEntry(data, inputUrl) {
    const entry = {
        id: Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8),
        platform: data.platform || "generic",
        type: data.type || "video",
        title: data.title || "",
        caption: data.caption || data.desc || "",
        url: data.url || "",
        images: data.images || [],
        image_count: data.image_count || 0,
        inputUrl: inputUrl,
        timestamp: Date.now(),
        transcript: null,
    };
    const hist = loadHistory();
    hist.unshift(entry);
    saveHistory(hist);
    currentHistoryEntryId = entry.id;
    refreshHistoryBadge();
    if ($("historyDrawer").classList.contains("open")) renderHistoryDrawer();
    return entry.id;
}
function updateHistoryTranscript(entryId, text) {
    if (!entryId) return;
    const hist = loadHistory();
    const item = hist.find(function (h) { return h.id === entryId; });
    if (item) {
        item.transcript = text;
        saveHistory(hist);
        if ($("historyDrawer").classList.contains("open")) renderHistoryDrawer();
    }
}
function formatRelTime(ts) {
    const diff = Date.now() - ts;
    if (diff < 60000) return "刚刚";
    if (diff < 3600000) return Math.floor(diff / 60000) + " 分钟前";
    if (diff < 86400000) return Math.floor(diff / 3600000) + " 小时前";
    if (diff < 604800000) return Math.floor(diff / 86400000) + " 天前";
    const d = new Date(ts);
    return (d.getMonth() + 1) + "/" + d.getDate();
}
function refreshHistoryBadge() {
    const count = loadHistory().length;
    const badge = $("historyBadge");
    if (count > 0) {
        badge.textContent = count > 99 ? "99+" : count;
        badge.classList.add("show");
    } else {
        badge.classList.remove("show");
    }
}
function renderHistoryDrawer() {
    const hist = loadHistory();
    const list = $("historyList");
    const foot = $("historyFoot");

    if (!hist.length) {
        list.innerHTML = '<div class="drawer-empty"><i class="fa-solid fa-inbox"></i><p>暂无解析历史</p></div>';
        foot.classList.remove("show");
        foot.innerHTML = "";
        return;
    }

    let items = "";
    hist.forEach(function (h) {
        const platLabel = PLATFORM_LABEL[h.platform] || h.platform;
        const typeIcon = h.type === "image" ? "fa-solid fa-images" : "fa-solid fa-film";
        const isImg = h.type === "image";
        const title = h.title || h.caption || h.inputUrl || "无标题";
        const transcriptBadge = h.transcript
            ? '<span class="hi-transcript-badge">文案</span>'
            : '';
        items += '<div class="history-item" data-action="restore-history" data-history-id="' + escapeAttr(h.id) + '">' +
            '<span class="hi-type-icon' + (isImg ? " img" : "") + '"><i class="' + typeIcon + '"></i></span>' +
            '<div class="hi-body">' +
            '<div class="hi-title">' + escapeHtml(shortPreview(title, 50)) + '</div>' +
            '<div class="hi-meta"><span class="hi-platform">' + escapeHtml(platLabel) + '</span>' +
            '<span>' + formatRelTime(h.timestamp) + '</span>' +
            transcriptBadge + '</div></div>' +
            '<button type="button" class="hi-remove" data-action="remove-history" data-history-id="' + escapeAttr(h.id) + '" title="删除"><i class="fa-solid fa-xmark"></i></button>' +
            '</div>';
    });
    list.innerHTML = items;

    foot.classList.add("show");
    foot.innerHTML = '<button type="button" class="btn btn-ghost btn-sm" data-action="clear-history" style="width:100%"><i class="fa-solid fa-trash-can"></i> 清空所有记录</button>';
}
function openHistoryDrawer() {
    renderHistoryDrawer();
    $("historyDrawer").classList.add("open");
    $("drawerBackdrop").classList.add("show");
    document.body.style.overflow = "hidden";
}
function closeHistoryDrawer() {
    $("historyDrawer").classList.remove("open");
    $("drawerBackdrop").classList.remove("show");
    document.body.style.overflow = "";
}
function toggleHistoryDrawer() {
    if ($("historyDrawer").classList.contains("open")) closeHistoryDrawer();
    else openHistoryDrawer();
}
function restoreHistory(entryId) {
    const hist = loadHistory();
    const entry = hist.find(function (h) { return h.id === entryId; });
    if (!entry) { toast("记录不存在", "err"); return; }

    // 使旧的飞行中请求失效
    ++currentParseId;
    ++currentExtractId;
    if (parseAbortCtrl) { parseAbortCtrl.abort(); parseAbortCtrl = null; }
    if (extractAbortCtrl) { extractAbortCtrl.abort(); extractAbortCtrl = null; }
    setLoading("parseBtn", false, '<i class="fa-solid fa-magnifying-glass"></i> 获取信息');
    setLoading("extractBtn", false, '<i class="fa-solid fa-closed-captioning"></i><span class="label">提取文案</span><span class="need-key">需 Key</span>');

    $("linkInput").value = entry.inputUrl || "";
    onInput();
    currentHistoryEntryId = entry.id;

    renderMediaZone(entry);

    if (entry.transcript) {
        renderExtractZone("success", { text: entry.transcript, title: entry.title, platform: entry.platform, caption: entry.caption });
    } else {
        renderExtractZone("idle");
    }

    closeHistoryDrawer();
    $("mediaZone").scrollIntoView({ behavior: "smooth", block: "start" });
    toast("已恢复历史记录");
}
function removeHistoryItem(entryId) {
    const hist = loadHistory();
    const idx = hist.findIndex(function (h) { return h.id === entryId; });
    if (idx === -1) return;
    hist.splice(idx, 1);
    saveHistory(hist);
    renderHistoryDrawer();
    refreshHistoryBadge();
    toast("已删除");
}
function clearHistory() {
    if (!confirm("确定要清空所有解析历史吗？此操作不可撤销。")) return;
    localStorage.removeItem(HISTORY_KEY);
    renderHistoryDrawer();
    refreshHistoryBadge();
    toast("历史已清空");
}

/* ---------- 请求 ---------- */
async function doParse() {
    const url = $("linkInput").value.trim();
    clearAlert();
    if (!url) { showAlert("请先粘贴分享链接再试。", { type: "warn", title: "还没有链接" }); $("linkInput").focus(); return; }

    const reqId = ++currentParseId;
    if (parseAbortCtrl) parseAbortCtrl.abort();
    parseAbortCtrl = new AbortController();

    const flow = createParseFlowLogger(url);
    let finalStatus = "未完成";
    let finalExtra = {};
    setLoading("parseBtn", true);
    flow.mark("按钮进入加载状态");
    showMediaSkeleton();
    $("extractZone").innerHTML = "";
    flow.mark("已展示解析占位内容");
    try {
        const body = JSON.stringify({ url });
        flow.mark("准备发送后端解析请求", { "接口": "POST /api/parse", "请求体长度": body.length });
        const requestStarted = performance.now();
        const r = await fetch("/api/parse", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Parse-Trace-Id": flow.traceId },
            body,
            signal: parseAbortCtrl.signal,
        });
        if (reqId !== currentParseId) { flow.finish("已废弃（新请求覆盖）"); return; }
        flow.mark("收到后端 HTTP 响应", {
            "HTTP状态": r.status,
            "响应OK": r.ok,
            "网络等待": fmtDuration(performance.now() - requestStarted),
        });
        const jsonStarted = performance.now();
        const data = await r.json();
        if (reqId !== currentParseId) { flow.finish("已废弃（新请求覆盖）"); return; }
        flow.mark("后端 JSON 读取完成", {
            "JSON读取": fmtDuration(performance.now() - jsonStarted),
            "后端状态": data.status || "无",
            "平台": data.platform || "无",
            "类型": data.type || "无",
            "资源": resultResourceSummary(data),
        });
        if (!r.ok || data.status === "error") {
            $("mediaZone").innerHTML = "";
            const msg = data.error || ("解析失败（HTTP " + r.status + "）");
            flow.mark("解析失败，准备展示错误提示", { "错误": msg });
            showAlert(msg, { title: "解析失败" });
            finalStatus = "失败";
            finalExtra = { "错误": msg };
        } else {
            const renderStarted = performance.now();
            renderMediaZone(data);
            addHistoryEntry(data, url);
            flow.mark("结果 DOM 渲染完成", {
                "渲染耗时": fmtDuration(performance.now() - renderStarted),
                "资源": resultResourceSummary(data),
            });
            finalStatus = "成功";
            finalExtra = { "平台": data.platform || "无", "类型": data.type || "无", "资源": resultResourceSummary(data) };
        }
    } catch (e) {
        if (e.name === "AbortError") { flow.finish("已取消"); return; }
        if (reqId !== currentParseId) { flow.finish("已废弃"); return; }
        $("mediaZone").innerHTML = "";
        showAlert("网络请求失败：" + e.message, { title: "请求出错" });
        finalStatus = "异常";
        finalExtra = { "错误": e.message };
    }
    finally {
        if (reqId === currentParseId) {
            setLoading("parseBtn", false, '<i class="fa-solid fa-magnifying-glass"></i> 获取信息');
        }
        flow.mark("按钮状态恢复完成");
        flow.finish(finalStatus, finalExtra);
    }
}

function cancelExtract() {
    if (extractAbortCtrl) extractAbortCtrl.abort();
    currentExtractId++;
    renderExtractZone("idle");
    setLoading("extractBtn", false, '<i class="fa-solid fa-closed-captioning"></i><span class="label">提取文案</span><span class="need-key">需 Key</span>');
    toast("已取消提取");
}

function retryExtract() {
    doExtract();
}

async function doExtract() {
    const url = $("linkInput").value.trim();
    clearAlert();
    if (!url) { showAlert("请先粘贴分享链接再试。", { type: "warn", title: "还没有链接" }); $("linkInput").focus(); return; }
    if (!hasKey()) {
        const be = getEffectiveBackend();
        const label = be === "siliconflow" ? "硅基流动 API Key" : "阿里云百炼 API Key";
        showAlert("「提取文案」需要先配置" + label + "，已为你打开配置窗口。", { type: "warn", title: "需要 API Key" }); openModal(); return;
    }

    const reqId = ++currentExtractId;
    if (extractAbortCtrl) extractAbortCtrl.abort();
    extractAbortCtrl = new AbortController();

    setLoading("extractBtn", true);
    renderExtractZone("loading");
    try {
        const be = getEffectiveBackend();
        const key = be === "siliconflow" ? getSfKey() : getKey();
        const payload = { url, api_key: key, backend: be };
        const r = await fetch("/api/extract", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: extractAbortCtrl.signal,
        });
        if (reqId !== currentExtractId) return;
        const data = await r.json();
        if (reqId !== currentExtractId) return;
        if (!r.ok || data.status === "error") {
            renderExtractZone("failure", { error: data.error || ("提取失败（HTTP " + r.status + "）") });
        } else {
            renderExtractZone("success", data);
            updateHistoryTranscript(currentHistoryEntryId, data.text || "");
        }
    } catch (e) {
        if (e.name === "AbortError") return;
        if (reqId !== currentExtractId) return;
        renderExtractZone("failure", { error: "网络请求失败：" + e.message });
    }
    finally {
        if (reqId === currentExtractId) {
            setLoading("extractBtn", false, '<i class="fa-solid fa-closed-captioning"></i><span class="label">提取文案</span><span class="need-key">需 Key</span>');
        }
    }
}

/* ---------- 回到顶部显隐 ---------- */
function onScroll() {
    closeDlMenu();
    const show = window.scrollY > 320 && $("mediaZone").innerHTML.trim() !== "";
    $("backTop").classList.toggle("show", show);
}

/* ---------- 初始化 ---------- */
async function initHealth() {
    try {
        const r = await fetch("/api/health"); const d = await r.json();
        serverKeyConfigured = !!d.api_key_configured;
        serverBackendDefault = d.asr_backend_default || "dashscope";
        serverDashscopeConfigured = !!d.dashscope_api_key_configured;
        serverSiliconflowConfigured = !!d.siliconflow_api_key_configured;
    } catch (e) {}
    refreshChip();
}

Object.assign(window, {
    openModal, closeModal, onModalBgClick, onKeyInput, toggleKeyVisible, clearKeyInput, saveKey, clearStoredKey,
    openHelp, closeHelp, onHelpBgClick, openLightbox, closeLightbox,
    closeCopyFallback, onCopyBgClick, reselectCopyText,
    openDlMenu, closeDlMenu, downloadAll,
    clearAlert, onInput, clearInput, onKeydown, pasteFromClipboard, scrollTopFocus,
    copyText, copyEl, downloadTranscriptMd, onVideoError, doParse, doExtract,
    cancelExtract, retryExtract, restoreHistory, removeHistoryItem, clearHistory,
    toggleHistoryDrawer, openHistoryDrawer, closeHistoryDrawer,
});

// 结果区事件委托
$("resultBox").addEventListener("click", (e) => {
    const copyBtn = e.target.closest("[data-copy]");
    if (copyBtn) { copyText(copyBtn.getAttribute("data-copy"), copyBtn); return; }
    const copyElBtn = e.target.closest("[data-copy-el]");
    if (copyElBtn) { copyEl(copyElBtn.getAttribute("data-copy-el"), copyElBtn); return; }
    const lbBtn = e.target.closest("[data-lightbox-preview]");
    if (lbBtn) {
        const img = lbBtn.querySelector("img");
        const previewSrc = (img && img.currentSrc) || lbBtn.getAttribute("data-lightbox-preview") || lbBtn.getAttribute("data-lightbox-full");
        openLightbox(previewSrc, lbBtn.getAttribute("data-lightbox-full") || previewSrc);
        return;
    }
    const dlBtn = e.target.closest("[data-dlmenu]");
    if (dlBtn) { e.stopPropagation(); openDlMenu(dlBtn, parseInt(dlBtn.getAttribute("data-dlmenu"), 10)); return; }
    const actBtn = e.target.closest("[data-action]");
    if (actBtn) {
        const act = actBtn.getAttribute("data-action");
        if (act === "dlall") downloadAll();
        else if (act === "dlmd") downloadTranscriptMd();
        else if (act === "cancel-extract") cancelExtract();
        else if (act === "retry-extract") retryExtract();
    }
});
$("resultBox").addEventListener("error", (e) => {
    const img = e.target;
    if (img && img.tagName === "IMG" && img.hasAttribute("data-fallback")) {
        const shot = img.closest(".shot");
        if (shot) shot.classList.add("broken");
    }
}, true);

$("lightboxStage").addEventListener("wheel", (e) => {
    if (!$("lightbox").classList.contains("show")) return;
    e.preventDefault();
    zoomLightboxTo(lightboxState.scale * (e.deltaY > 0 ? 0.88 : 1.14), e.clientX, e.clientY);
}, { passive: false });
$("lightboxStage").addEventListener("pointerdown", (e) => {
    if (!$("lightbox").classList.contains("show")) return;
    $("lightboxStage").setPointerCapture(e.pointerId);
    lightboxState.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (lightboxState.pointers.size === 1) {
        lightboxState.dragStart = { px: e.clientX, py: e.clientY, x: lightboxState.x, y: lightboxState.y };
        $("lightboxStage").classList.add("dragging");
    } else if (lightboxState.pointers.size === 2) {
        const pts = Array.from(lightboxState.pointers.values());
        const dx = pts[0].x - pts[1].x, dy = pts[0].y - pts[1].y;
        lightboxState.pinchStart = { distance: Math.hypot(dx, dy), scale: lightboxState.scale };
    }
});
$("lightboxStage").addEventListener("pointermove", (e) => {
    if (!lightboxState.pointers.has(e.pointerId)) return;
    lightboxState.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (lightboxState.pointers.size === 2 && lightboxState.pinchStart) {
        const pts = Array.from(lightboxState.pointers.values());
        const dx = pts[0].x - pts[1].x, dy = pts[0].y - pts[1].y;
        const cx = (pts[0].x + pts[1].x) / 2, cy = (pts[0].y + pts[1].y) / 2;
        zoomLightboxTo(lightboxState.pinchStart.scale * (Math.hypot(dx, dy) / lightboxState.pinchStart.distance), cx, cy);
        return;
    }
    if (lightboxState.dragStart) {
        lightboxState.x = lightboxState.dragStart.x + e.clientX - lightboxState.dragStart.px;
        lightboxState.y = lightboxState.dragStart.y + e.clientY - lightboxState.dragStart.py;
        clampLightboxPan();
        applyLightboxTransform();
    }
});
function endLightboxPointer(e) {
    lightboxState.pointers.delete(e.pointerId);
    if (lightboxState.pointers.size === 0) {
        lightboxState.dragStart = null;
        lightboxState.pinchStart = null;
        $("lightboxStage").classList.remove("dragging");
    } else if (lightboxState.pointers.size === 1) {
        const pt = Array.from(lightboxState.pointers.values())[0];
        lightboxState.dragStart = { px: pt.x, py: pt.y, x: lightboxState.x, y: lightboxState.y };
        lightboxState.pinchStart = null;
    }
}
$("lightboxStage").addEventListener("pointerup", endLightboxPointer);
$("lightboxStage").addEventListener("pointercancel", endLightboxPointer);
$("lbZoomOut").addEventListener("click", () => zoomLightboxBy(.82));
$("lbZoomReset").addEventListener("click", fitLightboxImage);
$("lbZoomIn").addEventListener("click", () => zoomLightboxBy(1.22));
$("dlMenu").addEventListener("click", (e) => {
    if (e.target.closest("[data-dlclose]")) closeDlMenu();
});

window.addEventListener("scroll", onScroll, { passive: true });
window.addEventListener("resize", () => {
    closeDlMenu();
    if ($("lightbox").classList.contains("show")) fitLightboxImage();
});
document.addEventListener("click", (e) => {
    if (!e.target.closest(".dl-menu") && !e.target.closest(".op.dl")) closeDlMenu();
});
initHealth();
refreshHistoryBadge();

// 历史抽屉事件委托
$("historyDrawer").addEventListener("click", (e) => {
    const actBtn = e.target.closest("[data-action]");
    if (!actBtn) return;
    const act = actBtn.getAttribute("data-action");
    if (act === "restore-history") restoreHistory(actBtn.getAttribute("data-history-id"));
    else if (act === "remove-history") { e.stopPropagation(); removeHistoryItem(actBtn.getAttribute("data-history-id")); }
    else if (act === "clear-history") clearHistory();
});
})();
