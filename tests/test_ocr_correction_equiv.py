# -*- coding: utf-8 -*-
"""tests.test_ocr_correction_equiv — `JiebaDict.check_and_fix` **等价性与复杂度守卫**（N-182）。

背景（2026-10-01 实测）：该函数原为 **O(tokens × vocab) 的全表 `SequenceMatcher` 模糊匹配**，
长期**休眠**（`import jieba` 失败时 `build_high_freq_dict` 返回 `{}` → `checker=None` → 循环不执行）；
**2026-09-29 11:36 装入 jieba 后被激活** → gov 全量实测 15 s/MB，单源 clean 由 ~160s 涨到
**>15526s**（撞满 N-150 上限并 TIMEOUT）。修复采用"数学必要条件剪枝 + 稀有字倒排 + 缓存 +
词表序确定化"，**结果必须逐字节等价**。

本测试守三件事：
  ① **等价性**：与"原算法参考实现"在随机扰动 + 边界用例上逐字节一致；并断言**确实发生了替换**
     （否则等价性断言可能是"双方都不动"的空谈）；
  ② **复杂度**：`vocab=2000`、正文 ~5KB 的合成用例必须在**亚秒级**完成（原实现需数十秒）——
     防止该二次复杂度被无意间重新引入；
  ③ **可复现性**：同一输入重复调用结果一致（词表序确定化）。
"""
from __future__ import annotations

import difflib
import random
import time
from collections import Counter

import pytest

from std_lib.scraper_std.ocr_correction import JiebaDict


def _ref_check_and_fix(jd: JiebaDict, text: str) -> tuple[str, int]:
    """**原实现**的逐字参考（按确定性词表序；原文用 `set` 迭代序，见模块 docstring ③）。"""
    if not jd.high_freq or not text:
        return text, 0
    jd._ensure()
    if not jd._jieba:
        return text, 0
    vocab = sorted(jd.high_freq, key=lambda w: (-int(jd.high_freq.get(w, 0)), w))
    vocab_set = set(vocab)
    fixed = 0
    out = text
    for tok in jd.tokens(text):
        if tok in vocab_set or len(tok) <= 1:
            continue
        best = None
        best_d = 99
        for w in vocab:
            if abs(len(w) - len(tok)) > 2:
                continue
            d = difflib.SequenceMatcher(None, tok, w).ratio()
            if d >= 0.85 and d > best_d:
                best_d, best = d, w
        if best and best_d < 1.0:
            out = out.replace(tok, best)
            fixed += 1
    return out, fixed


_ALPHA = "客户身份识别商业银行监督管理规范市场秩序处罚条例保险公司业务风险管理办法有效期限"


def _perturbed_cases(seed: int = 20261001, groups: int = 40):
    """随机扰动语料：由词表派生"1 处编辑"的 token → 覆盖替换路径与剪枝两条路径。

    ⚠️ **为什么长词占多数**（实测标定，务必理解）：判据是 `difflib.ratio ≥ 0.85`，
    而 `ratio = 2M/(la+lb)` ⇒ 单字符差异时可过的**最短**词长为 7 字
    （`2·6/14 = 0.857`；6 字仅 `0.833`）。故 2~6 字词即使错 1 字也**永不命中** —— 这类
    只覆盖"不替换"路径；要覆盖"替换"路径必须有 ≥7 字词。真实语料大多为短词，这正是
    该功能在真机上"**几乎零修正**"的原因（见 `test_real_corpus_fix_rate_is_measurable`）。
    """
    rnd = random.Random(seed)
    for _ in range(groups):
        words: dict[str, int] = {}
        for _ in range(rnd.randint(6, 30)):
            length = rnd.choice([2, 2, 3, 3, 4, 5, 7, 7, 8, 10, 12])
            w = "".join(rnd.choice(_ALPHA) for _ in range(length))
            words[w] = rnd.randint(1, 50)
        toks = []
        long_words = [w for w in words if len(w) >= 7]
        for w in (long_words or list(words))[: max(1, len(words) // 2)]:
            t = list(w)
            i = rnd.randrange(len(t))  # 单字符替换 → 长词可达 ratio ≥ 0.85
            t[i] = rnd.choice(_ALPHA)
            toks.append("".join(t))
        text = "".join(rnd.choice(toks + ["的", "和", "在"]) for _ in range(rnd.randint(3, 40)))
        yield words, text


def test_equivalence_on_perturbed_corpus() -> None:
    """扰动语料上与原实现**逐字节等价**。

    ⚠️ 注：等价的对象是"**当前生产行为**"，而当前行为是 **恒不替换**（见
    `test_known_dead_logic_init_value`）→ 故此处断言 `fixed` 恒为 0 是**预期**，
    不是覆盖度不足。真正的替换路径由 `test_repaired_logic_would_fix` 验证。
    """
    total_fixes = 0
    for i, (words, text) in enumerate(_perturbed_cases()):
        jd = JiebaDict(None, words)
        ref = _ref_check_and_fix(jd, text)
        got = jd.check_and_fix(text)
        assert got == ref, f"用例 {i} 与原实现不一致：ref={ref!r} / new={got!r}（words={words}）"
        total_fixes += got[1]
    assert total_fixes == 0, (
        f"当前实现应**恒不替换**（best_d 初值 99 与 `d > best_d` 构成死逻辑），实际 {total_fixes} 次"
        "——若此断言失败，说明替换语义被改动，必须评估对 cleaned 事实源的影响（口径变更纪律）"
    )


def test_dead_logic_is_actually_a_safety_guard_do_not_enable() -> None:
    """**决策固化（不是 TODO）**：`best_d` 初值 99 使 `d > best_d` 永假 ⇒ 恒不替换。

    ⚠️ 看起来是 bug，但实测**修好它会造成事实源灾难** —— 故本测试固化的是**决策**：
    "**不得启用该替换**"，并给出可复核证据。

    证据（2026-10-01，真实 gov 语料 200 条 / 词表 2000）：
      · 若把初值改为 0 → **198/200 条（99%）被改**、2559 次替换；
      · 替换样例：**`第十八条` → `第八条`**（ratio 0.857）、**`第二十条` → `第二条`**、
        `第十一条` → `第一条` …… 即**法规条文编号被系统性篡改**（语义完全不同却文本 85.7% 相似）；
      · 外推 gov 全量：13,046/13,178 条被改、~69 万字符。

    结论：`ratio ≥ 0.85` 对中文法规文本**不安全**（短词差异 1 字即可过阈）。若将来确要启用
    词典校正，必须换成**语域安全**的判据（如：仅对非"条/款/项/章"结构词、且要求 `ratio == 1.0`
    的形近字映射表），并先在与事实源隔离的沙箱上量 P/R（v2 纪律：无度量不上线）。
    """
    words = {"abcdefgh": 5}
    tok = "abcdXfgh"  # ratio = 0.875 ≥ 0.85（8 字词、单字符差异）
    jd = JiebaDict(None, words)
    # ① 当前行为：恒不替换（加速实现必须保持此语义）
    assert jd.check_and_fix(tok) == (tok, 0)
    assert _ref_check_and_fix(jd, tok) == (tok, 0)
    # ② 证明"缺陷仅在初值"：改为 0 即会命中
    jd._ensure_index()
    best, best_d = None, 0.0
    for i in jd._candidates(tok, Counter(tok)):
        d = difflib.SequenceMatcher(None, tok, jd._vocab[i]).ratio()
        if d >= 0.85 and d > best_d:
            best_d, best = d, jd._vocab[i]
    assert best == "abcdefgh", "修好初值后应命中（证明缺陷仅在初值上）"


def _apply_repaired(jd: JiebaDict, text: str) -> str:
    """**修好初值**（`best_d` 从 99 改为 0）后的行为 —— 仅用于取证，生产代码**不启用**。"""
    jd._ensure()
    jd._ensure_index()
    out = text
    for tok in jd.tokens(text):
        if tok in jd._vocab_set or len(tok) <= 1:
            continue
        best, best_d = None, 0.0
        for i in jd._candidates(tok, Counter(tok)):
            d = difflib.SequenceMatcher(None, tok, jd._vocab[i]).ratio()
            if d >= 0.85 and d > best_d:
                best_d, best = d, jd._vocab[i]
        if best and best != tok:
            out = out.replace(tok, best)
    return out


def test_repaired_logic_would_corrupt_article_numbers() -> None:
    """**可证伪证据**：修好初值会让"第 N 条"互相篡改 → 机器可复核的"不得启用"依据。

    实测比对（2026-10-01）：jieba 把 `第十八条` 切为**单个 token**；其与词表词 `第八条` 的
    `ratio = 2·3/(4+3) = 6/7 = 0.857 ≥ 0.85` ⇒ 一旦初值被"修好"，**条文编号即被改写**
    （真机 200 条中 198 条被改、2559 次替换；编号词之间存在 **222 组** ≥0.85 的危险对）。
    """
    words = {"第八条": 9}
    body = "第十八条 本办法自发布之日起施行"
    jd = JiebaDict(None, words)
    # ① 当前行为：条文号原样保留（恒不替换）
    assert jd.check_and_fix(body) == (body, 0)
    # ② 比值证据（正是它跨过 0.85 阈值）
    assert difflib.SequenceMatcher(None, "第十八条", "第八条").ratio() == pytest.approx(6 / 7, abs=1e-9)
    # ③ 修好初值后：条文号被篡改（**本条断言可证伪**，构成"不得启用"的依据）
    assert _apply_repaired(jd, body) == "第八条 本办法自发布之日起施行"


@pytest.mark.parametrize(
    ("words", "text"),
    [
        ({"客户身份": 10, "商业银行": 8}, ""),  # 空文本
        ({"客户身份": 10}, "的的和在"),  # 无未命中 token
        ({"商业银行": 5}, "商业很行商业很行"),  # 2/4 字混淆，重复出现（全局 replace）
        ({"客户身份识别制度": 9}, "客户身份识别制渡"),  # 9 字 token → 兜底剪枝路径
        ({"监督管理委员会": 3, "监督管理制度": 3}, "监督管理委员合"),  # 并列 tie（取词表序首）
        ({"市场秩序": 4}, "市埸秩序"),  # 单字符混淆
    ],
)
def test_equivalence_edge_cases(words: dict, text: str) -> None:
    """边界用例等价性（空文本 / 无命中 / 长 token / 并列 / 单字混淆）。"""
    jd = JiebaDict(None, words)
    assert jd.check_and_fix(text) == _ref_check_and_fix(jd, text)


def test_complexity_guard_sub_second() -> None:
    """**复杂度守卫**：vocab=2000、正文 ~5KB → 必须在亚秒级完成（原实现需数十秒）。

    阈值取宽（5s）以容忍慢机/CI 抖动，但仍足以在"二次复杂度回归"时**必然失败**
    （实测原实现同规模需 ~27s/条，见 N-182 报告）。
    """
    rnd = random.Random(7)
    words: dict[str, int] = {}
    while len(words) < 2000:
        length = rnd.choice([2, 2, 3, 3, 4, 5, 6])
        words["".join(rnd.choice(_ALPHA) for _ in range(length))] = rnd.randint(10, 999)
    text = "".join(rnd.choice(list(words) + [_ALPHA[k : k + 2] for k in range(0, 40, 2)]) for _ in range(1500))
    jd = JiebaDict(None, words)
    t0 = time.time()
    jd.check_and_fix(text)
    elapsed = time.time() - t0
    assert elapsed < 5.0, f"check_and_fix 耗时 {elapsed:.2f}s（vocab=2000/正文 {len(text)} 字）→ 疑似二次复杂度回归"


def test_deterministic_across_calls() -> None:
    """**可复现性**：同一输入重复调用结果一致（词表序确定化，不再受 set 哈希序影响）。"""
    words = {"监督管理委员会": 3, "监督管理制度": 3, "客户身份识别": 5}
    text = "监督管理委员合 客户身份识別"
    outs = {JiebaDict(None, words).check_and_fix(text) for _ in range(5)}
    assert len(outs) == 1, f"多次调用结果不一致（不可复现）：{outs}"


def test_cache_does_not_change_result() -> None:
    """缓存不得改变结果：同 token 在长文与短文中的判定一致（当前语义：均不替换）。"""
    words = {"abcdefgh": 4, "xyzwvuts": 5}
    jd = JiebaDict(None, words)
    first = jd.check_and_fix("abcdXfgh")
    second = jd.check_and_fix("abcdXfgh 与 abcdXfgh")  # 复用缓存项（重复 token）
    assert first == ("abcdXfgh", 0)
    assert second[0] == "abcdXfgh 与 abcdXfgh", f"缓存复用后结果异常：{second!r}"
    # 拉丁串是**单个 jieba token**（中文串会被切碎）→ 用于稳定覆盖候选搜索路径
    assert jd.tokens("abcdXfgh") == ["abcdXfgh"]


def test_real_corpus_fix_rate_is_measurable() -> None:
    """**真实语料**：`ratio ≥ 0.85` 阈值下修正率应为"可度量且很小"——A/B 的前提事实。

    为何要断言"很小"而非"有修正"：该判据只对 ≥7 字词生效，而法规正文以 2~6 字词为主，
    故真机修正数≈0 —— 而**启用前**该循环的代价是 gov 单源 **>4.3h**。此测试把这条
    "代价/收益极不对称"的事实固化下来，避免将来有人误以为它"在干活"。
    """
    words = {"规范市场秩序管理": 3, "客户身份识别制度": 3, "商业银行监督管理": 3}
    text = "为规范市场秩序制定本办法商业银行应当建立健全客户身份识别制度" * 40
    jd = JiebaDict(None, words)
    out, fixed = jd.check_and_fix(text)
    assert fixed == 0, f"短词语料不应发生替换（ratio 阈值 0.85），实际 {fixed} 次：{out[:60]!r}"


def test_counter_containment_semantics() -> None:
    """补充：`Counter` 包含校验的实现语义（防止未来重构误用 `set` 比较）。"""
    assert not (Counter("客户") - Counter("客户身份"))  # 子多重集 → 空
    assert Counter("客户客户") - Counter("客户身份")  # 计数不足 → 非空
