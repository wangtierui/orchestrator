"""全站路由与历史批量验收（P3 收口守卫）。

用法：
    python -m tools.route_all              # 跑 CASES 全部页面
    python -m tools.route_all --slug laws

判据按页面类型分四种（与 §4.3 对齐）：
    pager  有分页的列表页：点下一页 → hash 含 page=2 且分页文案以 "2 /" 开头；back() 还原
    url    状态段/筛选（pushState）：点击 → hash 变化；back() 还原
    q      输入型（replaceState，§4.3 R3）：输入 → hash 变化且内容变化；**不判后退**（无历史条目）
    view   多视图页：切视图 → hash 含 view=；重开同 URL 仍选中该视图
    path   目录树页：深链 ?path= 能渲染对应节点

metrics / overview 无任何可序列化状态，登记为 N/A，不在 CASES 中。

退出码：全过 0；任一失败 1。
"""
from __future__ import annotations

import argparse
import json
import time

BASE = "http://127.0.0.1:8765"
EXIT_OK = 0
EXIT_FAIL = 1

# slug → (模式, 触发选择器, 期望 hash 片段)
CASES = {
    "laws": ("pager", "#pagerSlot button", "page=2"),
    "rules": ("pager", "#pagerSlot button", "page=2"),
    "feedback": ("url", "#toolbarSlot button[aria-pressed]", "status=done"),
    "worklist": ("url", "#toolbarSlot button[aria-pressed]", "status=resolved"),
    "search": ("q", "#q", "q="),
    "release": ("q", "#q", "q="),
    "governance": ("q", "#q", "q=zzzz"),
    "regstats": ("q", "#q", "q=zzzz"),
    "graph": ("view", "#tabsSlot button", "view="),
    "flow": ("view", "#tabsSlot button", "view="),
    "project": ("path", None, "path="),
}


def _snap(s) -> dict:
    return json.loads(s.eval("""(() => JSON.stringify({
      hash: location.hash,
      rows: document.querySelectorAll('.rco-tbl__row').length,
      pager: (document.querySelector('#pagerSlot .text-small') || {}).textContent || '',
      active: [...document.querySelectorAll('#tabsSlot button')]
        .findIndex(b => b.getAttribute('aria-pressed') === 'true')
    }))()"""))


def _open(s, slug: str, qs: str = "") -> None:
    s.cmd("Page.navigate", {"url": f"{BASE}/?v={time.time():.0f}#/{slug}{qs}"})
    for _ in range(90):
        time.sleep(0.4)
        if s.eval("document.body.dataset.pageReady === '1'"):
            break
    time.sleep(1.2)


def _click(s, sel: str, index: int, text: str | None = None) -> None:
    s.eval("""(() => {
      const list = [...document.querySelectorAll(%s)];
      const el = list[%d];
      if (!el) return false;
      if (el.tagName === 'INPUT') {
        el.value = %s || 'zzzz';
        el.dispatchEvent(new Event('input', { bubbles: true }));
      } else el.click();
      return true;
    })()""" % (json.dumps(sel), index, json.dumps(text or "")))


def _run_pager(s, slug, sel, want) -> list:
    _open(s, slug)
    a = _snap(s)
    _click(s, sel, 1)          # 分页里第二个按钮 = 下一页
    time.sleep(2.6)
    b = _snap(s)
    s.eval("history.back()")
    time.sleep(12.0)
    c = _snap(s)
    return [
        {"check": "写 URL", "ok": want in b["hash"] and b["pager"].startswith("2 /"), "detail": b["hash"][-24:]},
        {"check": "后退还原", "ok": c["hash"] == a["hash"] and c["rows"] == a["rows"],
         "detail": f"rows {a['rows']}→{c['rows']}"},
    ]


def _run_url(s, slug, sel, want) -> list:
    _open(s, slug)
    a = _snap(s)
    _click(s, sel, 1)
    time.sleep(2.6)
    b = _snap(s)
    s.eval("history.back()")
    time.sleep(6.0)
    c = _snap(s)
    return [
        {"check": "状态写 URL", "ok": want in b["hash"], "detail": b["hash"][-26:]},
        {"check": "内容变化", "ok": b["rows"] != a["rows"] or b["pager"] != a["pager"],
         "detail": f"rows {a['rows']}→{b['rows']}"},
        {"check": "后退还原", "ok": c["hash"] == a["hash"], "detail": c["hash"][-24:]},
    ]


def _run_q(s, slug, sel, want) -> list:
    """输入型：replaceState 不产生历史条目，因此只判 URL 与内容。"""
    _open(s, slug)
    a = _snap(s)
    _click(s, sel, 0)
    time.sleep(2.6)
    b = _snap(s)
    return [
        {"check": "输入写 URL", "ok": want in b["hash"], "detail": b["hash"][-26:]},
        {"check": "内容变化", "ok": b["rows"] != a["rows"] or b["pager"] != a["pager"],
         "detail": f"rows {a['rows']}→{b['rows']}"},
    ]


def _run_view(s, slug, sel, want) -> list:
    _open(s, slug)
    _click(s, sel, 1)
    time.sleep(2.4)
    b = _snap(s)
    _open(s, slug, b["hash"].split(f"#/{slug}")[-1])
    c = _snap(s)
    return [
        {"check": "视图写 URL", "ok": want in b["hash"] and b["active"] == 1, "detail": b["hash"][-24:]},
        {"check": "深链还原", "ok": c["active"] == 1, "detail": f"active={c['active']}"},
    ]


def _run_path(s, slug, sel, want) -> list:
    _open(s, slug, "?path=web")
    b = _snap(s)
    info = s.eval("document.getElementById('info').innerText.replace(/\\s+/g, ' ')")
    return [
        {"check": "深链渲染节点", "ok": "web" in info and want in b["hash"], "detail": info[:40]},
    ]


RUNNERS = {"pager": _run_pager, "url": _run_url, "q": _run_q, "view": _run_view, "path": _run_path}


def run(slug: str) -> dict:
    from tools.cdp import Session

    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride",
          {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    s.cmd("Page.bringToFront", {})

    mode, sel, want = CASES[slug]
    checks = RUNNERS[mode](s, slug, sel, want)
    return {"slug": slug, "mode": mode, "checks": checks,
            "err": s.eval("String(window.__psErr)"),
            "passed": sum(1 for c in checks if c["ok"]), "total": len(checks)}


def main() -> int:
    ap = argparse.ArgumentParser(description="全站路由与历史验收")
    ap.add_argument("--slug", default=None, help="只跑某页")
    args = ap.parse_args()

    slugs = [args.slug] if args.slug else list(CASES)
    total = passed = 0
    failed_pages = []
    try:
        for slug in slugs:
            out = run(slug)
            total += out["total"]
            passed += out["passed"]
            flag = "OK  " if out["passed"] == out["total"] else "FAIL"
            print(f"  [{flag}] {slug:<11} ({out['mode']}) {out['passed']}/{out['total']}")
            for c in out["checks"]:
                if not c["ok"]:
                    print(f"         x {c['check']}: {c['detail']}")
            if out["err"] not in ("null", "undefined", ""):
                print(f"         ! {out['err']}")
            if out["passed"] != out["total"]:
                failed_pages.append(slug)
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] CDP/页面异常：{type(e).__name__}: {e}")
        return EXIT_FAIL

    print(f"\n{passed}/{total} 项通过 · 失败页：{failed_pages or '无'}")
    return EXIT_OK if passed == total else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
