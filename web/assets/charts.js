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
      sankey: function () {}, treeRadial: function () {}, rose: function () {},
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
      var dom = inst.getDom && inst.getDom();
      if (dom && dom.__skTitles) {
        setTimeout(function () { drawColHeads(dom, inst, dom.__skTitles); }, 120);
      }
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
      graphic: { elements: [{
        type: spec.shape === "rect" ? "rect" : (spec.shape === "diamond" ? "polygon" : "circle"),
        left: "center", top: "middle",
        shape: spec.shape === "rect"
          ? { width: "92%", height: "86%", r: 8 }
          : (spec.shape === "diamond"
            ? { points: [[90, 0], [180, 90], [90, 180], [0, 90]] }
            : { r: Math.max(40, Math.round(Math.min(el.clientWidth || 320,
                                                   el.clientHeight || 240) * 0.46)) }),
        style: { fill: "none", stroke: p.border, lineWidth: 1, lineDash: [3, 4] },
        silent: true, z: 0,
      }] },
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

  /* ------------------------------------------------------------------ 粒子/流动
   * ECharts 的桑基与树都没有原生"粒子"轨道（只有 lines/effectScatter 有，且需坐标系统）。
   * 做法：在图表容器上叠一层 canvas，**按真实图元的几何**画流动颗粒——
   *   · 桑基：链接是 ribbon path（x1,y1,x2,y2,cpx1,cpy1,cpx2,cpy2 + extent），取中线做三次贝塞尔；
   *   · 树：连线是 bezier-curve，直接取 x1,y1,cpx1,cpy1,cpx2,cpy2,x2,y2。
   * 这样粒子严格贴着实际连线走，不会出现"粒子与图不同步"。
   * prefers-reduced-motion 下不启动（静态图仍然可读）。
   */
  function collectRails(inst) {
    var list = inst.getZr().storage.getDisplayList();
    var rails = [];
    for (var i = 0; i < list.length; i++) {
      var e = list[i];
      var sh = e.shape || {};
      var color = (e.style && (e.style.stroke || e.style.fill)) || null;
      if (e.type === "bezier-curve" && sh.x1 != null && sh.x2 != null) {
        rails.push({ x1: sh.x1, y1: sh.y1, c1x: sh.cpx1, c1y: sh.cpy1,
                     c2x: sh.cpx2, c2y: sh.cpy2, x2: sh.x2, y2: sh.y2, color: color });
      } else if (e.type === "path" && sh.x1 != null && sh.x2 != null && sh.extent != null) {
        /* 桑基飘带：y1/y2 是上沿，中线要各下移半个带宽 */
        var half = (sh.extent || 0) / 2;
        rails.push({ x1: sh.x1, y1: sh.y1 + half, c1x: sh.cpx1, c1y: sh.cpy1 + half,
                     c2x: sh.cpx2, c2y: sh.cpy2 + half, x2: sh.x2, y2: sh.y2 + half,
                     color: color });
      }
    }
    return rails;
  }

  function bezierAt(r, t) {
    var u = 1 - t;
    var a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    return [a * r.x1 + b * r.c1x + c * r.c2x + d * r.x2,
            a * r.y1 + b * r.c1y + c * r.c2y + d * r.y2];
  }

  function startParticles(el, inst) {
    stopPulse(inst);
    if (!el || !inst) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    var rails = collectRails(inst);
    if (!rails.length) return;

    var cv = el.querySelector("canvas.flowfx");
    if (!cv) {
      cv = document.createElement("canvas");
      cv.className = "flowfx";
      cv.setAttribute("aria-hidden", "true");
      el.appendChild(cv);
    }
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var w = el.clientWidth, h = el.clientHeight;
    cv.width = Math.max(1, Math.round(w * dpr));
    cv.height = Math.max(1, Math.round(h * dpr));
    cv.style.cssText = "position:absolute;left:0;top:0;width:" + w + "px;height:" + h +
                       "px;pointer-events:none";
    var ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    /* 每条轨道 1–3 颗粒；相位错开，看起来像连续流动 */
    var parts = [];
    rails.forEach(function (r, i) {
      var n = rails.length > 60 ? 1 : 2;
      for (var k = 0; k < n; k++) {
        parts.push({ r: r, phase: (i * 0.37 + k * 0.5) % 1, speed: 0.00016 + (i % 5) * 0.00002 });
      }
    });

    var last = 0;
    var timer = null;

    function frame(ts) {
      if (!document.body.contains(el)) { stopPulse(inst); return; }
      if (ts - last < 33) { timer = requestAnimationFrame(frame); TIMERS.set(inst, timer); return; }
      last = ts;
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        var t = (p.phase + ts * p.speed) % 1;
        var pt = bezierAt(p.r, t);
        ctx.beginPath();
        ctx.arc(pt[0], pt[1], 2.2, 0, Math.PI * 2);
        ctx.fillStyle = p.r.color || "#0969da";
        ctx.globalAlpha = 0.85 - 0.45 * Math.abs(t - 0.5) * 2;
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      timer = requestAnimationFrame(frame);
      TIMERS.set(inst, timer);
    }
    timer = requestAnimationFrame(frame);
    TIMERS.set(inst, timer);
  }

  function stopPulse(inst) {
    var tm = TIMERS.get(inst);
    if (!tm) return;
    if (typeof tm === "number") cancelAnimationFrame(tm);
    else clearInterval(tm);
    TIMERS.delete(inst);
    var el = inst.getDom && inst.getDom();
    var cv = el && el.querySelector && el.querySelector("canvas.flowfx");
    if (cv) cv.remove();
  }

  /*: 列头覆盖层：按真实节点 x 定位，窗口缩放时重算（resizeAll 会调）。 */
  function drawColHeads(el, inst, titles) {
    if (!el) return;
    var old = el.querySelector(".sk-cols");
    if (old) old.remove();
    el.__skTitles = titles || null;
    if (!titles || !titles.length) return;
    var data = inst.getModel().getSeriesByIndex(0).getData();
    var colX = {};
    for (var i = 0; i < data.count(); i++) {
      var nodeEl = data.getItemGraphicEl(i);
      if (!nodeEl) continue;
      var d = (data.getRawDataItem(i) || {}).depth || 0;
      var r = nodeEl.getBoundingRect();
      var gx = nodeEl.transformCoordToGlobal(r.x, r.y)[0];
      if (colX[d] === undefined || gx < colX[d]) colX[d] = gx;
    }
    var wrap = document.createElement("div");
    wrap.className = "sk-cols";
    Object.keys(colX).sort(function (a, b) { return a - b; }).forEach(function (d) {
      var sp = document.createElement("span");
      sp.textContent = titles[d] || ("第 " + (Number(d) + 1) + " 列");
      sp.style.left = Math.round(colX[d]) + "px";
      wrap.appendChild(sp);
    });
    el.appendChild(wrap);
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
      /* 颜色看语义层（保持图例可读），列位置看依赖深度（同一层也可能分几列） */
      var lyr = Number(n.layer) || 0;
      var dep = Number(n.depth) || 0;
      return {
        name: String(n.name),
        depth: dep,
        vol: n.vol || "",
        itemStyle: { color: layerColors[lyr % layerColors.length], borderWidth: 0 },
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
        left: 8, right: 120, top: 30, bottom: 12,
        nodeWidth: 12, nodeGap: 8,
        emphasis: { focus: "adjacency" },
        data: data,
        links: links.map(function (l) {
          return { source: String(l.from || l.source), target: String(l.to || l.target),
                   value: Number(l.value) || 1 };
        }),
        nodeAlign: "justify",
        layoutIterations: 32,
        label: {
          position: "right", color: p.muted, fontSize: 11,
          formatter: function (o) {
            var d = o.data || {};
            return d.vol ? d.name + " · " + d.vol : d.name;
          },
        },
        lineStyle: { color: "gradient", curveness: 0.5, opacity: 0.32 },
      }],
    };
    inst.off("click");
    inst.on("click", function (params) {
      if (!params || params.dataType !== "node") return;
      if (typeof spec.onSelect === "function") spec.onSelect(params.name);
    });
    inst.setOption(opt, true);

    /* 列头：布局稳定后按每一列**真实节点 x** 画 HTML 覆盖层
     * （ECharts 的 graphic 在 setOption 合并时被忽略，实测不出来，故改用 DOM） */
    setTimeout(function () { drawColHeads(el, inst, spec.colTitles); }, 760);

    setTimeout(function () { startParticles(el, inst); }, 60);
  }

  /*: 南丁格尔玫瑰图（主题分布）：半径表示数值，**全部条目不合并**。 */
  function rose(el, spec) {
    var inst = ensure(el);
    if (!inst) return;
    SPECS.set(el, { kind: "rose", spec: spec });
    stopPulse(inst);
    var p = palette();
    var items = (spec.items || []).slice();
    var total = items.reduce(function (a, x) { return a + (Number(x.value) || 0); }, 0);
    var colors = [p.accent, p.purple, p.ok, p.warn, "#8250df", "#bc4c00", "#1b7c83",
                  "#57606a", "#0969da", "#6c5ce7", "#12855a"];
    var opt = {
      tooltip: {
        trigger: "item",
        formatter: function (o) {
          var pct = total ? (o.value / total * 100).toFixed(1) : "0";
          return o.name + "：" + o.value + "（" + pct + "%）";
        },
        backgroundColor: "#fff", borderColor: p.border, borderWidth: 1,
        textStyle: { color: p.text, fontSize: 12 },
      },
      legend: {
        ty: "scroll", bottom: 0, itemWidth: 9, itemHeight: 9,
        textStyle: { color: p.muted, fontSize: 11 },
      },
      series: [{
        type: "pie",
        roseType: "radius",
        radius: ["16%", "68%"],
        center: ["50%", "46%"],
        itemStyle: { borderColor: "#fff", borderWidth: 1, borderRadius: 3 },
        label: { color: p.text, fontSize: 11, formatter: "{b} {c}" },
        labelLine: { length: 8, length2: 8, lineStyle: { color: p.border } },
        data: items.map(function (x, i) {
          return { name: x.label, value: Number(x.value) || 0,
                   itemStyle: { color: colors[i % colors.length] } };
        }),
      }],
    };
    inst.setOption(opt, true);
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
    inst.off("click");
    inst.setOption(opt, true);

    /* 树节点很小、标签又盖在符号上：ECharts 的 click 只认符号本体，点到文字就没了。
     * 这里改成 zrender 级**最近节点**命中（18px 内），点符号或点文字都能选中。 */
    var zr = inst.getZr();
    if (el.__treePick) zr.off("click", el.__treePick);
    el.__treePick = function (ev) {
      var data = inst.getModel().getSeriesByIndex(0).getData();
      var best = -1, bestD = 18 * 18;
      for (var i = 0; i < data.count(); i++) {
        var g = data.getItemGraphicEl(i);
        if (!g || !g.transformCoordToGlobal) continue;
        var r = g.getBoundingRect();
        var p = g.transformCoordToGlobal(r.x + r.width / 2, r.y + r.height / 2);
        var dx = p[0] - ev.offsetX, dy = p[1] - ev.offsetY;
        var d = dx * dx + dy * dy;
        if (d < bestD) { bestD = d; best = i; }
      }
      if (best < 0 || typeof spec.onSelect !== "function") return;
      var raw = null;
      try { raw = data.getRawDataItem(best); } catch (e) { raw = null; }
      spec.onSelect(raw || { name: data.getName(best) });
    };
    zr.on("click", el.__treePick);

    setTimeout(function () { startParticles(el, inst); }, 120);
  }

  /*: 用当前 spec 重放（选中高亮、重置视图） */
  function refresh(el) {
    var s = SPECS.get(el);
    if (!s) return;
    var fn = { line: line, bar: bar, wordcloud: wordcloud, liquid: liquid,
               graph: graph, sankey: sankey, treeRadial: treeRadial,
               rose: rose }[s.kind];
    if (fn) fn(el, s.spec);
  }

  global.Charts = {
    available: true,
    line: line, bar: bar, wordcloud: wordcloud, liquid: liquid, graph: graph,
    sankey: sankey, treeRadial: treeRadial, rose: rose,
    refresh: refresh, dispose: dispose, resizeAll: resizeAll,
  };

  // 容器尺寸变化（含侧栏折叠）→ 统一 resize
  var t = 0;
  global.addEventListener("resize", function () {
    clearTimeout(t);
    t = setTimeout(resizeAll, 120);
  });
})(window);
