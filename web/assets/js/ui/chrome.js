/* ui/chrome.js —— 壳层唯一实现：导航数据 + 页签/侧栏渲染（规格 §3.1 / §4.1 / §4.2 / §5.3）
 *
 * 属于"必须自写"的 W5（页面级组合）：Primer 只有组件类，没有"8 项导航 + 业务切片侧栏"这种版式。
 * 视觉一律走 Primer 类与令牌；滚动交给 @primer/behaviors（经 window.RCO.behaviors）。
 */

/** 页面清单：每页仍是独立片段，多页在侧栏当业务切片 */
export const PAGES = {
  overview: { name: "项目总览", slug: "overview", anchors: [["关键指标", "#kpiRow"], ["运行与健康", "#groupA"], ["数据资产", "#groupB"], ["质量指标", "#groupC"], ["任务", "#groupD"], ["发布与告警", "#groupE"]] },
  project: { name: "项目结构", slug: "project", anchors: [["结构树", "#chart"], ["节点信息", "#info"]] },
  flow: { name: "数据流转", slug: "flow", anchors: [["流转图", "#chart"], ["运行步骤", "#tableSlot"]] },
  governance: { name: "治理审计", slug: "governance", anchors: [["指标", "#kpi"], ["运行时间线", "#runsSlot"], ["变更台账", "#auditSlot"]] },
  release: { name: "版本管理", slug: "release", anchors: [["状态卡", "#cardsSlot"], ["提交列表", "#tableSlot"]] },
  regstats: { name: "监管统计", slug: "regstats", anchors: [["KPI", "#kpi"], ["数据结构", "#tablesSlot"], ["数据元", "#elementsSlot"]] },
  search: { name: "文档检索", slug: "search", anchors: [["搜索", "#searchSlot"], ["结果", "#tableSlot"]] },
  laws: { name: "政策法规", slug: "laws", anchors: [["搜索", "#searchSlot"], ["列表", "#tableSlot"], ["分页", "#pagerSlot"]] },
  rules: { name: "公司制度", slug: "rules", anchors: [["搜索", "#searchSlot"], ["列表", "#tableSlot"], ["分页", "#pagerSlot"]] },
  graph: { name: "关系图谱", slug: "graph", anchors: [["图谱", "#chart"], ["高频节点", "#hubs"]] },
  worklist: { name: "程序指令", slug: "worklist", anchors: [["搜索与筛选", "#searchSlot"], ["批量处置条", "#bulkSlot"], ["列表", "#tableSlot"]] },
  feedback: { name: "用户反馈", slug: "feedback", anchors: [["状态与筛选", "#toolbarSlot"], ["列表", "#tableSlot"]] },
  metrics: { name: "指标（已并入总览）", slug: "metrics", anchors: [["门禁趋势", "#c-gate"]] },
};

/** 顶层导航：8 项，顺序固定（规格 §4.1） */
export const NAV = [
  { id: "overview", name: "项目总览", pages: ["overview"] },
  { id: "project", name: "项目结构", pages: ["project"] },
  { id: "flow", name: "数据流转", pages: ["flow"] },
  { id: "governance", name: "治理审计", pages: ["governance"] },
  { id: "release", name: "版本管理", pages: ["release"] },
  { id: "regstats", name: "监管统计", pages: ["regstats"] },
  { id: "docs", name: "文档管理", pages: ["search", "laws", "rules", "graph"] },
  { id: "tasks", name: "任务管理", pages: ["worklist", "feedback"] },
];

export const groupOf = (pageId) => NAV.find((g) => g.pages.includes(pageId)) || NAV[0];

const esc = (v) => String(v == null ? "" : v).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/** 页签行：8 项；当前页所属组高亮 */
export function renderTabs(host, pageId) {
  const active = groupOf(pageId).id;
  host.innerHTML = NAV.map((g) => {
    const cur = g.id === active ? ' aria-current="page"' : "";
    return `<a class="tab" href="#${g.pages[0]}" data-group="${g.id}"${cur}>${esc(g.name)}</a>`;
  }).join("");
}

/** 侧栏：业务切片（本组所有页面）+ 本页小节（锚点） */
export function renderRail(host, pageId) {
  if (!host) return;
  const page = PAGES[pageId] || PAGES.overview;
  const group = groupOf(pageId);
  const slices = group.pages.map((id) => {
    const on = id === pageId ? " on" : "";
    const cur = id === pageId ? ' aria-current="page"' : "";
    return `<a class="item${on}" href="#${id}" data-page="${id}"${cur}>${esc(PAGES[id].name)}</a>`;
  }).join("");
  const anchors = (page.anchors || []).map(([label, sel]) =>
    `<div class="item item-anchor" data-sel="${esc(sel)}" tabindex="0">${esc(label)}</div>`).join("");

  host.innerHTML =
    `<div class="grp">${esc(group.name)}</div>${slices}` +
    `<div class="grp" style="margin-top:var(--space-sm)">本页小节</div>${anchors}`;

  host.querySelectorAll(".item-anchor").forEach((el) => {
    const go = () => {
      const main = document.getElementById("main");
      const target = main && main.querySelector(el.dataset.sel);
      if (!target) return;
      host.querySelectorAll(".item-anchor").forEach((x) => x.classList.remove("on"));
      el.classList.add("on");
      const beh = window.RCO && window.RCO.behaviors;
      if (beh) beh.scrollIntoView(target, main, { direction: "vertical", startMargin: 8, behavior: "smooth" });
      else target.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    el.addEventListener("click", go);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}
