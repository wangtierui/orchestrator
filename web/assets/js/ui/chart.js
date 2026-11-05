/* ui/chart.js —— ECharts 唯一出口（令牌取色 + 尺寸约束 + 销毁）
 *
 * 规格：§7.1 令牌桥接、§7.3 通用配置
 * 纪律：option 里不得出现字面色值；图表高度必须由调用方给定（否则高度为 0）。
 */

import { el, token } from "./core.js";

/** 从 primitives 取色，返回 ECharts 可直接用的色板。 */
export function palette() {
  return [
    token("--fgColor-accent", "#0969da"),
    token("--fgColor-success", "#1a7f37"),
    token("--fgColor-attention", "#9a6700"),
    token("--fgColor-danger", "#d1242f"),
    token("--fgColor-muted", "#59636e"),
  ];
}

/** 统一基础配置：无动画、tooltip 不出界、字号跟随 token、字体跟随系统栈。 */
export function baseOption() {
  return {
    animationDuration: 0,
    textStyle: { fontSize: 12, fontFamily: token("--fontStack-system") },
    tooltip: { confine: true },
    grid: { left: 8, right: 8, top: 24, bottom: 8, containLabel: true },
    color: palette(),
  };
}

/**
 * 挂载图表。
 * @param {object} cfg {el|selector, option, height, onInit}
 * @returns {{setOption:Function, resize:Function, dispose:Function, chart:object}}
 */
export function mountChart(cfg = {}) {
  const host = typeof cfg.el === "string" ? document.querySelector(cfg.el) : cfg.el;
  if (!host) throw new Error("mountChart: 容器不存在");
  if (!window.echarts) throw new Error("mountChart: ECharts 未加载");
  if (cfg.height) host.style.height = `${cfg.height}px`;

  const chart = window.echarts.init(host, null, { renderer: "canvas" });
  const option = Object.assign(baseOption(), cfg.option || {});
  chart.setOption(option, true);

  const ro = new ResizeObserver(() => { try { chart.resize(); } catch (_) { /* 容器已移除 */ } });
  ro.observe(host);

  return {
    chart,
    setOption(next) { chart.setOption(Object.assign(baseOption(), next || {}), true); },
    resize() { chart.resize(); },
    dispose() { ro.disconnect(); chart.dispose(); },
  };
}

/** 图表的可访问容器：role=img + aria-label + 一句摘要（读屏用）。 */
export function chartHost(cfg = {}) {
  return el("div", {
    id: cfg.id || "",
    role: "img",
    "aria-label": cfg.label || "图表",
    "aria-busy": "false",
    style: `height:${cfg.height || 220}px;width:100%;`,
  });
}
