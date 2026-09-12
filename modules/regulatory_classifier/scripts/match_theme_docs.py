# -*- coding: utf-8 -*-
"""
match_theme_docs.py — 五库正文匹配 + 条款级引用分析工具（通用化沉淀版）

将"五库匹配三级判据 + 条款级引用分析"方法论固化为可复用工具。
数据源：regulatory_scrapers 五库清洗 JSONL（nfra/pbc/mof/gov/supp）。

匹配三级判据（顺序执行，取正文最长者）：
  ① 标题归一化精确（去括号尾注/书名号/空白）
  ② 发文字号归一化相等（去括号 + rstrip('号')，防"136号"vs"136"误剔）
  ③ 标题双向包含（长度≥6）+ 文号佐证（目标无文号时放宽）

条款级引用分析：
  - basis：全文范围提取"根据/依据/依照/按照《X》"（取第一条立法依据段，全部《X》去重）
  - art_refs：《X》第Y条第Z款第W项 模式计数
  - name_refs_top：全书名号引用 TOP8

用法：
  python match_theme_docs.py --input <final.json> --output <matched.json> --citerefs <citerefs.json>
  python match_theme_docs.py --input _t1_258_final.json --output _t1_258_matched.json --citerefs _t1_258_citerefs.json
  python match_theme_docs.py --input _3_final.json --libs nfra,pbc,supp --min-body 50

说明：
  - final.json 字段兼容两种：T1/T2（year_reported/eff_status/real_year）与 T3-T8（year/eff）
  - 输出 matched/citerefs 与既有分析格式一致（lib/title/docno/body_len/body/url；basis/art_refs/name_refs_top）
"""
import argparse
import collections
import json
import os
import re
import sys

# ============ 默认五库路径（可按 --lib-config 覆盖；动态取 clean_index latest） ============
# P4（2026-09-08）：同仓注入（R4/Q3）——取消盘符。模块位于 modules/regulatory_classifier/scripts
_THIS = os.path.dirname(os.path.abspath(__file__))           # modules/regulatory_classifier/scripts
_MOD_CLASS = os.path.dirname(_THIS)                          # modules/regulatory_classifier
_SCRAPERS_MOD = os.path.join(os.path.dirname(_MOD_CLASS), "regulatory_scrapers")
_ORCH_ROOT = os.path.dirname(os.path.dirname(_MOD_CLASS))
for _p in (_MOD_CLASS, _SCRAPERS_MOD, _ORCH_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from clean_index import get_clean_index

_idx = get_clean_index()
DEFAULT_LIBS = {src: _idx.latest_jsonl_path(src) for src in ("nfra", "pbc", "mof", "gov", "supp")}


# 标题归一（与 rfn 唯一实现同语义：去括号尾注/书名号/空白）。不做本地 def（专项三）
from rfn import _norm_title as norm_title  # noqa: E402


def norm_doc(d):
    """发文字号归一化：去括号、空白，rstrip('号') 防"136号"vs"136"误剔。"""
    d = re.sub(r"[〔\[\]（）()〕\s]", "", d or "")
    return d.rstrip("号")


def get_year(r):
    """兼容两种 final 字段结构：real_year（T1/T2）或 year/文号解析（T3-T8）。"""
    y = r.get("real_year")
    if y:
        return y
    y = r.get("year", "")
    if y and y.isdigit():
        return int(y)
    m = re.search(r"[〔\[](\d{4})[〕\]]", r.get("doc_no", "") or "")
    return int(m.group(1)) if m else None


def extract_basis(body):
    """全文范围提取立法依据：'根据/依据/依照/按照'后 200 字内全部《X》去重。"""
    basis = []
    for m in re.finditer(r"(?:根据|依据|依照|按照)[^。；\n]{0,200}", body):
        for law in re.findall(r"《([^》]{2,40})》", m.group(0)):
            if law not in basis:
                basis.append(law)
        if basis:  # 首条立法依据段即可
            break
    if not basis:
        m2 = re.search(r"第[一二三四五六七八九十百零〇\d]+条[^。]{0,100}(?:根据|依据|依照|按照)《([^》]{2,40})》", body)
        if m2:
            basis.append(m2.group(1))
    return basis


def analyze_body(body):
    """条款级引用分析：art_refs（条/款/项）+ name_refs_top（书名号 TOP8）。"""
    art_refs = collections.Counter()
    for mm in re.finditer(
        r"《([^》]{2,40})》\s*第([一二三四五六七八九十百零〇\d]+)条"
        r"(?:第([一二三四五六七八九十百零〇\d]+)款)?(?:第([一二三四五六七八九十百零〇\d]+)项)?",
        body,
    ):
        law = mm.group(1)
        art = f"第{mm.group(2)}条" + (f"第{mm.group(3)}款" if mm.group(3) else "") + (
            f"第{mm.group(4)}项" if mm.group(4) else ""
        )
        art_refs[(law, art)] += 1
    name_refs = collections.Counter(re.findall(r"《([^》]{2,40})》", body))
    return (
        [(law, art, cnt) for (law, art), cnt in art_refs.items()],
        name_refs.most_common(8),
    )


def build_lib_index(lib_config, libs):
    """构建五库标题归一化索引并返回 (index, lib_paths)。

    F-O08（2026-09-12）内存优化：原实现把整行记录（含 body_text 全文，五库合计
    数百 MB）全部驻留内存。现索引条目只存**轻量字段 + 行偏移**（title/docno/
    body_len/offset），命中后经 `_read_at` 定点读取正文 → 常驻内存降至 MB 级，
    IO 仅命中行（每主题命中数百行，代价可忽略）。
    """
    index, lib_paths = {}, {}
    for lib in libs:
        path = lib_config.get(lib)
        if not path or not os.path.exists(path):
            print(f"  ⚠️ 库 {lib} 路径不存在，跳过: {path}")
            continue
        idx = {}
        with open(path, "rb") as f:
            offset = 0
            for raw in f:
                step = len(raw)
                if raw.strip():
                    try:
                        rec = json.loads(raw)
                    except Exception:
                        rec = None
                    if rec is not None:
                        nt = norm_title(rec.get("title", "") or "")
                        if nt:
                            idx.setdefault(nt, []).append({
                                "offset": offset,
                                "body_len": len(rec.get("body_text", "") or ""),
                                "docno": rec.get("document_number", "") or "",
                                "title": rec.get("title", "") or "",
                            })
                offset += step
        index[lib] = idx
        lib_paths[lib] = path
        print(f"  📚 {lib} 索引: {len(idx)} 条（轻量偏移索引，F-O08）")
    return index, lib_paths


def _read_at(path, offset):
    """按行偏移定点读取 JSONL 记录（F-O08：命中后按需读取，避免全量正文驻留）。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            return json.loads(fh.readline())
    except (OSError, ValueError):
        return None


def match_one(seq, title, doc_no, lib_index, min_doc_len=5):
    """三级判据匹配单条，返回 (lib, light_item) 或 None（F-O08：item 为轻量索引项）。"""
    nt, tdoc = norm_title(title), norm_doc(doc_no)
    best = None
    for lib, idx in lib_index.items():
        cand = None
        # 判据①：标题归一化精确
        if nt in idx:
            cand = (lib, max(idx[nt], key=lambda x: x["body_len"]))
        # 判据②：发文字号归一化相等
        if not cand and tdoc and len(tdoc) >= min_doc_len:
            for _k, cands in idx.items():
                for b in cands:
                    if norm_doc(b.get("docno", "")) == tdoc:
                        cand = (lib, b)
                        break
                if cand:
                    break
        # 判据③：标题双向包含 + 文号佐证
        if not cand:
            for k, cands in idx.items():
                if nt and len(nt) >= 6 and len(k) >= 6 and (nt in k or k in nt):
                    b = max(cands, key=lambda x: x["body_len"])
                    rdoc = norm_doc(b.get("docno", ""))
                    if not tdoc or len(tdoc) < min_doc_len or (rdoc and (tdoc in rdoc or rdoc in tdoc)):
                        cand = (lib, b)
                        break
        if cand and (best is None or cand[1]["body_len"] > best[1]["body_len"]):
            best = cand
    return best


def main():
    ap = argparse.ArgumentParser(description="五库正文匹配 + 条款级引用分析工具")
    ap.add_argument("--input", required=True, help="主题底座 final.json（含 seq/title/doc_no/cluster）")
    ap.add_argument("--output", required=True, help="输出 matched.json 路径")
    ap.add_argument("--citerefs", required=True, help="输出 citerefs.json 路径")
    ap.add_argument("--libs", default="nfra,pbc,mof,gov,supp", help="参与匹配的库（逗号分隔，默认五库）")
    ap.add_argument("--lib-config", default="", help="五库 JSONL 路径配置 JSON（可选，默认内置标准路径）")
    ap.add_argument("--min-body", type=int, default=50, help="条款分析最小正文长度（默认 50 字）")
    ap.add_argument("--merge", action="store_true", help="增量合并：保留既有 matched 中本次未匹配的旧记录")
    args = ap.parse_args()

    # 读取主题底座
    recs = json.load(open(args.input, encoding="utf-8"))
    print(f"输入主题底座: {len(recs)} 条 | {args.input}")

    # 五库配置
    lib_config = DEFAULT_LIBS
    if args.lib_config:
        lib_config.update(json.load(open(args.lib_config, encoding="utf-8")))
    libs = [lib.strip() for lib in args.libs.split(",") if lib.strip()]

    # 构建索引
    lib_index, lib_paths = build_lib_index(lib_config, libs)
    if not lib_index:
        print("❌ 无可用库索引，退出")
        sys.exit(1)

    # 既有 matched（--merge 时；键兼容 seq 旧期与 RFN 现期）
    old_matched = {}
    if args.merge and os.path.exists(args.output):
        old_matched = json.load(open(args.output, encoding="utf-8"))

    # 匹配（R9 修复 2026-09-08：顶层键=RFN，clause_graph 依赖 RFN 键回填 docno/eff/file_src；
    # rec 补 监管文件编号/seq 双溯源；旧 seq 键仅 merge 保留时兼容）
    matched = {}
    miss = []
    for r in recs:
        seq = r["seq"]
        rfn = (r.get("监管文件编号") or "").strip()
        key = rfn or str(seq)
        best = match_one(seq, r["title"], r.get("doc_no", ""), lib_index)
        if best:
            lib, item = best
            # F-O08：命中后按偏移定点读取完整记录（正文仅命中行驻留）
            rec = _read_at(lib_paths[lib], item["offset"]) or {}
            matched[key] = {
                "lib": lib,
                "title": rec.get("title", "") or item.get("title", ""),
                "docno": rec.get("document_number", ""),
                "body_len": len(rec.get("body_text", "") or ""),
                "body": rec.get("body_text", "") or "",
                "url": rec.get("source_url", ""),
                "监管文件编号": rfn,
                "seq": seq,
                "cluster": r.get("cluster", ""),   # F-D03：clause_graph 经 citerefs 取 cluster（防取明细旧值）
            }
        elif args.merge and (key in old_matched or str(seq) in old_matched):
            matched[key] = old_matched.get(key) or old_matched[str(seq)]  # 保留旧记录
        else:
            miss.append(key)

    # 条款分析
    citerefs = {}
    for sk, m in matched.items():
        body = m.get("body", "") or ""
        if len(body) < args.min_body:
            continue
        art_refs, name_refs = analyze_body(body)
        citerefs[sk] = {
            "title": m.get("title", ""),
            "lib": m.get("lib", ""),
            "body_len": len(body),
            "basis": extract_basis(body),
            "art_refs": art_refs,
            "name_refs_top": name_refs,
            "监管文件编号": m.get("监管文件编号", ""),   # R9 补：recall Gate4 CITEREFS_KEYS 期望键
            "seq": m.get("seq", ""),
            "cluster": m.get("cluster", ""),   # F-D03：cluster 随 final 统一来源（clause_graph 消费）
        }

    # 保存
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    json.dump(matched, open(args.output, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(citerefs, open(args.citerefs, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 统计
    lib_stat = collections.Counter(v["lib"] for v in matched.values())
    full = sum(1 for v in matched.values() if v.get("body_len", 0) >= 100)
    print(f"\n✅ 匹配: {len(matched)}/{len(recs)} | 按库: {dict(lib_stat)} | 有正文(≥100字): {full}")
    print(f"✅ 条款分析: {len(citerefs)} 条")
    # F-D04：未命中清单落盘（覆盖缺口可见可追；Gate4 matched_coverage 依据；库覆盖补齐后
    # 重跑本步即可消退）。
    miss_path = os.path.splitext(args.output)[0] + "_miss.json"
    json.dump(miss, open(miss_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"⚠️ 未命中清单: {miss_path}（{len(miss)} 条）")
    if miss:
        print(f"⚠️ 未命中 {len(miss)} 条: {miss[:20]}")
    print(f"✅ 输出: {args.output}\n        {args.citerefs}")


if __name__ == "__main__":
    main()
