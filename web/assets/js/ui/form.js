/* ui/form.js —— 表单（唯一实现）：label 在上、控件满宽、必填校验、错误定位
 * 规格 §5.10 / §5.14.8；页面通过 controls.renderForm 或直接 import 使用。 */

import { el } from "./core.js";

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
