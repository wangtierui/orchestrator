# -*- coding: utf-8 -*-
"""本地 Office 旧格式 → docx 转换层（移植自 std_lib.scraper_std.doc_convert）。

零网络、纯本地；转换失败/环境缺失一律返回 None（由调用方降级）。
"""
import os
import shutil
import subprocess
import tempfile


def _windows_lo_candidates() -> list[str]:
    out = []
    for var in ("ProgramFiles", "PROGRAMFILES"):
        pf = os.environ.get(var, "").strip()
        if pf:
            out.append(os.path.join(pf, "LibreOffice", "program", "soffice.exe"))
    for var in ("ProgramFiles(x86)", "PROGRAMFILES(X86)"):
        pf = os.environ.get(var, "").strip()
        if pf:
            out.append(os.path.join(pf, "LibreOffice", "program", "soffice.exe"))
    return out


def find_libreoffice(bin: str | None = None) -> str | None:
    """探测 LibreOffice(headless) 可执行；找不到返回 None。"""
    if bin and os.path.exists(bin):
        return bin
    env_bin = (bin or os.environ.get("LO_BIN") or "").strip()
    if env_bin and os.path.exists(env_bin):
        return env_bin
    for c in ("soffice", "libreoffice"):
        p = shutil.which(c)
        if p:
            return p
    for cand in _windows_lo_candidates():
        if os.path.exists(cand):
            return cand
    for cand in (
        "/c/Program Files/LibreOffice/program/soffice.exe",
        "/c/Program Files (x86)/LibreOffice/program/soffice.exe",
    ):
        if os.path.exists(cand):
            return cand
    return None


def doc_to_docx(doc_path: str, *, bin: str | None = None, timeout: int = 120) -> str | None:
    """headless 将 .doc 等旧格式单文件转为 docx（输出同目录），返回新路径或 None。"""
    lo = find_libreoffice(bin)
    if not lo or not os.path.exists(doc_path):
        return None
    out_dir = os.path.dirname(os.path.abspath(doc_path))
    try:
        subprocess.run(
            [lo, "--headless", "--convert-to", "docx", "--outdir", out_dir, doc_path],
            check=False, capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    base = os.path.splitext(os.path.basename(doc_path))[0]
    conv = os.path.join(out_dir, base + ".docx")
    return conv if os.path.exists(conv) else None


def doc_bytes_to_docx(
    data: bytes, name: str = "", *, bin: str | None = None, timeout: int = 120
) -> bytes | None:
    """bytes → 临时 .doc → 转 docx → 读回 bytes → 清理。失败返回 None。"""
    if not data:
        return None
    tmp_dir = tempfile.mkdtemp(prefix="doc_convert_")
    try:
        ext = os.path.splitext(name or ".doc")[1].lower() or ".doc"
        src = os.path.join(tmp_dir, "input" + ext)
        with open(src, "wb") as fh:
            fh.write(data)
        conv = doc_to_docx(src, bin=bin, timeout=timeout)
        if not conv:
            return None
        with open(conv, "rb") as fh:
            return fh.read()
    except Exception:  # noqa: BLE001
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
