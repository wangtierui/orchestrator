# -*- coding: utf-8 -*-
"""pbc 中国人民银行爬虫：主流程（继承 CollectorPipeline，实现 SourceCollector）。

通用流程（列表→known/new/dup 分流→复用/抓取→落库）由 CollectorPipeline 承担；
pbc 的特殊步骤均表达为钩子：
  - iter_list_entries：逐栏目翻页，首页带服务端限流（反爬）退避重试；
  - handle_new：html 抓详情 / 附件下载 + 格式路由解析（docx/pdf/xls/...）；
  - handle_known：复用旧记录并标 skipped_existing（断点续跑不丢内容）；
  - batch_key_of：按栏目分批落盘（崩溃容错，已完成栏目不丢）；
  - known_pred：仅 fetch_status=='ok' 算已知；
  - max_items：调试上限；save：storage + 可选 CSV。
"""
import argparse
import os
import sys
import time

from ..base import REPO_ROOT, SourceCollector
from ..lib.cli import add_common_args, is_full_mode, make_known_pred_full
from . import net, parse
from ..lib.pipeline import CollectorPipeline

# 默认落盘收口在 refactor/data/raw（自包含；完整去耦在 rollout 阶段）。
SCRAPERS_ROOT = os.path.join(REPO_ROOT, "data", "raw")
DEFAULT_OUT = SCRAPERS_ROOT


# ----------------------------------------------------------------------------------
# 列表页解析（保留，供 iter_list_entries 使用）
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


class PbcCollector(CollectorPipeline):
    """中国人民银行法规爬虫；继承通用管线，特殊步骤表达为钩子。"""

    source_id = "pbc"
    master_filename = "pbc_laws.json"
    # 仅 fetch_status=='ok' 的历史记录算"已知可复用"（断点续跑）。
    known_pred = staticmethod(lambda r: r.get("fetch_status") == "ok")

    # ----------------------------- CollectorPipeline 钩子 -----------------------------
    def key_of(self, item: dict):
        return item.get("detail_url")

    def batch_key_of(self, entry: dict):
        return entry.get("category")  # 按栏目分批落盘（崩溃容错）

    def max_items(self) -> int:
        return getattr(self._args, "max_items", 0) or 0

    def _setup(self, args):
        if is_full_mode(args):
            self.known_pred = make_known_pred_full()
        min_d = max(0.4, args.delay * 0.8)
        max_d = args.delay * 1.6
        self._fetcher = net.Fetcher(min_delay=min_d, max_delay=max_d, timeout=30, retries=3)
        self._args = args

    def iter_list_entries(self, ctx):
        cats = [c for c in net.CATEGORIES
                if (not getattr(self._args, "category", None) or c["name"] == self._args.category)]
        if not cats:
            print(f"[!] 未找到栏目：{getattr(self._args, 'category', None)}", file=sys.stderr)
            return
        for cat in cats:
            name = cat["name"]
            index_url = cat["index_url"]
            col_dir = index_url.rsplit("/index.html", 1)[0]

            # 1) 抓取首页探测分页参数（带服务端限流容错重试）
            # pbc 站点对高频访问会主动返回反爬页（含"访问频率/验证码"等），属服务端自身
            # 行为而非网络故障；此类页面既无分页也无条目，若静默放过会导致整栏目 0 条入库。
            # 故检测反爬页/0 条目后指数退避重试，持续失败则明确报警并放弃该栏目。
            moduleid = totalpage = None
            entries = []
            referer = net.BASE + "/tiaofasi/144941/index.html"
            for attempt in range(1, 5):
                status, txt = self._fetcher.get(index_url, referer=referer)
                if status != 200 or not txt:
                    print(f"  [!] 栏目《{name}》首页获取失败 status={status}（尝试 {attempt}/4）",
                          file=sys.stderr)
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if not net._is_cacheable(txt):
                    print(f"  [!] 栏目《{name}》首页疑似被服务端风控（反爬页，尝试 {attempt}/4），退避后重试",
                          file=sys.stderr)
                    time.sleep(min(2 ** attempt * 3, 30))
                    continue
                moduleid, totalpage, entries = parse_listing(txt, col_dir)
                if not entries:
                    print(f"  [!] 栏目《{name}》首页 0 条目（尝试 {attempt}/4），退避后重试",
                          file=sys.stderr)
                    time.sleep(min(2 ** attempt * 3, 30))
                    continue
                break
            else:
                print(f"  [!] 栏目《{name}》持续被服务端风控/0 条目，放弃本栏目（非网络故障，为服务端限流）",
                      file=sys.stderr)
                continue

            print(f"  [+] 栏目《{name}》探测到 moduleid={moduleid} totalpage={totalpage} 首页条目={len(entries)}")

            # 2) 遍历所有分页，收集条目
            page_urls = listing_page_urls(index_url, moduleid, totalpage)
            for pu in page_urls[1:]:  # 第 1 页已处理
                st, ptxt = self._fetcher.get(pu, referer=index_url)
                if st == 200 and ptxt:
                    _, _, more = parse_listing(ptxt, col_dir)
                    entries.extend(more)
                else:
                    print(f"  [!] 分页获取失败 {pu} status={st}", file=sys.stderr)

            # 3) 逐条 yield（跨页重复 / known 判定交给基类 tracker）
            for title, url in entries:
                yield {"title": title, "detail_url": url, "category": name, "index_url": index_url}

    def handle_known(self, key, entry, ctx):
        rec = ctx.master_by_key.get(key)
        if rec is None:
            return None
        rec = dict(rec)
        rec["fetch_status"] = "skipped_existing"  # 断点续跑：复用旧记录内容，标 skipped
        return rec

    def handle_new(self, entry, ctx):
        args = self._args
        url = entry["detail_url"]
        index_url = entry["index_url"]
        name = entry["category"]
        rec = {
            "category": name,
            "title": entry["title"],
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

        if args.no_detail:
            rec["fetch_status"] = "title_link_only"
            return rec

        if rec["link_type"] == "html":
            st, dtxt = self._fetcher.get(url, referer=index_url)
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
                st, bdata = self._fetcher.get(url, referer=index_url, binary=True, timeout=60)
                if not (st == 200 and bdata):
                    # 附件下载失败（含 404）：记为非致命元数据，记录保留（写一次），
                    # 不降级为致命 fetch_failed，后续可由"附件补抓"任务重试。
                    rec["fetch_status"] = "attachment_failed"
                    rec["attachment_status"] = "failed"
                    rec["attachment_errors"] = [
                        {"url": url, "status": st, "reason": f"附件下载失败 status={st}"}
                    ]
                    return rec
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
        return rec

    def save(self, records, ctx):
        return ctx.storage.save_master(records, extra=self.build_extra(ctx, records))

    # ----------------------------- SourceCollector 协议入口 -----------------------------
    def collect(self, out_dir: str, *, category="", mode="incremental", delay=1.0,
                limit=0, no_detail=False, no_attachments=False) -> str:
        args = argparse.Namespace(
            out_dir=out_dir, category=category, max_items=limit,
            delay=delay, no_detail=no_detail, no_attachments=no_attachments, csv=False)
        self._setup(args)
        return self.run(out_dir)


def main():
    ap = argparse.ArgumentParser(description="中国人民银行条法司法规抓取脚本")
    add_common_args(ap, with_category=True, with_mode=True, with_delay=True,
                    with_limit=True, with_detail=True, with_attachments=True,
                    default_out=DEFAULT_OUT)
    args = ap.parse_args()
    PbcCollector().collect(
        args.out_dir, category=args.category, mode=args.mode, delay=args.delay,
        limit=args.limit, no_detail=args.no_detail,
        no_attachments=args.no_attachments)


if __name__ == "__main__":
    main()
