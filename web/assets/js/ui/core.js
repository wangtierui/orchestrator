/* ui/core.js —— 基础工具与三态组件（唯一实现，13 页共用）
 *
 * 纪律（见 docs/spec-github-replica.md §10）：
 *   · 视觉一律走 Primer 类与令牌，本文件只做布局与语义；
 *   · 前端不加工数据（不求和、不排序、不筛选），只渲染后端给的结果；
 *   · 空/错误/加载三态必须带可访问角色（role=status/alert + aria-live）。
 */

/** 取值令牌（调用方需要把 CSS 变量传给 ECharts 之类的 API 时用）。 */
export function token(name, fallback = "") {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** HTML 转义。所有插值必须过这里，避免 XSS。 */
export function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/** 创建元素：el("div", {class:"p-2", role:"status"}, [child]) */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.append(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

/** 千分位（纯展示，不是数据加工）。 */
export function fmtInt(n) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toLocaleString("zh-CN") : "—";
}

/** 防抖：只影响「何时发请求」，不影响「怎么算」。 */
export function debounce(fn, ms = 220) {
  let t = 0;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/** 三态：loading / empty / error。role 与 aria-live 由语义决定，不随手写。 */
export function renderState(kind, cfg = {}) {
  const isError = kind === "error";
  const box = el("div", {
    class: "p-4 text-center",
    role: isError ? "alert" : "status",
    "aria-live": isError ? "assertive" : "polite",
  });
  const title = el("div", { class: "text-bold mb-1" });
  title.textContent = cfg.title || (kind === "loading" ? "加载中…" : kind === "error" ? "加载失败" : "暂无数据");
  box.append(title);
  if (cfg.desc) {
    const d = el("div", { class: "text-small" });
    d.textContent = cfg.desc;
    box.append(d);
  }
  if (cfg.action) {
    const btn = el("button", { class: "btn mt-2", type: "button" });
    btn.textContent = cfg.action.label;
    btn.addEventListener("click", cfg.action.onClick);
    box.append(btn);
  }
  return box;
}

/** 骨架行：保持容器高度，避免加载时页面跳动。 */
export function renderSkeleton(rows = 5) {
  const wrap = el("div", { class: "p-2", role: "status", "aria-live": "polite", "aria-label": "加载中" });
  for (let i = 0; i < rows; i++) {
    wrap.append(el("div", { class: "border-bottom", style: "height:var(--base-size-32)" }));
  }
  return wrap;
}

/** 渲染完成标记：smoke 等它再断言（把时序变成确定性）。 */
export function markReady() {
  if (document.body) document.body.dataset.pageReady = "1";
  else addEventListener("DOMContentLoaded", () => { document.body.dataset.pageReady = "1"; }, { once: true });
}

/** 轮询等待条件成立（用于等待容器出现后再标记 ready）。 */
export function when(selector, timeoutMs = 8000) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      if (document.querySelector(selector)) return resolve(true);
      if (Date.now() - t0 > timeoutMs) return resolve(false);
      setTimeout(tick, 80);
    };
    tick();
  });
}
