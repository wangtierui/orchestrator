/* ui/controls.js —— 搜索行 / 工具条 / 分页 / 表单 / 浮层面板（唯一实现）
 *
 * 对应规格：§5.4 搜索、§5.5 工具条、§5.7 分页、§5.8 面板、§5.10 表单
 * 交互原语一律用 @primer/behaviors（定位/焦点/键盘），本文件不重复实现。
 */

import { el, esc, debounce } from "./core.js";

const BEH = "/vendor/primer-behaviors/dist/esm/index.mjs";
let beh = null;
async function behaviors() {
  if (!beh) beh = await import(BEH);
  return beh;
}

/* ── 搜索行：外层容器承载边框与高度（input 自身裸，规格 §5.4） ── */
export function renderSearch(cfg = {}) {
  const { value = "", placeholder = "搜索…", label = "搜索", debounceMs = 220,
    onInput, onSubmit } = cfg;

  const input = el("input", {
    type: "search", class: "form-control", id: cfg.id || "q",
    "aria-label": label, placeholder, value,
    style: "border:0;padding:0;background:transparent;outline:none;",
  });
  const box = el("div", {
    class: "border rounded-2 d-flex flex-items-center",
    role: "search", style: "height:30px;padding-inline:var(--space-sm);gap:var(--space-xs);",
  }, [input, el("span", { class: "text-small", "aria-hidden": "true", text: "🔍" })]);

  const fire = debounce((v) => onInput && onInput(v), debounceMs);
  input.addEventListener("input", (e) => fire(e.target.value));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); onSubmit && onSubmit(input.value); }
    if (e.key === "Escape") { input.value = ""; onInput && onInput(""); }
  });
  return { el: box, input, value: () => input.value, setValue: (v) => { input.value = v; } };
}

/* ── 工具条：左状态开关 / 右筛选 + 密度（左右分组用 flex，不裁切） ── */
export function renderToolbar(cfg = {}) {
  const { segments = [], active = "", filters = [], density = "comfortable",
    onSegment, onFilter, onDensity } = cfg;

  const left = el("div", { class: "d-flex flex-items-center", role: "group", "aria-label": "列表状态",
    style: "gap:var(--space-xs);" });
  for (const s of segments) {
    const pressed = s.key === active;
    const b = el("button", { class: "btn btn-sm", type: "button", "aria-pressed": String(pressed) });
    b.append(el("span", { text: s.label }));
    if (s.count != null) {
      const c = el("span", { class: "Counter ml-1", "aria-label": `${s.count} 项` });
      c.textContent = String(s.count);
      b.append(c);
    }
    if (pressed) b.classList.add("btn-primary");
    b.addEventListener("click", () => onSegment && onSegment(s.key));
    left.append(b);
  }

  const right = el("div", { class: "d-flex flex-items-center", style: "gap:var(--space-xs);" });
  for (const f of filters) {
    const b = el("button", { class: "btn btn-sm", type: "button",
      "aria-haspopup": "dialog", "aria-expanded": "false", "data-filter": f.key });
    b.textContent = f.label;
    b.addEventListener("click", () => onFilter && onFilter(f, b));
    right.append(b);
  }
  const dens = el("div", { role: "group", "aria-label": "列表密度",
    class: "d-flex", style: "gap:var(--space-xs);" });
  for (const d of [{ k: "comfortable", t: "≡" }, { k: "compact", t: "☰" }]) {
    const b = el("button", { class: "btn btn-sm", type: "button",
      "aria-pressed": String(density === d.k), "aria-label": d.k === "comfortable" ? "舒适密度" : "紧凑密度",
      "aria-controls": "rco-list" });
    b.textContent = d.t;
    b.addEventListener("click", () => onDensity && onDensity(d.k));
    dens.append(b);
  }
  right.append(dens);

  return el("div", { class: "d-flex flex-wrap flex-items-center", style: "gap:var(--space-sm);" }, [
    left, el("span", { class: "flex-1" }), right,
  ]);
}

/* ── 分页：表格容器之外 ── */
export function renderPagination(cfg = {}) {
  const { page = 1, pages = 1, onChange, total = null, size = null } = cfg;
  const prev = el("button", { class: "btn btn-sm", type: "button", disabled: page <= 1 });
  prev.textContent = "上一页";
  const next = el("button", { class: "btn btn-sm", type: "button", disabled: page >= pages });
  next.textContent = "下一页";
  const info = el("span", { class: "text-small", "aria-live": "polite" });
  info.textContent = `${page} / ${pages}${total != null ? ` · 共 ${total} 条` : ""}${size ? ` · 每页 ${size}` : ""}`;
  prev.addEventListener("click", () => onChange && onChange(page - 1));
  next.addEventListener("click", () => onChange && onChange(page + 1));
  return el("nav", { class: "d-flex flex-items-center", "aria-label": "分页",
    style: "gap:var(--space-sm);justify-content:flex-end;padding-block:var(--space-lg);" },
  [prev, info, next]);
}

/* 面板与表单已拆到独立模块（P0）；此处保留同名导出，页面无需改动。 */
export { renderForm } from "./form.js";
export { openPanel } from "./panel.js";
