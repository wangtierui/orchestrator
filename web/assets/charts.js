/* ============================================================================
 * charts.js — 工作台**唯一**的图表出口（全部基于本地 echarts 5.5.1 + 5 个插件）
 *
 * 为什么单独一层：之前图表有 3 套手写实现（自绘 SVG 折线、div 条形、自研 g3d 投影）。
 * 这里收成一个门面：页面只声明"要什么图 + 数据"，初始化/主题/自适应/销毁都由这里管。
 *
 * 约定：
 *   · 所有函数接收 **DOM 元素** + 纯数据，不返回 HTML（避免页面里再拼图表标记）；
 *   · 主题颜色取 CSS 变量（与 Primer 令牌同源），不在图表里写死十六进制；
 *   · `Charts.dispose(el)` 幂等；`Charts.resizeAll()` 供窗口/侧栏变化时调用。
 * ========================================================================== */
(function (global) {
  "use strict";

  if (typeof echarts === "undefined") {
    // 库没加载时不抛错：页面仍能用空态提示，不至于整屏白
    global.Charts = {
      available: false,
      line: function () {}, bar: function () {}, wordcloud: function () {},
      liquid: function () {}, graph3d: function () {}, dispose: function () {},
      resizeAll: function () {},
    };
    return;
  }

  var INSTANCES = new Map();

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  function palette() {
    return {
      text: cssVar("--text", "#1f2328"),
      muted: cssVar("--text-muted", "#59636e"),
      border: cssVar("--border", "#d1d9e0"),
      accent: cssVar("--accent", "#0969da"),
      ok: "#12855a",
      warn: "#b26a00",
      bad: "#cf222e",
      purple: "#6c5ce7",
    };
  }

  /*: 统一坐标轴/提示样式 —— 所有图共用，避免每图各调一套 */
  function baseOption() {
    var p = palette();
    return {
      textStyle: { fontFamily: "inherit", fontSize: 12, color: p.text },
      grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#fff",
        borderColor: p.border,
        borderWidth: 1,
        textStyle: { color: p.text, fontSize: 12 },
        extraCssText: "box-shadow:0 8px 24px rgba(31,35,40,.12);border-radius:6px;",
      },
    };
  }

  function ensure(el) {
    if (!el) return null;
    var old = INSTANCES.get(el);
    if (old) return old;
    var inst = echarts.init(el, null, { renderer: "canvas" });
    INSTANCES.set(el, inst);
    return inst;
  }

  function resizeAll() {
    INSTANCES.forEach(function (inst) {
      try { inst.resize(); } catch (e) { /* 容器已移除 */ }
    });
  }

  function dispose(el) {
    var inst = INSTANCES.get(el);
    if (inst) {
      try { inst.dispose(); } catch (e) { /* 已销毁 */ }
      INSTANCES.delete(el);
    }
  }

  /*: 折线（趋势） —— 单序列，面积淡填充，点只在 hover 出现 */
  function line(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    var p = palette();
    var color = spec.color || p.accent;
    var opt = baseOption();
    opt.xAxis = {
      type: "category", data: spec.labels || [], boundaryGap: false,
      axisLine: { lineStyle: { color: p.border } },
      axisTick: { show: false },
      axisLabel: { color: p.muted, fontSize: 11 },
    };
    opt.yAxis = {
      type: "value", splitLine: { lineStyle: { color: p.border, type: "dashed" } },
      axisLabel: { color: p.muted, fontSize: 11 },
    };
    opt.series = [{
      type: "line", data: spec.values || [], smooth: true, showSymbol: true,
      symbolSize: 4, connectNulls: true,
      lineStyle: { width: 2, color: color },
      itemStyle: { color: color },
      areaStyle: { color: color, opacity: 0.08 },
    }];
    inst.setOption(opt, true);
  }

  /*: 横向条形（分布/aging/主题） —— 单序列，值走标签 */
  function bar(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    var p = palette();
    var color = spec.color || p.accent;
    var items = (spec.items || []).slice();
    var opt = baseOption();
    opt.grid = { left: 8, right: 40, top: 8, bottom: 8, containLabel: true };
    opt.tooltip.trigger = "item";
    opt.xAxis = { type: "value", show: false };
    opt.yAxis = {
      type: "category", inverse: true,
      data: items.map(function (x) { return x.label; }),
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: p.text, fontSize: 12, width: 160, overflow: "truncate" },
    };
    opt.series = [{
      type: "bar", barWidth: 12, data: items.map(function (x) { return x.value; }),
      itemStyle: { color: color, borderRadius: [0, 3, 3, 0] },
      label: {
        show: true, position: "right", color: p.muted, fontSize: 11,
        formatter: function (o) { return o.value + (spec.unit || ""); },
      },
    }];
    inst.setOption(opt, true);
  }

  /*: 词云（主题分布）—— echarts-wordcloud */
  function wordcloud(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    var p = palette();
    var colors = [p.accent, p.purple, p.ok, p.warn, "#8250df", "#bc4c00", "#1b7c83"];
    var opt = {
      tooltip: { show: true, formatter: function (o) { return o.name + "：" + o.value; } },
      series: [{
        type: "wordCloud", shape: "circle", width: "100%", height: "100%",
        sizeRange: [12, 28], rotationRange: [0, 0], gridSize: 8, drawOutOfBound: false,
        textStyle: {
          fontFamily: "inherit",
          color: function () { return colors[Math.floor(Math.random() * colors.length)]; },
        },
        data: spec.items || [],
      }],
    };
    inst.setOption(opt, true);
  }

  /*: 液位（覆盖率/完成度）—— echarts-liquidfill */
  function liquid(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    var p = palette();
    var v = Math.max(0, Math.min(1, Number(spec.value) || 0));
    var opt = {
      series: [{
        type: "liquidFill", data: [v, v - 0.06, v - 0.12], radius: "78%",
        color: [p.accent, "rgba(9,105,218,.55)", "rgba(9,105,218,.3)"],
        label: { formatter: (v * 100).toFixed(0) + "%", fontSize: 22, color: p.text },
        outline: { show: true, borderDistance: 4, itemStyle: { borderColor: p.border, borderWidth: 1 } },
        backgroundStyle: { color: "transparent" },
      }],
    };
    inst.setOption(opt, true);
  }

  /*: 3D 图（关系/ER）—— echarts-gl */
  function graph3d(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    var p = palette();
    var nodes = (spec.nodes || []).map(function (n) { return { name: n.name, value: n.value || 1 }; });
    var links = (spec.links || []).map(function (l) {
      return { source: l.source, target: l.target };
    });
    var opt = {
      tooltip: {},
      series: [{
        type: "graph3D",
        shading: "lambert",
        layout: "force",
        force: { repulsion: 120, edgeLength: 40 },
        data: nodes,
        links: links,
        label: { show: false },
        itemStyle: { color: p.accent, opacity: 0.9 },
        lineStyle: { color: "rgba(89,99,110,.35)", width: 1 },
        light: { main: { intensity: 1.1 }, ambient: { intensity: 0.35 } },
      }],
    };
    inst.setOption(opt, true);
  }

  global.Charts = {
    available: true,
    line: line, bar: bar, wordcloud: wordcloud, liquid: liquid, graph3d: graph3d,
    dispose: dispose, resizeAll: resizeAll,
  };

  // 容器尺寸变化（含侧栏折叠）→ 统一 resize
  var t = 0;
  global.addEventListener("resize", function () {
    clearTimeout(t);
    t = setTimeout(resizeAll, 120);
  });
})(window);
