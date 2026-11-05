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

/* ── 表单：label 在上、控件满宽、必填校验、错误定位 ── */
export function renderForm(cfg = {}) {
  const { fields = [], actions = [], onSubmit } = cfg;
  const form = el("form", { class: "d-flex flex-column", style: "gap:var(--space-lg);", novalidate: "true" });
  const controls = {};

  for (const f of fields) {
    const id = `f-${f.key}`;
    const wrap = el("div", { class: "d-flex flex-column", style: "gap:var(--space-xs);" });
    const lab = el("label", { for: id, class: "text-bold text-small" });
    lab.textContent = f.label + (f.required ? " *" : "");
    let ctl;
    if (f.type === "textarea") {
      ctl = el("textarea", { id, class: "form-control", rows: "4", style: "min-height:100px;",
        "aria-required": String(!!f.required), "aria-describedby": `e-${f.key}` });
    } else if (f.type === "select") {
      ctl = el("select", { id, class: "form-select", "aria-required": String(!!f.required) });
      for (const o of f.options || []) {
        const opt = el("option", { value: o.value });
        opt.textContent = o.label;
        ctl.append(opt);
      }
    } else {
      ctl = el("input", { id, class: "form-control", type: f.type || "text",
        "aria-required": String(!!f.required), "aria-describedby": `e-${f.key}` });
    }
    if (f.value != null) ctl.value = f.value;
    const err = el("div", { id: `e-${f.key}`, class: "text-small", style: "color:var(--fgColor-danger);", hidden: "hidden" });
    wrap.append(lab, ctl, err);
    controls[f.key] = { ctl, err, field: f };
    form.append(wrap);
  }

  const bar = el("div", { class: "d-flex", style: "gap:var(--space-sm);justify-content:flex-end;" });
  for (const a of actions) {
    const b = el("button", { class: a.variant === "primary" ? "btn btn-primary" : "btn", type: a.type || "button" });
    b.textContent = a.label;
    b.addEventListener("click", async () => {
      if (a.key === "submit") {
        const values = {};
        let firstBad = null;
        for (const [k, c] of Object.entries(controls)) {
          c.err.hidden = true;
          c.ctl.removeAttribute("aria-invalid");
          values[k] = c.ctl.value;
          if (c.field.required && !String(c.ctl.value).trim()) {
            c.err.textContent = `${c.field.label} 必填`;
            c.err.hidden = false;
            c.ctl.setAttribute("aria-invalid", "true");
            firstBad = firstBad || c.ctl;
          }
        }
        if (firstBad) { firstBad.focus(); return; }
        onSubmit && (await onSubmit(values));
      } else if (a.onClick) a.onClick();
    });
    bar.append(b);
  }
  form.append(bar);
  return { el: form, values: () => Object.fromEntries(Object.entries(controls).map(([k, c]) => [k, c.ctl.value])) };
}

/* ── 浮层面板：定位 + 焦点管理全部交给 behaviors ── */
export async function openPanel(cfg = {}) {
  const { anchor, items = [], multi = false, searchable = false,
    label = "选项", onPick, onClose } = cfg;
  const { getAnchoredPosition, focusZone, focusTrap, FocusKeys } = await behaviors();

  const list = el("ul", { role: "listbox", "aria-label": label, class: "m-0 p-0",
    style: "list-style:none;max-height:264px;overflow:auto;" });
  for (const it of items) {
    const li = el("li", {
      role: "option", tabindex: "-1", "aria-selected": String(!!it.checked),
      class: "d-flex flex-items-center", style: "height:32px;padding:6px 8px;gap:var(--space-xs);cursor:pointer;",
    });
    if (multi) li.append(el("input", { type: "checkbox", checked: it.checked ? "checked" : null,
      "aria-label": it.label, tabindex: "-1" }));
    li.append(el("span", { class: "flex-1", text: it.label }));
    if (it.count != null) {
      const c = el("span", { class: "Counter", "aria-label": `${it.count} 项` });
      c.textContent = String(it.count);
      li.append(c);
    }
    li.addEventListener("click", () => { onPick && onPick(it, li); if (!multi) close(); });
    list.append(li);
  }

  const panel = el("div", {
    role: "dialog", "aria-modal": "false", "aria-label": label, id: `panel-${Date.now()}`,
    class: "bgColor-default", style:
      "position:absolute;width:320px;border-radius:12px;visibility:hidden;" +
      "box-shadow:0 0 0 1px rgba(209,217,224,.25),0 8px 24px rgba(66,74,83,.12);padding:var(--space-sm);",
  }, [searchable ? el("input", { class: "form-control mb-1", type: "search", placeholder: "筛选…",
    "aria-label": "在选项中筛选" }) : null, list]);

  document.body.append(panel);
  const pos = getAnchoredPosition(panel, anchor, { side: "outside-bottom", align: "start", allowOutOfBounds: false });
  panel.style.top = `${pos.top + window.scrollY}px`;
  panel.style.left = `${pos.left + window.scrollX}px`;
  panel.style.visibility = "visible";

  anchor.setAttribute("aria-expanded", "true");
  anchor.setAttribute("aria-controls", panel.id);

  const trap = focusTrap(panel, list.querySelector('[role="option"]'));
  const zone = focusZone(panel, { bindKeys: FocusKeys.ArrowVertical | FocusKeys.HomeAndEnd, focusOutBehavior: "stop" });

  function close() {
    trap && trap.abort && trap.abort();
    zone && zone.abort && zone.abort();
    anchor.setAttribute("aria-expanded", "false");
    panel.remove();
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("click", onDoc, true);
    anchor.focus();
    onClose && onClose();
  }
  function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(); } }
  function onDoc(e) { if (!panel.contains(e.target) && e.target !== anchor) close(); }
  document.addEventListener("keydown", onKey, true);
  setTimeout(() => document.addEventListener("click", onDoc, true), 0);

  return { close, el: panel };
}
