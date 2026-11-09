import { el } from "./core.js";

/* behaviors 助手：面板的定位 / 焦点圈定 / 键盘都依赖它。
 * 注意：早前把 panel.js 从 controls.js 拆出时这段丢失，openPanel 一调用就抛
 * "behaviors is not defined"，表现为"点筛选按钮毫无反应"。 */
const BEH = "/vendor/primer-behaviors/dist/esm/index.mjs";
let _beh = null;
async function behaviors() {
  if (!_beh) _beh = await import(BEH);
  return _beh;
}

/* ui/panel.js —— 浮层面板（唯一实现）：定位/焦点/键盘交给 @primer/behaviors
 * 规格 §5.8 / §5.14.7 / §6.1–6.3 */
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
