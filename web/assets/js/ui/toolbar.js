/* ui/toolbar.js —— 工具条（唯一实现）：状态开关 + 筛选按钮 + 密度
 * 规格 §5.5 / §5.12 / §5.14.4 / §5.14.11 */

import { el } from "../ui/core.js";

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
