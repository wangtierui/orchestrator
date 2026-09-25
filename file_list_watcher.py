#!/usr/bin/env python3
# file_list_watcher.py
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

# 根目录 = 当前脚本所在目录
ROOT = Path(__file__).resolve().parent

# 输出的 md 文件
OUTPUT = ROOT / "FILE_LIST.md"

# 当前脚本名，避免把自己列进去
SCRIPT_NAME = Path(__file__).name

# 排除的目录
EXCLUDE_DIRS = {
    ".git", ".svn", ".hg",
    "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", "venv", ".venv", "env",
    "dist", "build", ".idea", ".vscode",
}

# 排除的文件
EXCLUDE_FILES = {OUTPUT.name, SCRIPT_NAME}

# 排除的文件扩展名（统一小写，含点）
EXCLUDE_EXTS = {
    # 文档/办公/电子书
    ".doc", ".docx", ".rtf", ".odt", ".wps",
    ".xls", ".xlsx", ".xlsm", ".csv", ".tsv", ".ods", ".et",
    ".ppt", ".pptx", ".pps", ".ppsx", ".odp",
    ".pdf", ".txt", ".md", ".markdown", ".rst", ".tex", ".log",
    ".epub", ".mobi", ".azw", ".azw3", ".djvu",
    ".eml", ".msg",
    ".vsdx", ".ofd",
    # 图片/照片/设计稿
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico",
    ".heic", ".heif", ".avif", ".svg",
    ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef", ".srw",
    ".psd", ".ai", ".eps",
    # 压缩包
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    # 列明排除
    ".json", ".jsonl", ".pyc", ".emf", ".bin", ".html", ".htm",
}

# 文件类型 -> emoji
EMOJI_MAP = {
    ".py": "🐍", ".pyw": "🐍", ".pyi": "🐍",
    ".js": "🟨", ".mjs": "🟨", ".cjs": "🟨",
    ".jsx": "🟨", ".ts": "🔷", ".tsx": "🔷",
    ".java": "☕", ".class": "☕", ".jar": "☕",
    ".kt": "🟣", ".kts": "🟣", ".scala": "🔴", ".groovy": "🔴",
    ".go": "🐹",
    ".rs": "🦀",
    ".c": "🔧", ".h": "🔧",
    ".cpp": "🔧", ".cc": "🔧", ".cxx": "🔧", ".hpp": "🔧",
    ".cs": "🔷",
    ".swift": "🦅",
    ".m": "🍎", ".mm": "🍎",
    ".rb": "💎",
    ".php": "🐘",
    ".lua": "🌙",
    ".pl": "🐪", ".pm": "🐪",
    ".r": "📊",
    ".dart": "🎯",
    ".sh": "🐚", ".bash": "🐚", ".zsh": "🐚", ".fish": "🐚",
    ".ps1": "💠", ".psm1": "💠", ".psd1": "💠",
    ".bat": "🪟", ".cmd": "🪟",
    ".sql": "🗃️", ".db": "🗄️", ".sqlite": "🗄️", ".sqlite3": "🗄️",
    ".yaml": "⚙️", ".yml": "⚙️",
    ".toml": "⚙️", ".ini": "⚙️", ".cfg": "⚙️", ".conf": "⚙️",
    ".properties": "⚙️",
    ".env": "🔐",
    ".xml": "📰",
    ".lock": "🔒",
    ".md": "📝", ".markdown": "📝", ".rst": "📝",
    ".txt": "📄",
    ".pdf": "📕",
    ".doc": "📘", ".docx": "📘",
    ".xls": "📗", ".xlsx": "📗", ".csv": "📊",
    ".ppt": "📙", ".pptx": "📙",
    ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️", ".gif": "🖼️",
    ".bmp": "🖼️", ".webp": "🖼️", ".ico": "🖼️", ".tif": "🖼️",
    ".svg": "🎨",
    ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵", ".m4a": "🎵", ".ogg": "🎵",
    ".mp4": "🎬", ".avi": "🎬", ".mkv": "🎬", ".mov": "🎬", ".wmv": "🎬",
    ".zip": "📦", ".rar": "📦", ".7z": "📦", ".tar": "📦", ".gz": "📦",
    ".exe": "⚡", ".msi": "⚡", ".dll": "🧩", ".so": "🧩",
    ".ttf": "🔤", ".otf": "🔤", ".woff": "🔤", ".woff2": "🔤",
    ".log": "📋",
}


def get_emoji(path: Path) -> str:
    """根据扩展名返回对应 emoji"""
    ext = path.suffix.lower()
    if ext in EMOJI_MAP:
        return EMOJI_MAP[ext]

    name = path.name.lower()
    if name in ("makefile", "dockerfile", "vagrantfile", "jenkinsfile"):
        return "🛠️"
    if name in ("license", "licence", "copying"):
        return "📜"
    if name.startswith("readme"):
        return "📖"
    if name.startswith("."):
        return "🔧"

    return "📄"


def should_skip(path: Path) -> bool:
    """判断文件是否应该跳过"""
    rel = path.relative_to(ROOT)

    for part in rel.parts[:-1]:
        if part in EXCLUDE_DIRS:
            return True

    if path.name in EXCLUDE_FILES:
        return True

    if path.suffix.lower() in EXCLUDE_EXTS:
        return True

    return False


def human_size(num_bytes: int) -> str:
    """把字节数转成人类可读的大小"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.2f} {unit}"
        size /= 1024


def get_create_time(st) -> float:
    """
    跨平台获取文件创建时间（时间戳）。

    - macOS / 部分 BSD：使用 st_birthtime（真正的创建时间）
    - Windows：st_ctime 即创建时间
    - Linux：没有 st_birthtime，只能退回 st_ctime
      （注意：Linux 下 st_ctime 是元数据变更时间，不是真正的创建时间）
    """
    birth = getattr(st, "st_birthtime", None)
    if birth is not None:
        return float(birth)
    return float(st.st_ctime)


def scan():
    """扫描根目录，返回文件列表 [(rel, size, mtime, ctime), ...]"""
    files = []

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        current = Path(dirpath)
        for name in filenames:
            p = current / name
            try:
                rel = p.relative_to(ROOT)
            except ValueError:
                continue

            if should_skip(p):
                continue

            try:
                st = p.stat()
                size = st.st_size
                mtime = st.st_mtime
                ctime = get_create_time(st)
            except OSError:
                continue

            files.append((rel, size, mtime, ctime))

    files.sort(key=lambda x: x[0].as_posix().lower())
    return files


def build_tree(files):
    """
    从文件列表构建目录树。
    只包含有文件的目录，空目录不会出现在树里。
    """
    root = {}

    for rel, size, mtime, ctime in files:
        node = root
        for part in rel.parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(
            (rel.parts[-1], rel, size, mtime, ctime)
        )

    return root


def count_dirs(node):
    """统计树中有文件的目录数量（不含根）"""
    total = 0
    for k, v in node.items():
        if k == "__files__":
            continue
        total += 1 + count_dirs(v)
    return total


def render_tree(node, lines, depth=0):
    """递归渲染目录树为 Markdown 缩进列表"""
    indent = "    " * depth

    folders = sorted(
        (k for k in node.keys() if k != "__files__"),
        key=lambda x: x.lower(),
    )
    files = sorted(node.get("__files__", []), key=lambda x: x[0].lower())

    for folder in folders:
        lines.append(f"{indent}- 📁 **{folder}/**")
        render_tree(node[folder], lines, depth + 1)

    for name, rel, size, mtime, ctime in files:
        rel_posix = rel.as_posix()
        link = quote(rel_posix)
        mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        ctime_str = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S")
        icon = get_emoji(rel)
        lines.append(
            f"{indent}- {icon} [`{name}`]({link}) — {human_size(size)}"
            f" — 修改: {mtime_str} — 创建: {ctime_str}"
        )


def generate_md():
    """扫描根目录，生成 FILE_LIST.md"""
    files = scan()

    total_size = sum(f[1] for f in files)
    tree = build_tree(files)
    dir_count = count_dirs(tree)

    lines = [
        "# 文件路径清单",
        "",
        f"> 最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        # 2026-09-26（v2 §3.15.4）：原写 `> 根目录：<绝对路径>` → 生成物**必然**含盘符字面量，
        # 一旦入库即触发 gate_hardcoded_paths（R4，实测 FILE_LIST.md:4 自 2026-09-24 起 FAIL）。
        # 生成物已按派生产物 gitignore；此处进一步改为仓库相对表述，使"若将来入库"也不再违规。
        "> 根目录：`.`（仓库根；下列路径均为仓库相对路径，不含盘符）",
        f"> 文件夹总数：**{dir_count}**",
        f"> 文件总数：**{len(files)}**",
        f"> 总大小：**{human_size(total_size)}**",
        "",
        "## 目录结构",
        "",
    ]

    render_tree(tree, lines, depth=0)
    lines.append("")

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"已更新 {OUTPUT.name}，文件夹 {dir_count} 个，"
        f"文件 {len(files)} 个，总大小 {human_size(total_size)}"
    )


if __name__ == "__main__":
    generate_md()
