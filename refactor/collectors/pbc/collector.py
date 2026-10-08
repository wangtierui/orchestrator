# -*- coding: utf-8 -*-
"""pbc 中国人民银行爬虫：主流程（实现 SourceCollector）。

原 modules/regulatory_scrapers/collectors/pbc_collector.py 主流程迁出，逻辑逐函数等价；
产物仍为裸 list 写入 data/raw/pbc_laws.json（与下游 clean 既有输入契约一致），
仅落盘改为 atomic_write（崩溃安全），运行锁与原实现一致（fs_lock + atexit）。
"""
import argparse
import csv
import io
import json
import os
import sys
import atexit

from ..base import REPO_ROOT, SourceCollector, atomic_write
from . import net, parse
from std_lib.common_lib import fs_lock

# 原 collector 的 regulatory_scrapers 根：用于默认 out 与 local_path 相对基址（字节一致）。
SCRAPERS_ROOT = os.path.join(REPO_ROOT, "modules", "regulatory_scrapers")
DEFAULT_OUT = os.path.join(SCRAPERS_ROOT, "data", "raw")


# ----------------------------------------------------------------------------------
# 列表页解析
# ----------------------------------------------------------------------------------
def parse_listing(html_text, column_dir):
    """
    返回 (moduleid, totalpage, [(title, abs_url), ...])
    column_dir: 栏目目录的绝对 URL（不含 /index.html）
    """
    paging = net.RE_PAGING.search(html_text)
    moduleid, totalpage = (paging.group(1), int(paging.group(2))) if paging else (None, 1)

    entries = []
    seen = set()
    for m in net.RE_ENTRY.finditer(html_text):
        href = m.group(1)
        title = parse.clean_text(m.group(2)) or parse.clean_text(parse.strip_tags(m.group(3)))
        if not title:
            continue
        abs_url = net.BASE + "/" + href.lstrip("/") if not href.startswith("http") else href
        abs_url = parse.safe_url(abs_url)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        entries.append((title, abs_url))
    return moduleid, totalpage, entries


def listing_page_urls(index_url, moduleid, totalpage):
    """
    生成列表所有分页 URL（第 1 页为 index.html，后续为 {short}-{n}.html）。
    统一截断 moduleid 前 8 位，同时覆盖数字栏目与 hex 栏目。
    """
    column_dir = index_url.rsplit("/index.html", 1)[0]
    urls = [index_url]
    if moduleid and totalpage > 1:
        short = moduleid[:8]
        for n in range(2, totalpage + 1):
            urls.append(f"{column_dir}/{short}-{n}.html")
    return urls


# ----------------------------------------------------------------------------------
# 逐条抓取
# ----------------------------------------------------------------------------------
def scrape_category(cat, fetcher, args, done_urls, existing_map=None):
    name = cat["name"]
    index_url = cat["index_url"]
    records: list = []

    # 1) 抓取首页，探测分页参数
    try:
        status, txt = fetcher.get(index_url, referer=net.BASE + "/tiaofasi/144941/index.html")
    except net._OfflineMiss:
        print(f"  [offline] 栏目《{name}》首页缓存缺失，跳过", file=sys.stderr)
        return records
    if status != 200 or not txt:
        print(f"  [!] 栏目《{name}》首页获取失败 status={status}", file=sys.stderr)
        return records

    moduleid, totalpage, entries = parse_listing(txt, index_url.rsplit("/index.html", 1)[0])
    print(f"  [+] 栏目《{name}》探测到 moduleid={moduleid} totalpage={totalpage} 首页条目={len(entries)}")

    # 2) 遍历所有分页，收集条目
    page_urls = listing_page_urls(index_url, moduleid, totalpage)
    for pu in page_urls[1:]:  # 第 1 页已处理
        try:
            st, ptxt = fetcher.get(pu, referer=index_url)
        except net._OfflineMiss:
            print(f"  [offline] 分页缓存缺失 {pu}，停止翻页", file=sys.stderr)
            break
        if st == 200 and ptxt:
            _, _, more = parse_listing(ptxt, index_url.rsplit("/index.html", 1)[0])
            entries.extend(more)
        else:
            print(f"  [!] 分页获取失败 {pu} status={st}", file=sys.stderr)

    # 去重（跨页可能重复）
    uniq = {}
    for t, u in entries:
        uniq[u] = t
    print(f"  [+] 合并后去重条目数：{len(uniq)}")

    # 3) 逐条抓取详情 / 附件
    for i, (url, title) in enumerate(uniq.items(), 1):
        if args.max_items and len(records) >= args.max_items:
            break
        rec = {
            "category": name,
            "title": title,
            "detail_url": url,
            "link_type": "attachment" if parse.is_attachment(url) else "html",
            "file_type": (url.rsplit(".", 1)[-1].lower() if parse.is_attachment(url) else None),
            "local_path": None,
            "publish_date": None,
            "document_number": None,
            "issuing_authority": None,
            "effective_date": None,
            "content": "",
            "summary": "",
            "fetch_status": "pending",
            "error": None,
        }

        if url in done_urls:
            # 断点续跑：复用已成功抓取的完整旧记录（含正文/字段），避免重跑时丢失内容
            old = (existing_map or {}).get(url)
            if old:
                rec = dict(old)
                rec["fetch_status"] = "skipped_existing"
            else:
                rec["fetch_status"] = "skipped_existing"
            records.append(rec)
            continue

        if rec["link_type"] == "html":
            try:
                st, dtxt = fetcher.get(url, referer=index_url)
            except net._OfflineMiss:
                rec["fetch_status"] = "fetch_failed"
                rec["error"] = "offline: 详情缓存缺失"
                records.append(rec)
                continue
            if st == 200 and dtxt:
                try:
                    d = parse.parse_detail(dtxt)
                    rec.update({k: d[k] for k in (
                        "title", "publish_date", "document_number",
                        "issuing_authority", "effective_date", "content")})
                    rec["fetch_status"] = "ok"
                except Exception as e:  # noqa: BLE001
                    rec["fetch_status"] = "parse_error"
                    rec["error"] = f"{type(e).__name__}: {e}"
            else:
                rec["fetch_status"] = "fetch_failed"
                rec["error"] = f"status={st}; {dtxt}"
        else:
            # 附件：下载原始文件到本地，并按格式路由解析正文。
            if args.no_attachments:
                rec["fetch_status"] = "attachment_skipped"
            else:
                try:
                    st, bdata = fetcher.get(url, referer=index_url, binary=True, timeout=60)
                except net._OfflineMiss:
                    rec["fetch_status"] = "fetch_failed"
                    rec["error"] = "offline: 附件缓存缺失"
                    records.append(rec)
                    continue
                if not (st == 200 and bdata):
                    rec["fetch_status"] = "fetch_failed"
                    rec["error"] = f"附件下载失败 status={st}"
                else:
                    # 落盘原始文件（中文名安全编码）
                    fname = parse.safe_filename(rec["title"], rec["file_type"], url)
                    adir = os.path.join(net.ATTACHMENTS_DIR, name)
                    os.makedirs(adir, exist_ok=True)
                    fpath = os.path.join(adir, fname)
                    with open(fpath, "wb") as fh:
                        fh.write(bdata)
                    rec["local_path"] = os.path.relpath(fpath, SCRAPERS_ROOT).replace("\\", "/")
                    # 格式路由解析
                    ft = rec["file_type"]
                    parsed = None
                    try:
                        if ft == "docx":
                            parsed = parse.extract_docx_text(bdata)
                        elif ft == "pdf":
                            parsed = parse.extract_pdf_text(bdata)
                        elif ft in ("xls", "xlsx"):
                            parsed = parse.extract_xls_text(bdata)
                        elif ft in ("doc", "wps", "rtf", "ceb"):
                            if net.LO_AVAILABLE:
                                conv = parse.convert_with_libreoffice(fpath)
                                if conv and os.path.exists(conv):
                                    with open(conv, "rb") as cf:
                                        parsed = parse.extract_docx_text(cf.read())
                        # 表格结构化（xlsx/docx 附件表 → rec 表键）
                        if ft in ("docx", "xls", "xlsx", "doc", "wps", "rtf", "ceb"):
                            rec.update(net.structured_table_fields(bdata, fname))
                        # 富内容轨（docx/xlsx 图形/公式/图片）
                        try:
                            rec.update(net.rich_object_fields(
                                bdata, fname,
                                image_dir=net.docs_root("pbc", "diagrams"),
                                rec_key=f"{name}_{ft}"))
                        except Exception:  # noqa: BLE001
                            pass
                    except Exception as e:  # noqa: BLE001
                        rec["error"] = ("附件正文解析异常：%s: %s；已保存原始文件供下载"
                                        % (type(e).__name__, e))
                        parsed = None
                    # 结果判定
                    if parsed:
                        rec["content"] = parsed
                        rec["fetch_status"] = "ok"
                    else:
                        rec["fetch_status"] = "attachment_saved"
                        if ft in ("doc", "wps", "rtf", "ceb"):
                            rec["error"] = "正文解析需 LibreOffice 环境（当前不可用），已保存原始文件供下载"
                        elif ft in ("xls", "xlsx"):
                            rec["error"] = "表格正文提取失败，已保存原始文件供下载"
                        elif ft == "pdf":
                            rec["error"] = "PDF 文本提取失败，已保存原始文件供下载"
                        else:
                            rec["error"] = f".{ft} 暂不支持正文解析，已保存原始文件供下载"

        # 内容摘要（前 200 字）
        if rec["content"]:
            rec["summary"] = rec["content"][:200]
        records.append(rec)
        if i % 20 == 0 or i == len(uniq):
            print(f"  [.] 《{name}》进度 {i}/{len(uniq)} 成功={sum(1 for r in records if r['fetch_status']=='ok')}")
    return records


def save_outputs(records, out_dir, write_csv=False):
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "pbc_laws.json")
    csv_path = os.path.join(out_dir, "pbc_laws.csv")
    # 原子写：先写临时文件再替换，避免写入中途崩溃损坏已有结果（内容与原实现一致）。
    atomic_write(json_path, json.dumps(records, ensure_ascii=False, indent=2))
    if write_csv:                                 # 默认仅 JSON 主库（2026-09-09 规范）
        fields = ["category", "title", "detail_url", "link_type", "file_type", "local_path",
                  "publish_date", "document_number", "issuing_authority",
                  "effective_date", "content", "summary", "fetch_status", "error"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields)
        w.writeheader()
        for r in records:
            w.writerow(r)
        atomic_write(csv_path, "\ufeff" + buf.getvalue())  # 与原 utf-8-sig 一致（含 BOM）
    return json_path, (csv_path if write_csv else "")


class PbcCollector:
    """中国人民银行法规爬虫；实现 SourceCollector。"""

    source_id = "pbc"

    def collect(self, out_dir: str, *, category=None, max_items=0, delay=1.0,
                no_attachments=False, csv=False, cache_dir="", offline=False) -> str:
        net._init_cache(cache_dir or None, offline)

        # —— 调度可靠性：跨进程单实例锁防并发重复（统一 fs_lock 公共库，N-8）——
        _lock = fs_lock.ProcessLock(os.path.join(out_dir, "scrape.lock"))
        if not _lock.acquire():
            print("[!] 已有抓取任务在运行（锁存在且 PID 存活），本次跳过以避免重复抓取。",
                  file=sys.stderr)
            sys.exit(0)
        atexit.register(_lock.release)

        min_d = max(0.4, delay * 0.8)
        max_d = delay * 1.6
        fetcher = net.Fetcher(min_delay=min_d, max_delay=max_d, timeout=30, retries=3)

        # 断点续跑：读取已有 JSON
        done_urls = set()
        existing_map = {}
        out_json = os.path.join(out_dir, "pbc_laws.json")
        if os.path.exists(out_json):
            try:
                with open(out_json, encoding="utf-8") as f:
                    existing = json.load(f)
                for r in existing:
                    if r.get("fetch_status") == "ok":
                        done_urls.add(r["detail_url"])
                        existing_map[r["detail_url"]] = r
                print(f"[*] 检测到已有结果，跳过 {len(done_urls)} 条已成功条目（断点续跑）")
            except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                pass

        cats = [c for c in net.CATEGORIES if (not category or c["name"] == category)]
        if not cats:
            print(f"[!] 未找到栏目：{category}", file=sys.stderr)
            sys.exit(1)

        import argparse as _argparse
        args = _argparse.Namespace(max_items=max_items, no_attachments=no_attachments)

        all_records = []
        for cat in cats:
            print(f"\n=== 开始抓取栏目：《{cat['name']}》 ===")
            recs = scrape_category(cat, fetcher, args, done_urls, existing_map)
            all_records.extend(recs)
            # 逐栏目增量落盘：即使进程被中断，已完成栏目数据不丢失，下次运行可断点续跑
            save_outputs(all_records, out_dir, write_csv=csv)
            print(f"  [✓] 《{cat['name']}》已落盘，累计 {len(all_records)} 条")

        if max_items:
            all_records = all_records[:max_items]

        json_path, csv_path = save_outputs(all_records, out_dir, write_csv=csv)
        print(f"\n[✓] 完成。JSON: {json_path}\n    CSV : {csv_path}")
        return json_path


def main():
    ap = argparse.ArgumentParser(description="中国人民银行条法司法规抓取脚本")
    ap.add_argument("--category", help="仅抓取指定栏目名称（如 国家法律）")
    ap.add_argument("--max-items", type=int, default=0, help="限制总条目数（调试用）")
    ap.add_argument("--delay", type=float, default=1.0, help="平均请求间隔（秒）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出目录（默认统一 data/raw/）")
    ap.add_argument("--no-attachments", action="store_true", help="不下载附件正文，仅记录链接")
    ap.add_argument("--csv", action="store_true",
                    help="额外输出 CSV（默认仅写 JSON 主库，2026-09-09 规范）")
    ap.add_argument("--cache-dir", default="",
                    help="请求缓存目录（显式覆盖）：缺省由 cache_store.source_cache_root(pbc) 统一解析")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    args = ap.parse_args()

    json_path = PbcCollector().collect(
        args.out, category=args.category, max_items=args.max_items, delay=args.delay,
        no_attachments=args.no_attachments, csv=args.csv,
        cache_dir=args.cache_dir, offline=args.offline)
    return json_path


if __name__ == "__main__":
    main()
