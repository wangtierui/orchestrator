/* ui/table.js —— 表格（唯一实现）
 *
 * 规格：docs/spec-github-replica.md §5.6 / §5.14.5 / §5.12
 *   · 外框无边框；表头 48 高、底色 --bgColor-muted、下边 1px --borderColor-muted
 *   · 行高严格按密度：comfortable 65 / compact 38（全站统一，无 32 例外）
 *   · 排序列头显示箭头并写 aria-sort；排序是**回调**（真正的排序在后端）
 *   · 勾选支持三态表头；aria-selected 同步
 */

import { el, esc, getHashParam, setHashParam } from "./core.js";

const STYLE_ID = "rco-table-style";

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const css = `
  .rco-tbl { display: flex; flex-direction: column; }
  .rco-tbl__head, .rco-tbl__row {
    display: grid; align-items: center; column-gap: var(--space-sm);
    padding-inline: var(--space-sm);
  }
  .rco-tbl__head {
    min-height: 48px; background: var(--bgColor-muted);
    border-bottom: 1px solid var(--borderColor-muted);
    font-size: var(--text-body-size-small); font-weight: 600;
  }
  .rco-tbl__row { border-bottom: 1px solid var(--borderColor-muted); font-size: var(--text-body-size-medium); }
  .rco-tbl__row:last-child { border-bottom: 0; }
  .rco-tbl[data-density="comfortable"] .rco-tbl__row { min-height: 65px; }
  .rco-tbl[data-density="compact"] .rco-tbl__row { min-height: 38px; }
  .rco-tbl__cell { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rco-tbl__cell--wrap { white-space: normal; }
  .rco-tbl__head button[data-sort] { background: none; border: 0; padding: 0;
    font: inherit; color: inherit; cursor: pointer; display: inline-flex; gap: var(--space-xxs); }
  .rco-tbl__head button[data-sort][aria-disabled="true"] { cursor: default; }
  `;
  document.head.append(el("style", { id: STYLE_ID, text: css }));
}

/** 标签徽标：返回 DOM 节点，供列定义 render 使用（字符串会被转义成纯文本）。 */
export function badge(text, tone = "") {
  const span = el("span", { class: "IssueLabel" + (tone ? ` ${tone}` : "") });
  span.textContent = text == null || text === "" ? "—" : String(text);
  return span;
}

function colsTemplate(columns, selectable) {
  const parts = [];
  if (selectable) parts.push("var(--base-size-32)");
  for (const c of columns) parts.push(c.width || "minmax(0, 1fr)");
  return parts.join(" ");
}

/**
 * @param {object} cfg
 * @param {Array} cfg.columns  [{key,label,width,sortable,align,render(row)}]
 * @param {Array} cfg.rows
 * @param {string} cfg.density 'comfortable'|'compact'
 * @param {boolean} cfg.selectable
 * @param {Function} cfg.onSort (key, dir) —— 后端排序入口
 * @param {Function} cfg.onRow (row, index)
 * @param {Function} cfg.onSelect (rows[])
 * @param {object} cfg.empty {title,desc}
 */
export function renderTable(cfg) {
  ensureStyle();
  const {
    columns = [], selectable = false,
    onSort, onRow, onSelect, empty = { title: "暂无数据" }, rowKey = (r, i) => i,
  } = cfg;
  /* rows 必须可重赋（update 时替换数据）；用 const 解构会在排序后抛
   * "Assignment to constant variable"，表现为整表变成错误态。 */
  let rows = cfg.rows || [];
  /* URL 里的 density 优先于调用方默认值（刷新/深链要能还原，规格 §5.12） */
  let density = getHashParam("density") || cfg.density || "comfortable";

  const tpl = colsTemplate(columns, selectable);
  const wrap = el("div", { class: "rco-tbl", role: "table", "aria-label": cfg.label || "数据表",
    "data-density": density, style: `--rco-cols:${tpl}` });
  const head = el("div", { class: "rco-tbl__head", role: "row", style: `grid-template-columns:${tpl}` });
  const body = el("div", { class: "rco-tbl__body", role: "rowgroup" });
  wrap.append(head, body);

  let sortKey = cfg.sortKey || "";
  let sortDir = cfg.sortDir || "desc";
  const selected = new Set();

  function headerCell(col) {
    const canSort = !!col.sortable;
    const active = canSort && sortKey === col.key;
    const arrow = active ? (sortDir === "asc" ? " ↑" : " ↓") : "";
    const th = el("div", {
      class: "rco-tbl__cell", role: "columnheader",
      "aria-sort": active ? (sortDir === "asc" ? "ascending" : "descending") : "none",
      style: col.align === "right" ? "text-align:right" : "",
    });
    if (!canSort) {
      // 不可排序列：不渲染按钮，避免用户误以为能点（名称/文号属于此类）
      th.textContent = col.label;
      return th;
    }
    const btn = el("button", { type: "button", "data-sort": col.key });
    btn.textContent = col.label + arrow;
    btn.addEventListener("click", () => {
      sortDir = active && sortDir === "desc" ? "asc" : "desc";
      sortKey = col.key;
      renderHead();
      onSort && onSort(sortKey, sortDir);
    });
    th.append(btn);
    return th;
  }

  function renderHead() {
    head.replaceChildren();
    if (selectable) {
      const box = el("input", { type: "checkbox", "aria-label": "全选本页" });
      box.addEventListener("change", () => {
        body.querySelectorAll("input[type=checkbox]").forEach((c) => { c.checked = box.checked; });
        syncSelection();
      });
      head.append(el("div", { class: "rco-tbl__cell" }, [box]));
    }
    for (const c of columns) head.append(headerCell(c));
  }

  function syncSelection() {
    selected.clear();
    body.querySelectorAll("input[type=checkbox]").forEach((c) => {
      const idx = Number(c.dataset.idx);
      if (c.checked) selected.add(idx);
      const row = c.closest(".rco-tbl__row");
      if (row) row.setAttribute("aria-selected", c.checked ? "true" : "false");
    });
    const all = body.querySelectorAll("input[type=checkbox]");
    const master = head.querySelector('input[type="checkbox"]');
    if (master && all.length) {
      master.checked = selected.size === all.length;
      master.indeterminate = selected.size > 0 && selected.size < all.length;
    }
    onSelect && onSelect([...selected].map((i) => rows[i]));
  }

  function renderBody() {
    body.replaceChildren();
    if (!rows.length) {
      body.append(el("div", { class: "p-4", role: "status" }, [
        el("div", { class: "text-bold", text: empty.title || "暂无数据" }),
        empty.desc ? el("div", { class: "text-small mt-1", text: empty.desc }) : null,
      ]));
      return;
    }
    rows.forEach((row, i) => {
      const r = el("div", { class: "rco-tbl__row", role: "row", "aria-selected": "false",
        style: `grid-template-columns:${tpl}`, tabindex: cfg.onRow ? "0" : "-1" });
      if (selectable) {
        const box = el("input", { type: "checkbox", "data-idx": String(i), "aria-label": `选择第 ${i + 1} 行` });
        box.addEventListener("change", syncSelection);
        r.append(el("div", { class: "rco-tbl__cell" }, [box]));
      }
      for (const c of columns) {
        const cell = el("div", {
          class: `rco-tbl__cell${c.wrap ? " rco-tbl__cell--wrap" : ""}`,
          role: "cell", title: c.titleFrom ? String(row[c.titleFrom] || "") : "",
          style: c.align === "right" ? "text-align:right" : "",
        });
        if (c.render) {
          const out = c.render(row, i);
          if (out instanceof Node) cell.append(out);
          else cell.textContent = out == null ? "" : String(out);   // 纯文本；标记请返回节点
        } else {
          cell.textContent = row[c.key] == null || row[c.key] === "" ? "—" : row[c.key];
        }
        r.append(cell);
      }
      if (cfg.onRow) {
        r.addEventListener("click", (ev) => {
          if (ev.target.closest("input,button,a")) return;
          cfg.onRow(row, i);
        });
        r.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter") cfg.onRow(row, i);
        });
      }
      body.append(r);
    });
  }

  renderHead();
  renderBody();

  return {
    el: wrap,
    update(nextRows, density2) {
      if (Array.isArray(nextRows)) rows = nextRows;
      if (density2) wrap.dataset.density = density2;
      renderHead();
      renderBody();
    },
    setDensity(d) { wrap.dataset.density = d; setHashParam("density", d); },
    selection() { return [...selected].map((i) => rows[i]); },
    clearSelection() { selected.clear(); syncSelection(); },
  };
}
