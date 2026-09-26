# -*- coding: utf-8 -*-
"""
encoding.py —— 编码自动检测与转换（第八节）

规范要求：
  - 禁止硬编码解码格式（如 .decode('utf-8')）；
  - 使用 chardet 对响应原始字节检测编码，置信度 > 0.9 直接采用；
  - 置信度不足时降级链：gb18030 → utf-8（带 errors='replace' 兜底）；
  - 最终输出文件统一 UTF-8（with BOM，兼容 Excel）。

实现：
  - detect_encoding(data) -> str         ：检测编码名
  - decode_bytes(data, hint=None) -> str ：字节 → 文本（含乱码比例标注）
  - ensure_utf8_bom(text) -> bytes       ：文本 → UTF-8 with BOM 字节
  - write_csv_utf8_bom(path, text)       ：便捷写文件
"""

from __future__ import annotations

import re

# 可控的候选降级链（覆盖中文政务网站全部常见编码）
_FALLBACK_CHAIN = ("gb18030", "utf-8")
# 常见误检规避：chardet 对短文本常误报 ISO-8859-1 / windows-1252
_SUSPECT_LATIN = re.compile(r"[\u4e00-\u9fff]")
_MISDETECT = {"ISO-8859-1", "windows-1252", "ascii", "MacRoman"}


def _chardet_detect(data: bytes) -> str | None:
    """惰性导入 chardet；缺失返回 None（不抛异常）。"""
    try:
        import chardet  # type: ignore
    except Exception:
        return None
    try:
        res = chardet.detect(data)
        if res and res.get("encoding"):
            return res["encoding"]
    except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
        pass
    return None


def _confidence(data: bytes, enc: str) -> float:
    """基于 chardet 的置信度；库缺失时为 0.0（走降级链）。"""
    try:
        import chardet  # type: ignore
    except Exception:
        return 0.0
    try:
        res = chardet.detect(data)
        if res and res.get("encoding") == enc:
            return float(res.get("confidence") or 0.0)
    except Exception:  # noqa: BLE001  旁路设施/缓存降级（主路径不受影响）
        pass
    return 0.0


def detect_encoding(data: bytes, hint: str | None = None) -> str:
    """
    返回推荐的解码编码名。

    决策链：
      1. 显式 hint（如 HTTP 头 charset）且可解码 → 采用；
      2. chardet 检测且置信度 > 0.9 → 采用；
      3. chardet 检测到中文文本但误报拉丁系 → 修正为 gb18030；
      4. 降级链 gb18030 → utf-8。
    """
    if not data:
        return "utf-8"
    # 1) 显式 hint 优先（可解码性校验）
    if hint:
        enc = hint.strip().lower()
        if enc in ("utf-8", "utf8"):
            try:
                data.decode("utf-8")
                return "utf-8"
            except (UnicodeDecodeError, LookupError):
                pass
        elif enc and enc not in ("none", ""):
            try:
                data.decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                pass
    # 2/3) chardet
    det = _chardet_detect(data)
    if det:
        enc = det.upper()
        conf = _confidence(data, det)
        if conf > 0.9:
            return det
        # 误报规避：检测到中文但编码是拉丁系 → 几乎必为 gb 系
        if enc in _MISDETECT and _SUSPECT_LATIN.search(data.decode("latin-1", "ignore")):
            return "gb18030"
        if enc.startswith("GB") or enc in ("BIG5", "BIG5-HKSCS"):
            return "gb18030"
    # 4) 降级链
    #   注意：GB18030 是超集编码，几乎可解码任意字节序列——若 gb18030 在前，
    #   会把 UTF-8 字节静默解成乱码。因此 utf-8 严格解码成功即为权威（规范
    #   "gb18030→utf-8" 的次序在 chardet 置信度门槛存在时由第 2 步满足；此处
    #   仅在 chardet 缺失/低置信时生效，采用 utf-8 优先的防乱码次序）。
    try:
        data.decode("utf-8")
        return "utf-8"
    except (UnicodeDecodeError, LookupError):
        pass
    for enc in _FALLBACK_CHAIN:
        if enc == "utf-8":
            continue
        try:
            data.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


def decode_bytes(data: bytes, hint: str | None = None) -> tuple[str, str, float]:
    """
    字节 → 文本。返回 (text, encoding_used, garble_ratio)。
    garble_ratio：U+FFFD 替换字符占比（>0.01 视为疑似乱码）。
    """
    if not data:
        return "", "utf-8", 0.0
    enc = detect_encoding(data, hint)
    try:
        text = data.decode(enc, errors="replace")
    except LookupError:
        enc = "utf-8"
        text = data.decode("utf-8", errors="replace")
    garble = text.count("\ufffd") / max(1, len(text))
    return text, enc, garble


def ensure_utf8_bom(text: str) -> bytes:
    """统一输出编码：UTF-8 with BOM（兼容 Excel 打开中文不乱码）。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    return ("\ufeff" + text).encode("utf-8")


def write_text_utf8_bom(path: str, text: str) -> None:
    """便捷写文件（UTF-8 with BOM）。"""
    import os

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(ensure_utf8_bom(text))


if __name__ == "__main__":  # 离线自检
    assert decode_bytes("中文测试".encode())[1] in ("utf-8", "utf8")
    gb = "财政法规".encode("gb18030")
    t, enc, _ = decode_bytes(gb)
    assert t == "财政法规", (t, enc)
    assert decode_bytes(b"\xff\xfe\x00\x41")[0].startswith("\ufffd")  # 损坏字节不崩溃
    assert ensure_utf8_bom("abc").startswith(b"\xef\xbb\xbf")
    print("[scraper_std.encoding] 离线自检通过")
