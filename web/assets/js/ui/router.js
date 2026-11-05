/* ui/router.js —— URL 与状态的双向映射（规格 §4.3）
 *
 * 形态： #/<page>?k=v&k2=v2     例： #/laws?q=主题:T1&sort=title&desc=0&page=2
 * 兼容： 旧的 #laws（无斜杠、无参数）→ 归一化为 #/laws
 * 规则： 状态变化才 push；输入中 replace；popstate 还原并回调 onRoute。
 */

const LEGACY = /^#([a-z][\w-]*)$/;

/** 解析当前 hash → { page, params } */
export function parse(hash = location.hash) {
  let h = String(hash || "").replace(/^#/, "");
  const legacy = h.match(LEGACY);
  if (legacy) return { page: legacy[1], params: {} };
  if (h.startsWith("/")) h = h.slice(1);
  const qi = h.indexOf("?");
  const page = (qi >= 0 ? h.slice(0, qi) : h) || "overview";
  const params = {};
  if (qi >= 0) {
    for (const [k, v] of new URLSearchParams(h.slice(qi + 1))) params[k] = v;
  }
  return { page, params };
}

/** { page, params } → hash 字符串 */
export function build(page, params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === "") continue;
    qs.set(k, String(v));
  }
  const q = qs.toString();
  return `#/${page}${q ? `?${q}` : ""}`;
}

let current = null;

/**
 * 初始化：返回 onChange(page, params, opts)，并在 popstate/hashchange 时回调。
 * @param {Function} onRoute
 */
export function initRouter(onRoute) {
  const fire = (opts) => {
    const route = parse();
    current = route;
    onRoute(route.page, route.params, opts || {});
  };

  addEventListener("popstate", () => fire({ pop: true }));
  addEventListener("hashchange", () => {
    const next = parse();
    if (current && build(current.page, current.params) === build(next.page, next.params)) return;
    fire({ pop: true });
  });

  // 首屏：补默认值但不产生历史条目
  if (!location.hash) {
    history.replaceState({ page: "overview", params: {} }, "", build("overview"));
  }
  fire({ initial: true });

  return {
    /** 推入历史（用户操作产生的新状态） */
    push(page, params) {
      const url = build(page, params);
      if (url === build(current ? current.page : "", current ? current.params : {})) return;
      history.pushState({ page, params }, "", url);
      current = { page, params };
    },
    /** 替换当前条目（输入中、或修正非法状态） */
    replace(page, params) {
      const url = build(page, params);
      history.replaceState({ page, params }, "", url);
      current = { page, params };
    },
    current() { return current || parse(); },
    /** 只改参数，保留 page */
    mergeParams(patch) {
      const c = current || parse();
      const next = Object.assign({}, c.params, patch);
      for (const k of Object.keys(next)) if (next[k] === "" || next[k] == null) delete next[k];
      return { page: c.page, params: next };
    },
  };
}
