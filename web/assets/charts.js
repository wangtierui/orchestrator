/* ============================================================================
 * charts.js — 工作台**唯一**的图表出口（本地 echarts 5.5.1 + 插件）
 *
 * 为什么单独一层：之前图表有 3 套手写实现（自绘 SVG 折线、div 条形、自研 3D 投影）。
 * 这里收成一个门面：页面只声明"要什么图 + 数据"，初始化 / 主题 / 自适应 / 销毁都由这里管。
 *
 * 约定：
 *   · 所有函数接收 **DOM 元素** + 纯数据，不返回 HTML；
 *   · 主题颜色取 CSS 变量（与 Primer 令牌同源），不写死十六进制；
 *   · `Charts.refresh(el)` 用登记过的 spec 重放（选中态变化、重置视图都走它）。
 * ========================================================================== */
(function (global) {
  "use strict";

  if (typeof echarts === "undefined") {
    // 库没加载时不抛错：页面仍能显示空态，不至于整屏白
    global.Charts = {
      available: false,
      line: function () {}, bar: function () {}, wordcloud: function () {},
      liquid: function () {}, graph: function () {}, refresh: function () {},
      dispose: function () {}, resizeAll: function () {},
    };
    return;
  }

  var INSTANCES = new Map();
  var SPECS = new Map();          // el -> {kind, spec}（refresh 时重放同一份数据）

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
    SPECS.delete(el);
  }

  /*: 折线（趋势） */
  function line(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    SPECS.set(el, { kind: "line", spec: spec });
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
    var values = spec.values || [];
    opt.series = [{
      type: "line", data: values, smooth: true, showSymbol: true,
      symbolSize: 4, connectNulls: true,
      lineStyle: { width: 2, color: color },
      itemStyle: { color: color },
      areaStyle: { color: color, opacity: 0.08 },
    }];

    /*: 回归线（**可选**，spec.regression 才画）：用 ecStat 拟合，
     * R² 由残差/总平方和自己算（ecStat 该版本不返回 r2）。 */
    if (spec.regression && window.ecStat && values.length >= 3) {
      var pairs = values.map(function (v, i) { return [i, v]; });
      try {
        var fit = window.ecStat.regression("linear", pairs);
        var pts = fit.points || [];
        var mean = values.reduce(function (a, b) { return a + b; }, 0) / values.length;
        var sst = values.reduce(function (a, b) { return a + Math.pow(b - mean, 2); }, 0);
        var sse = values.reduce(function (a, v, i) {
          var y = pts[i] ? pts[i][1] : v;
          return a + Math.pow(v - y, 2);
        }, 0);
        var r2 = sst > 0 ? 1 - sse / sst : 1;

        opt.series.push({
          type: "line", data: pts.map(function (q) { return q[1]; }),
          smooth: false, symbol: "none", silent: true, z: 1,
          lineStyle: { width: 1.5, type: "dashed", color: p.muted },
        });
        opt.title = {
          text: "",
          subtext: "拟合 " + String(fit.expression || "").replace(/\s+/g, "")
                   + " · R²=" + r2.toFixed(2),
          right: 8, top: 0,
          subtextStyle: { color: p.muted, fontSize: 11, fontFamily: "inherit" },
        };
      } catch (e) { /* 拟合失败不影响主图 */ }
    }

    inst.setOption(opt, true);
  }

  /*: 横向条形（分布/aging/主题） */
  function bar(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    SPECS.set(el, { kind: "bar", spec: spec });
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
    SPECS.set(el, { kind: "wordcloud", spec: spec });
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

  /*: 液位（达标率/完成度）—— echarts-liquidfill */
  function liquid(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    SPECS.set(el, { kind: "liquid", spec: spec });
    var p = palette();
    var v = Math.max(0, Math.min(1, Number(spec.value) || 0));
    var opt = {
      series: [{
        type: "liquidFill", data: [v, v - 0.06, v - 0.12], radius: "78%",
        color: [p.accent, "rgba(9,105,218,.55)", "rgba(9,105,218,.3)"],
        label: { formatter: (v * 100).toFixed(0) + "%", fontSize: 22, color: p.text },
        outline: { show: true, borderDistance: 4,
                   itemStyle: { borderColor: p.border, borderWidth: 1 } },
        backgroundStyle: { color: "transparent" },
      }],
    };
    inst.setOption(opt, true);
  }

  /*: 关系图 / E-R（**2D force**）。
   *
   * 为什么不是 echarts-gl 的 3D 图：实测两个事实——
   *   · echarts-gl 2.0.9 的 dist 里**没有 `graph3D` 系列**（只有 `graphGL`）；
   *   · `graphGL` 能出画布，但**不产生节点 click**（pointer 命不中节点），
   *     而"点节点 → 下方列表联动"是必须保留的交互。
   * 因此用 ECharts 内置 `graph`（force）：roam 缩放、节点可拖、click 精确。
   */
  function graph(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    SPECS.set(el, { kind: "graph", spec: spec });
    var p = palette();
    var nodes = spec.nodes || [];
    var links = spec.links || [];

    var groups = [];
    nodes.forEach(function (n) {
      var g = String(n.group == null ? "" : n.group);
      if (groups.indexOf(g) < 0) groups.push(g);
    });
    var colors = [p.accent, p.purple, p.ok, p.warn, "#8250df", "#bc4c00", "#1b7c83"];
    var hollow = spec.hollowGroups || [];      // 例：语料外节点（空心 + 虚线圈）
    var maxDeg = 1;
    nodes.forEach(function (n) { maxDeg = Math.max(maxDeg, Number(n.degree) || 1); });
    var sel = String(spec.selected || "");

    var data = nodes.map(function (n) {
      var d = Number(n.degree) || 1;
      var id = String(n.id);
      var showLabel = !!spec.labelAll || d >= (spec.labelMin || 8);
      var gi = Math.max(0, groups.indexOf(String(n.group == null ? "" : n.group)));
      var isHollow = hollow.indexOf(String(n.group == null ? "" : n.group)) >= 0;
      var style;

      if (isHollow) {
        style = { color: "#fff", borderColor: colors[gi % colors.length], borderWidth: 2 };
      }

      if (id === sel) {
        style = style || {};
        style.borderColor = p.accent;
        style.borderWidth = 3;
      }

      return {
        name: id,
        labelText: String(n.name || id),
        value: d,
        category: gi,
        symbolSize: 7 + 11 * Math.sqrt(d / maxDeg),
        label: { show: showLabel },
        itemStyle: style,
      };
    });

    var edges = links.map(function (l) {
      return { source: String(l.from || l.source), target: String(l.to || l.target) };
    });

    inst.off("click");
    inst.on("click", function (params) {
      if (!params || params.dataType !== "node") return;
      if (typeof spec.onSelect === "function") spec.onSelect(params.name);
    });

    var catNames = groups.map(function (g) { return g || "默认"; });
    var opt = {
      animationDuration: 500,
      legend: [{ data: catNames, bottom: 0, itemWidth: 10, itemHeight: 10,
                 textStyle: { color: p.muted, fontSize: 11 } }],
      tooltip: {
        confine: true,
        backgroundColor: "#fff",
        borderColor: p.border,
        borderWidth: 1,
        textStyle: { color: p.text, fontSize: 12 },
        extraCssText: "box-shadow:0 8px 24px rgba(31,35,40,.12);border-radius:6px;",
        formatter: function (o) {
          if (o.dataType === "node") return o.data.labelText + "（引用 " + o.data.value + "）";
          return "";
        },
      },
      series: [{
        type: "graph", layout: spec.layout === "circular" ? "circular" : "force",
        roam: true, draggable: true,
        force: { repulsion: spec.repulsion || 90, edgeLength: [40, 120], gravity: 0.08 },
        circular: { rotateLabel: false },
        categories: groups.map(function (g, i) {
          return { name: g || "默认", itemStyle: { color: colors[i % colors.length] } };
        }),
        label: { position: "right", color: p.muted, fontSize: 11 },
        lineStyle: { color: "rgba(89,99,110,.35)", width: 1, curveness: 0.08 },
        emphasis: { focus: "adjacency", lineStyle: { width: 2 } },
        data: data,
        links: edges,
      }],
    };
    inst.setOption(opt, true);
  }

  /*: 用当前 spec 重放（选中高亮、重置视图） */
  function refresh(el) {
    var s = SPECS.get(el);
    if (!s) return;
    var fn = { line: line, bar: bar, wordcloud: wordcloud, liquid: liquid, graph: graph }[s.kind];
    if (fn) fn(el, s.spec);
  }

  global.Charts = {
    available: true,
    line: line, bar: bar, wordcloud: wordcloud, liquid: liquid, graph: graph,
    refresh: refresh, dispose: dispose, resizeAll: resizeAll,
  };

  // 容器尺寸变化（含侧栏折叠）→ 统一 resize
  var t = 0;
  global.addEventListener("resize", function () {
    clearTimeout(t);
    t = setTimeout(resizeAll, 120);
  });
})(window);
