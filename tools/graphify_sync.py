# -*- coding: utf-8 -*-
"""
tools/graphify_sync — 让 graphify 产物跟随本仓 git 提交同步更新。

为什么需要它：
    `graphify-out/`（graph.json / GRAPH_REPORT.md / graph.html）是**派生产物**，不入库
    （见 .gitignore 与 gates/gate_hardcoded_paths.EXCLUDE_DIRS）。因此它不会随 `git commit`
    自动变化 —— 代码改了、图还是旧的，助手查图就会拿到过期事实。本工具把"提交 → 刷新图谱"
    固化成 git hook，保证**图与 HEAD 一致**。

同步动作（无 LLM、无 API 成本）：
    1) `graphify update .`        —— 增量重抽代码 AST（含 .md 链接与 # NOTE: 依据节点），
       重写 graph.json / GRAPH_REPORT.md / graph.html；
    2) `graphify cluster-only .`  —— 重算社区，刷新 .graphify_analysis.json（god node / 内聚度）
       与社区标签，消除 "analysis sidecar is stale" 告警；
    3) `graphify export html --node-limit N` —— **节点数 >5000 时 graphify 默认把 graph.html
       退化为社区聚合视图**（实测 5047 节点 → 只剩 346 社区节点），此处强制全节点交互视图；
    4) `tools/graphify_offline_html.py` —— 重新本地化 vis-network 依赖（graphify 每次重写
       graph.html 都会把 <script> 还原成 unpkg CDN，不重跑补丁就会白屏）。

hook 形态（**后台执行**，不拖慢 commit / checkout）：
    post-commit    —— 提交后刷新（图跟随本次提交）
    post-checkout  —— 切分支/切工作区后刷新（图跟随检出的代码）
    两者都追加一个受管代码块（`>>> graphify-sync` 标记），**不覆盖**已有 hook 内容
    （本仓 post-commit 原有"自动 push"逻辑保持原样）。

用法：
    python tools/graphify_sync.py                  # 立即同步一次（前台，可看输出）
    python tools/graphify_sync.py --install-hooks  # 安装/更新受管 hook 块（幂等）
    python tools/graphify_sync.py --uninstall-hooks
    python tools/graphify_sync.py --status         # 查看 hook 安装状态与最近一次同步日志

hook 日志：graphify-out/sync.log（该目录不入库）。
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
HOOK_DIR = os.path.join(ROOT, ".git", "hooks")
GRAPH_DIR = os.path.join(ROOT, "graphify-out")
SYNC_LOG = os.path.join(GRAPH_DIR, "sync.log")
PATCH_TOOL = os.path.join(ROOT, "tools", "graphify_offline_html.py")
# graph.html 的节点上限：超过该值 graphify 会退化为社区聚合视图（丢全节点交互图），
# 用 --node-limit 提高阈值。可用环境变量覆盖，便于超大图时下调以控制 html 体积。
NODE_LIMIT = os.environ.get("GRAPHIFY_HTML_NODE_LIMIT", "20000")

BEGIN = "# >>> graphify-sync (managed by tools/graphify_sync.py) >>>"
END = "# <<< graphify-sync <<<"
HOOKS = ("post-commit", "post-checkout")


def find_graphify() -> str:
    """定位 graphify 可执行文件（uv tool 默认装在 ~/.local/bin）。"""
    found = shutil.which("graphify")
    if found:
        return found
    home = os.path.expanduser("~")
    for cand in (
        os.path.join(home, ".local", "bin", "graphify.exe"),
        os.path.join(home, ".local", "bin", "graphify"),
    ):
        if os.path.isfile(cand):
            return cand
    return ""


def _sh_path(path: str) -> str:
    """Windows 反斜杠路径 -> Git Bash 可用的正斜杠形式。"""
    return path.replace("\\", "/")


def run_sync(quiet: bool = False) -> int:
    """执行一次同步：update + cluster-only + 全节点 html 导出 + 本地化补丁。"""
    exe = find_graphify()
    if not exe:
        print("[FAIL] 未找到 graphify 可执行文件（期望 ~/.local/bin/graphify，"
              "或先 uv tool install graphifyy）")
        return 1

    # 注意：graph.html 默认在节点数 >5000 时**退化为社区聚合视图**（346 节点），
    # 会丢掉可交互的全节点图；--node-limit 提高阈值即恢复全节点视图。
    steps = [
        ("graphify update .（AST 增量重抽，无 API 成本）", [exe, "update", "."]),
        ("graphify cluster-only .（刷新社区与分析 sidecar）", [exe, "cluster-only", "."]),
        ("graphify export html --node-limit %s（强制全节点视图）" % NODE_LIMIT,
         [exe, "export", "html", "--node-limit", NODE_LIMIT]),
    ]

    total = len(steps) + 1
    for idx, (desc, cmd) in enumerate(steps, start=1):
        if not quiet:
            print("[%d/%d] %s" % (idx, total, desc))
        rc = subprocess.run(cmd, cwd=ROOT).returncode
        if rc != 0:
            if idx == 1:  # update 是根步骤：失败即中止，保留上一版图谱
                print("[FAIL] graphify update 返回 rc=%d —— 图谱未刷新，保留上一版" % rc)
                return rc
            print("[WARN] %s 返回 rc=%d（图谱本体已刷新，继续）" % (desc, rc))

    if not quiet:
        print("[%d/%d] graph.html 依赖本地化（防止回归为 unpkg CDN 白屏）" % (total, total))
    rc = subprocess.run([sys.executable, PATCH_TOOL, "--check"], cwd=ROOT).returncode
    if rc != 0:
        rc = subprocess.run([sys.executable, PATCH_TOOL], cwd=ROOT).returncode
    if rc != 0:
        print("[WARN] 本地化补丁执行异常 rc=%d（图谱本体已刷新）" % rc)
    if not quiet:
        print("[OK] graphify 产物已同步到当前 HEAD")
    return 0


def _strip_block(text: str) -> str:
    out, skip = [], False
    for line in text.splitlines(keepends=True):
        if line.strip() == BEGIN:
            skip = True
            continue
        if line.strip() == END:
            skip = False
            continue
        if not skip:
            out.append(line)
    return "".join(out)


def hook_block() -> str:
    """生成受管 hook 片段：后台执行，输出落 graphify-out/sync.log。"""
    return (
        "%s\n"
        '# 提交/检出后后台刷新 graphify 派生产物（图跟 HEAD 走；日志 graphify-out/sync.log）\n'
        'export PATH="$HOME/.local/bin:$PATH"\n'
        '# 日志固定 UTF-8（否则 Git Bash 下 python stdout 走 GBK，中文状态行变乱码）\n'
        'export PYTHONIOENCODING=utf-8\n'
        'mkdir -p "%s"\n'
        '"%s" "%s" --quiet >> "%s" 2>&1 &\n'
        "%s\n"
        % (
            BEGIN,
            _sh_path(GRAPH_DIR),
            _sh_path(sys.executable),
            _sh_path(os.path.join(ROOT, "tools", "graphify_sync.py")),
            _sh_path(SYNC_LOG),
            END,
        )
    )


def install_hooks() -> int:
    if not os.path.isdir(HOOK_DIR):
        print("[FAIL] 未找到 %s —— 请先 git init / 在仓库内执行" % HOOK_DIR)
        return 1
    block = hook_block()
    for name in HOOKS:
        path = os.path.join(HOOK_DIR, name)
        text = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            text = "#!/bin/sh\n"
        new = _strip_block(text).rstrip("\n") + "\n\n" + block
        if not new.startswith("#!"):
            new = "#!/bin/sh\n" + new
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        os.chmod(path, 0o755)
        print("[OK] %s 已%s受管代码块" % (name, "更新" if text else "写入"))
    return 0


def uninstall_hooks() -> int:
    for name in HOOKS:
        path = os.path.join(HOOK_DIR, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        new = _strip_block(text)
        if new.strip() in ("", "#!/bin/sh"):
            os.remove(path)
            print("[OK] %s 已移除（原本只有受管块）" % name)
            continue
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        os.chmod(path, 0o755)
        print("[OK] %s 已移除受管代码块" % name)
    return 0


def show_status() -> int:
    print("graphify 可执行文件: %s" % (find_graphify() or "(未找到)"))
    for name in HOOKS:
        path = os.path.join(HOOK_DIR, name)
        if not os.path.isfile(path):
            print("%-15s: 无" % name)
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        print("%-15s: %s" % (name, "已安装受管块" if BEGIN in text else "未安装"))
    if os.path.isfile(SYNC_LOG):
        size = os.path.getsize(SYNC_LOG)
        print("sync.log: %d bytes, 末次修改 %s" % (size, _mtime_str(SYNC_LOG)))
    else:
        print("sync.log: 尚无（hook 还没触发过）")
    return 0


def _mtime_str(path: str) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="graphify 产物跟随 git 提交同步")
    ap.add_argument("--install-hooks", action="store_true", help="安装 post-commit / post-checkout 受管块")
    ap.add_argument("--uninstall-hooks", action="store_true", help="移除受管块")
    ap.add_argument("--status", action="store_true", help="查看安装状态")
    ap.add_argument("--quiet", action="store_true", help="静默模式（供 hook 调用）")
    args = ap.parse_args(argv)

    if args.install_hooks:
        return install_hooks()
    if args.uninstall_hooks:
        return uninstall_hooks()
    if args.status:
        return show_status()
    return run_sync(quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
