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
      sankey: function () {}, treeRadial: function () {},
      dispose: function () {}, resizeAll: function () {},
    };
    return;
  }

  var INSTANCES = new Map();
  var SPECS = new Map();          // el -> {kind, spec}（refresh 时重放同一份数据）
  var TIMERS = new Map();         // inst -> intervalId（流动脉冲）

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
      stopPulse(inst);
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
    var values = spec.values || [];
    var opt = baseOption();
    opt.xAxis = {
      type: "category", data: spec.labels || [], boundaryGap: false,
      axisLine: { lineStyle: { color: p.border } },
      axisTick: { show: false },
      axisLabel: { color: p.muted, fontSize: 11 },
    };
    /*: 纵轴**不要从 0 起**：通过率这类指标全在 90±5 区间，0 起会把折线压成一条贴顶直线。
     * scale:true 让轴贴合数据；若数据全在 0–100，则上下各留 ~8% 余量并夹到 [0,100]。 */
    var nums = values.filter(function (v) { return typeof v === "number" && isFinite(v); });
    var yAxis = {
      type: "value", scale: true,
      splitLine: { lineStyle: { color: p.border, type: "dashed" } },
      axisLabel: { color: p.muted, fontSize: 11 },
    };
    if (nums.length) {
      var lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums);
      var pad = Math.max(1, (hi - lo) * 0.35);
      yAxis.min = Math.max(0, Math.floor(lo - pad));
      if (hi <= 100) yAxis.max = Math.min(100, Math.ceil(hi + pad));
    }
    opt.yAxis = yAxis;
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
      /*: 轮廓：词云本身只排字，形状靠一层虚线几何体暗示（圆/菱形/矩形由 spec.shape 决定）。 */
      graphic: [{
        type: spec.shape === "rect" ? "rect" : (spec.shape === "diamond" ? "polygon" : "circle"),
        left: "center", top: "middle",
        shape: spec.shape === "rect"
          ? { width: "92%", height: "86%", r: 8 }
          : (spec.shape === "diamond"
            ? { points: [[90, 0], [180, 90], [90, 180], [0, 90]] }
            : { r: "46%" }),
        style: { fill: "none", stroke: p.border, lineWidth: 1, lineDash: [3, 4] },
        silent: true, z: 0,
      }],
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

  /* ------------------------------------------------------------------ 流动效果
   * "粒子/流动"在 ECharts 里只有 `lines`/`effectScatter` 原生支持（需要坐标系统）；
   * 桑基与树没有粒子轨道，这里用**脉动**近似：周期性在两档透明度/线宽之间切换，
   * 观感上像"流向在动"。prefers-reduced-motion 时完全不启动。
   */
  function pulse(inst, frames, period) {
    if (!inst || !frames || frames.length < 2) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var i = 0;
    var timer = setInterval(function () {
      var dom = inst.getDom && inst.getDom();
      if (!dom || !document.body.contains(dom)) { clearInterval(timer); return; }
      i = (i + 1) % frames.length;
      try { inst.setOption(frames[i], { lazyUpdate: true }); } catch (e) { clearInterval(timer); }
    }, period || 1400);
    TIMERS.set(inst, timer);
  }

  function stopPulse(inst) {
    var tm = TIMERS.get(inst);
    if (tm) { clearInterval(tm); TIMERS.delete(inst); }
  }

  /*: 桑基图（数据流转）：**逐节点 itemStyle**（按层着色），链接用渐变 + 脉动。 */
  function sankey(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    SPECS.set(el, { kind: "sankey", spec: spec });
    stopPulse(inst);
    var p = palette();
    var layerColors = spec.layerColors ||
      [p.accent, p.purple, p.ok, p.warn, "#8250df", "#bc4c00", "#1b7c83", "#57606a"];
    var nodes = spec.nodes || [];
    var links = spec.links || [];

    var data = nodes.map(function (n) {
      var d = Number(n.layer) || 0;
      return {
        name: String(n.name),
        depth: d,
        itemStyle: { color: layerColors[d % layerColors.length], borderWidth: 0 },
        label: { color: p.text, fontSize: 11 },
      };
    });

    var opt = {
      animationDuration: 700,
      tooltip: { trigger: "item", triggerOn: "mousemove",
                 backgroundColor: "#fff", borderColor: p.border, borderWidth: 1,
                 textStyle: { color: p.text, fontSize: 12 } },
      series: [{
        type: "sankey",
        left: 8, right: 120, top: 12, bottom: 12,
        nodeWidth: 12, nodeGap: 8,
        emphasis: { focus: "adjacency" },
        data: data,
        links: links.map(function (l) {
          return { source: String(l.from || l.source), target: String(l.to || l.target),
                   value: Number(l.value) || 1 };
        }),
        label: { position: "right", color: p.muted, fontSize: 11 },
        lineStyle: { color: "gradient", curveness: 0.5, opacity: 0.32 },
      }],
    };
    inst.setOption(opt, true);

    pulse(inst, [{
      series: [{ lineStyle: { opacity: 0.5 } }],
    }, {
      series: [{ lineStyle: { opacity: 0.22 } }],
    }], 1500);
  }

  /*: 径向树（项目结构）：按深度着色，连线脉动（近似"流动"）。 */
  function treeRadial(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    SPECS.set(el, { kind: "treeRadial", spec: spec });
    stopPulse(inst);
    var p = palette();
    var colors = [p.accent, p.purple, p.ok, p.warn, "#8250df"];
    var opt = {
      tooltip: { trigger: "item", triggerOn: "mousemove",
                 backgroundColor: "#fff", borderColor: p.border, borderWidth: 1,
                 textStyle: { color: p.text, fontSize: 12 },
                 formatter: function (o) {
                   var n = o.data || {};
                   return (n.name || "") + (n.children ? "（" + n.children.length + " 项）" : "");
                 } },
      series: [{
        type: "tree",
        data: [spec.tree],
        layout: "radial",
        roam: true,
        initialTreeDepth: spec.depth || 3,
        symbol: "circle",
        symbolSize: 7,
        edgeShape: "curve",
        edgeForkPosition: "50%",
        label: { position: "left", verticalAlign: "middle", fontSize: 11,
                 color: p.text, distance: 6 },
        leaves: { label: { position: "right", fontSize: 10, color: p.muted } },
        emphasis: { focus: "descendant" },
        lineStyle: { color: p.border, width: 1.1, curveness: 0.5, opacity: 0.9 },
        itemStyle: {
          color: function (params) {
            return colors[(params.data.depth || 0) % colors.length];
          },
        },
        animationDuration: 700,
        animationDurationUpdate: 500,
      }],
    };
    inst.setOption(opt, true);

    pulse(inst, [{
      series: [{ lineStyle: { width: 2.2, opacity: 1 } }],
    }, {
      series: [{ lineStyle: { width: 1.1, opacity: 0.65 } }],
    }], 1600);
  }

  /*: 用当前 spec 重放（选中高亮、重置视图） */
  function refresh(el) {
    var s = SPECS.get(el);
    if (!s) return;
    var fn = { line: line, bar: bar, wordcloud: wordcloud, liquid: liquid,
               graph: graph, sankey: sankey, treeRadial: treeRadial }[s.kind];
    if (fn) fn(el, s.spec);
  }

  global.Charts = {
    available: true,
    line: line, bar: bar, wordcloud: wordcloud, liquid: liquid, graph: graph,
    sankey: sankey, treeRadial: treeRadial,
    refresh: refresh, dispose: dispose, resizeAll: resizeAll,
  };

  // 容器尺寸变化（含侧栏折叠）→ 统一 resize
  var t = 0;
  global.addEventListener("resize", function () {
    clearTimeout(t);
    t = setTimeout(resizeAll, 120);
  });
})(window);
