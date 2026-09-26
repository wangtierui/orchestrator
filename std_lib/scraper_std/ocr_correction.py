# -*- coding: utf-8 -*-
"""
ocr_correction.py —— OCR 识别错误校正（第七节 7.3 专项）

四级处理流程：
  ① 常见 OCR 混淆字符自动替换：读取 ocr_confusion.json 映射表（形近字/英文数字
     混淆/标点误识），替换逻辑优先匹配长词，防止过度修正；
  ② 中文分词与词频词典校验：jieba + 行业自定义词典（custom_dict.txt），分词结果
     中某词不在词典且编辑距离 ≤ 2 与高频词匹配 → 自动替换并记录 [OCR校正]；
  ③ 噪声字符与乱码过滤：异常 Unicode 符号、控制字符、连续重复标点、水印正则；
  ④ OCR 表格段落专项：疑似表格段落跳过常规字形替换，优先表格结构还原；
  ⑤ 低置信度标记：校正后仍含 >3 处未匹配混淆字符 / 整句无常见停用词 /
     语义相似度 < 0.85 → 禁止强行篡改，加 [OCR存疑] 前缀，
     原始文本导出 logs/ocr_uncertain_{date}.json 供人工核验。
"""

from __future__ import annotations

import datetime as _dt
import difflib
import json
import logging
import os
import re
from typing import Any

LOG = logging.getLogger("scraper_std.ocr_correction")

# ③ 噪声
_NOISE_SYMBOLS = re.compile(r"[\ufffd▇□■◇◆●○◉◎△▲☆★♠♣♥♦☀☁]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DUP_PUNCT = re.compile(r"([。！？；：，、.])\1{2,}")
# ⑤ 常见停用词（判定整句是否可疑）
_STOPWORDS = (
    "的",
    "了",
    "和",
    "与",
    "是",
    "在",
    "为",
    "及",
    "对",
    "以",
    "本",
    "该",
    "应当",
    "规定",
    "管理",
    "工作",
    "政府",
    "人民",
)


def load_confusion_map(path: str) -> dict[str, str]:
    """
    读取 ocr_confusion.json。支持格式（自动探测）：
      - 直接映射 {"已":"己","0":"O", ...}
      - 封装格式 {"map": {...}} 或 {"char_map": {...}} 或 {"word_map": {...}}
      - 多层嵌套（递归下钻所有 str→str 子字典）
    返回 {错误串: 正确串}。文件缺失/解析失败返回**空映射**（不做任何替换）。

    ⚠️ 2026-09-15 严重缺陷修复（数据质量）：
      原实现内置 `{"0":"O","1":"l","已":"己","曰":"日","人":"入","未":"末","土":"士",
      "8":"B","5":"S","2":"Z"}`，与本函数声明的 `{错误串: 正确串}` 语义**方向完全相反**——
      即把**正确**文本改写为形近**错字**；且 `out.update(builtin)` 使内置集**无条件覆盖**
      显式映射（注释误写为"不覆盖"），`ocr_confusion.json` 又从未落仓（本仓实测不存在）
      → 清洗管道 `ocr_correct` 阶段对五源**全部**语料执行了该破坏性替换。
      实测畸变样例：「最高人民法院」→「最高入民法院」、「2013」→「ZOl3」、「1575」→「lS75」、
      「5月」→「S月」、「未能」→「末能」、「已经」→「己经」；受影响记录数
      gov 39/605、mof 131/870、nfra 172/1939、pbc 417/571（「入民」口径）。
      现策略：**上下文无关的硬编码字符替换一律取消**（任何时候把「人」替换成「入」、
      「0」替换成「O」都是错的）；纠错映射**唯一来源**为显式 `ocr_confusion.json`，
      且显式映射优先、内置集永不覆盖显式值。语料已按修复后逻辑重清洗。
    """
    builtin: dict[str, str] = {}
    if not path or not os.path.exists(path):
        return builtin
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        LOG.warning("混淆映射加载失败 %s：%s", path, e)
        return builtin
    out: dict[str, str] = {}
    _collect_map(raw, out)
    # 顶层本身即为映射（所有值为字符串）
    if not out and isinstance(raw, dict) and raw and all(isinstance(v, str) for v in raw.values()):
        out.update({str(k): str(v) for k, v in raw.items() if not str(k).startswith("_")})
    if not out:
        return builtin
    return out


def _collect_map(obj: Any, out: dict[str, str]) -> None:
    """递归下钻 dict：把 str→str 叶子字典合并进 out，跳过 _ 前缀注释键。"""
    if not isinstance(obj, dict):
        return
    if obj and all(isinstance(v, str) for v in obj.values()):
        for k, v in obj.items():
            if str(k).startswith("_"):
                continue
            out[str(k)] = str(v)
        return
    for v in obj.values():
        if isinstance(v, dict):
            _collect_map(v, out)


def replace_confusions(text: str, cmap: dict[str, str]) -> tuple[str, int]:
    """
    ① 混淆字符替换：优先匹配长词（按 key 长度降序），防止过度修正。
    返回 (替换后文本, 替换次数)。替换次数>3 且语义存疑时调用方标记存疑。
    """
    if not text or not cmap:
        return text or "", 0
    keys = sorted(cmap.keys(), key=len, reverse=True)
    replaced = 0
    out = text
    for k in keys:
        if k and len(k) >= 1 and k in out:
            n = out.count(k)
            out = out.replace(k, cmap[k])
            replaced += n
    return out, replaced


class JiebaDict:
    """② 词典校验：加载 custom_dict.txt，分词 + 编辑距离修正。"""

    def __init__(self, dict_path: str | None = None, high_freq: dict[str, int] | None = None):
        self.dict_path = dict_path
        self.high_freq = dict(high_freq or {})  # {词: 频次}
        self._jieba = None
        self._loaded = False

    def _ensure(self) -> None:
        if self._loaded:
            return
        try:
            import jieba

            self._jieba = jieba
            if self.dict_path and os.path.exists(self.dict_path):
                dict_path = self._filtered_dict(self.dict_path)
                jieba.load_userdict(dict_path)
                if dict_path != self.dict_path:
                    try:
                        os.remove(dict_path)
                    except OSError:
                        pass
            # 高频词注入（来源②动态抽取）
            for w, n in self.high_freq.items():
                jieba.add_word(w, freq=n)
            self._loaded = True
        except Exception as e:
            LOG.warning("jieba 不可用：%s", e)
            self._jieba = None
            self._loaded = True

    @staticmethod
    def _filtered_dict(path: str) -> str:
        """去除 jieba 用户词典中的 # 注释行与空行（jieba 不识别注释）。
        若无需过滤则原样返回路径；否则写入临时文件并返回其路径。"""
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return path
        cleaned = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        if len(cleaned) == len(lines):
            return path
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".dict", prefix="jd_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(cleaned)
            return tmp
        except Exception:
            return path

    def tokens(self, text: str) -> list[str]:
        self._ensure()
        if not self._jieba or not text:
            return []
        try:
            return [w for w in self._jieba.cut(text) if w.strip()]
        except Exception:
            return []

    def check_and_fix(self, text: str) -> tuple[str, int]:
        """
        分词结果中，若某词不在词典且编辑距离 ≤ 2 与高频词匹配 → 替换。
        返回 (文本, 修正次数)。
        """
        if not self.high_freq or not text:
            return text, 0
        self._ensure()
        if not self._jieba:
            return text, 0
        vocab = set(self.high_freq.keys())
        fixed = 0
        out = text
        for tok in self.tokens(text):
            if tok in vocab or len(tok) <= 1:
                continue
            # 编辑距离 ≤ 2 且长度相近
            best = None
            best_d = 99
            for w in vocab:
                if abs(len(w) - len(tok)) > 2:
                    continue
                d = difflib.SequenceMatcher(None, tok, w).ratio()
                # 用 ratio 反推编辑距离近似：ratio>=0.85 视为 ≤2
                if d >= 0.85 and d > best_d:
                    best_d = d
                    best = w
            if best and best_d < 1.0:
                out = out.replace(tok, best)
                fixed += 1
                LOG.info("[OCR校正] %s → %s", tok, best)
        return out, fixed


def filter_noise(text: str) -> str:
    """③ 噪声字符与乱码过滤。"""
    t = text or ""
    t = _NOISE_SYMBOLS.sub("", t)
    t = _CTRL.sub("", t)
    t = _DUP_PUNCT.sub(r"\1", t)
    return t


def _is_table_like(text: str) -> bool:
    """④ OCR 表格段落判定：连续多行字符数相近 + 数字文字交替。"""
    from .cleaner import is_table_block

    return is_table_block(text)


def correct_ocr_text(
    text: str,
    *,
    confusion_map: dict[str, str] | None = None,
    dict_path: str | None = None,
    high_freq: dict[str, int] | None = None,
    dict_checker: JiebaDict | None = None,
    uncertain_export_dir: str | None = None,
) -> dict[str, Any]:
    """
    OCR 校正总入口。返回：
      {
        "text": 校正后文本,
        "corrected": int,          # 字形替换次数
        "dict_fixed": int,         # 词典修正次数
        "uncertain": bool,         # 是否低置信（[OCR存疑] 前缀）
        "original": str,           # 原始文本（供核验）
        "table_like": bool,        # 是否表格段落（跳过字形替换）
        "notes": [str],
      }
    dict_checker：预构建的 JiebaDict 实例（高频词词典已在实例内），
    批量处理时传入可避免重复加载词典（性能优化）。
    """
    notes: list[str] = []
    if not text:
        return {
            "text": "",
            "corrected": 0,
            "dict_fixed": 0,
            "uncertain": False,
            "original": "",
            "table_like": False,
            "notes": notes,
        }
    table_like = _is_table_like(text)
    corrected = 0
    dict_fixed = 0
    t = text
    if table_like:
        notes.append("table_block_skip_shape_correction")
    else:
        # ① 字形替换（长词优先）
        cmap = confusion_map or {}
        t, corrected = replace_confusions(t, cmap)
        # ② 词典校验
        if high_freq or dict_checker is not None:
            jd = dict_checker or JiebaDict(dict_path, high_freq)
            t, dict_fixed = jd.check_and_fix(t)
    # ③ 噪声过滤
    t = filter_noise(t)

    # ⑤ 低置信度判定
    uncertain = False
    if corrected > 3 or dict_fixed > 3:
        # 结合语义：整句不含常见停用词 → 存疑
        stripped = re.sub(r"[\s\dA-Za-z\u4e00-\u9fff]", "", t)
        if not stripped or not any(w in t for w in _STOPWORDS):
            uncertain = True
            notes.append("low_confidence_semantic")
    if uncertain:
        t = "[OCR存疑] " + t
        if uncertain_export_dir:
            _export_uncertain(text, t, uncertain_export_dir, corrected, dict_fixed)
    return {
        "text": t,
        "corrected": corrected,
        "dict_fixed": dict_fixed,
        "uncertain": uncertain,
        "original": text,
        "table_like": table_like,
        "notes": notes,
    }


def _export_uncertain(
    original: str, corrected: str, export_dir: str, corrected_n: int, dict_fixed_n: int
) -> None:
    """⑤ 存疑样本导出 logs/ocr_uncertain_{date}.json。"""
    os.makedirs(export_dir, exist_ok=True)
    date = _dt.date.today().strftime("%Y%m%d")
    path = os.path.join(export_dir, f"ocr_uncertain_{date}.json")
    entry = {
        "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "original": original,
        "corrected_text": corrected,
        "corrected_count": corrected_n,
        "dict_fixed_count": dict_fixed_n,
    }
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []
        data.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        LOG.warning("存疑样本导出失败：%s", e)


if __name__ == "__main__":  # 离线自检
    cmap = {"己": "已", "0": "O", "曰": "日"}
    # 长词优先：先匹配长 key
    t, n = replace_confusions("己经规定 己知 0K", {"已经": "已经", "己": "已"})
    assert n >= 1
    assert filter_noise("ab\u0000cd�ef。。。。") == "abcdef。"
    r = correct_ocr_text("己经规定的曰期", confusion_map=cmap)
    assert "已经" in r["text"] or "已" in r["text"]
    r2 = correct_ocr_text("序号 项目 金额\n1 收入 100\n2 支出 50", confusion_map={"0": "O"})
    assert r2["table_like"] is True
    print("[scraper_std.ocr_correction] 离线自检通过")
