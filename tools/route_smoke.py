"""路由与历史验收（规格 §4.3 四条）——可重复执行的判定工具。

用法：
    python -m tools.route_smoke            # 默认对 laws 页跑四条
    python -m tools.route_smoke --slug laws

四条判据（每条都同时看 URL 与内容，避免"hash 变了但表没变"的假通过）：
    A 翻页写 URL：点「下一页」→ hash 含 page=2，分页文案以 "2 /" 开头
    B 后退还原：history.back() → hash 含 page=1，且首行与初始一致
    C 前进还原：history.forward() → hash 含 page=2，且首行与第 2 页一致
    D 输入不堆历史：输入搜索词 → hash 含 q=，history.length 不增加

退出码：全过 0；任一失败 1。
"""
from __future__ import annotations

import argparse
import json
import time

# 以 python -m tools.route_smoke 运行，仓库根已在 sys.path（不做 sys.path 引导）

BASE = "http://127.0.0.1:8765"
EXIT_OK = 0
EXIT_FAIL = 1


def _snap(s) -> dict:
    return json.loads(s.eval("""(() => JSON.stringify({
      hash: location.hash,
      pager: (document.querySelector('#pagerSlot .text-small') || {}).textContent || '',
      first: (document.querySelector('.rco-tbl__row') || {innerText: ''}).innerText.replace(/\\s+/g, ' ').slice(0, 26),
      len: history.length
    }))()"""))


def run(slug: str = "laws") -> dict:
    from tools.cdp import Session

    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride",
          {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    s.cmd("Page.bringToFront", {})
    s.cmd("Page.navigate", {"url": f"{BASE}/?v={time.time():.0f}#/{slug}?page=1"})
    for _ in range(80):
        time.sleep(0.4)
        if s.eval("document.querySelectorAll('.rco-tbl__row').length"):
            break
    time.sleep(1.2)

    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 分页可能在表格出现后稍晚渲染：等按钮稳定再取基线（此前把它当"点击无效"，误判 N6）
    for _ in range(40):
        if s.eval("!!document.querySelector('#pagerSlot button')"):
            break
        time.sleep(0.2)
    time.sleep(0.4)

    p1 = _snap(s)
    add("A1 初始渲染", p1["pager"].startswith("1 /"), f"pager={p1['pager'][:12]!r}")

    s.eval("""(() => {
      const b = [...document.querySelectorAll('#pagerSlot button')]
        .find((x) => x.textContent.indexOf('下一页') >= 0);
      if (b && !b.disabled) b.click();
    })()""")
    time.sleep(2.8)
    p2 = _snap(s)
    add("A 翻页写 URL", "page=2" in p2["hash"] and p2["pager"].startswith("2 /"),
        f"hash=…{p2['hash'][-22:]!r} pager={p2['pager'][:7]!r}")

    s.eval("history.back()")
    time.sleep(3.2)
    p3 = _snap(s)
    add("B 后退还原", "page=1" in p3["hash"] and p3["pager"].startswith("1 /") and p3["first"] == p1["first"],
        f"hash=…{p3['hash'][-22:]!r} pager={p3['pager'][:7]!r} 首行同初始={p3['first'] == p1['first']}")

    s.eval("history.forward()")
    time.sleep(3.2)
    p4 = _snap(s)
    add("C 前进还原", "page=2" in p4["hash"] and p4["pager"].startswith("2 /") and p4["first"] == p2["first"],
        f"hash=…{p4['hash'][-22:]!r} pager={p4['pager'][:7]!r} 首行同第2页={p4['first'] == p2['first']}")

    before_len = _snap(s)["len"]
    s.eval("""(() => { const i = document.getElementById('q');
      if (!i) return; i.value = '保险'; i.dispatchEvent(new Event('input', { bubbles: true })); })()""")
    time.sleep(2.8)
    p5 = _snap(s)
    add("D 输入不堆历史", "q=" in p5["hash"] and p5["len"] == before_len,
        f"hash 含 q={'q=' in p5['hash']} 历史增量={p5['len'] - before_len}")

    return {"slug": slug, "checks": checks, "err": s.eval("String(window.__psErr)"),
            "passed": sum(1 for c in checks if c["ok"]), "total": len(checks)}


def main() -> int:
    ap = argparse.ArgumentParser(description="路由与历史四条验收")
    ap.add_argument("--slug", default="laws", help="页面 slug，默认 laws")
    args = ap.parse_args()

    try:
        out = run(args.slug)
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] CDP 不可用：{type(e).__name__}: {e}")
        return EXIT_FAIL

    for c in out["checks"]:
        flag = "OK  " if c["ok"] else "FAIL"
        print(f"  [{flag}] {c['check']:<14} {c['detail']}")
    if out["err"] not in ("null", "undefined", ""):
        print(f"  [WARN] 页面异常：{out['err']}")
    print(f"\n{out['passed']}/{out['total']} 通过（{out['slug']}）")
    return EXIT_OK if out["passed"] == out["total"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
