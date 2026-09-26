# -*- coding: utf-8 -*-
"""
scraper_std.pkulaw_cli —— 北大法宝 CLI 客户端与时效判定（零 LLM 消耗路径）
================================================================================
mcp.pkulaw.com 官方 CLI（@pkulaw/mcp-cli）：纯命令执行、不经 LLM（官方标注「零消耗」），
与 WorkBuddy 同一套鉴权网关、Token 通用。本模块提供：

  1) CLI 定位与调用        find_cli / call_cli / get_law_list_records
  2) Token 安全加载        load_token（环境变量 PKULAW_TOKEN 或本地文件，绝不落盘）
  3) 返回归一化            normalize_records（保留接口全字段，Url 取裸链）
  4) 时效判定（核心）      同名自版本优先 + 重复标题按文号去歧义：
                           匹配优先级 = 文号签名(年份+序号) → publish_date → 最新版本
  5) 并发断点查询引擎      build_query_plan / execute_queries（checkpoint 即查询 JSONL）

依赖：仅标准库 + 系统 Node（CLI 运行环境）。
安全红线：Token 只读自环境变量/本机文件，绝不写入日志、命令参数、输出文件。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# ---------------------------------------------------------------- 状态常量
VALID, AMENDED, REPEALED, EXPIRED, PENDING, UNCERTAIN = (
    "valid", "amended", "repealed", "expired", "pending", "uncertain")

# ---------------------------------------------------------------- 环境事实
# P1 去硬编码：node.exe 与 @pkulaw/mcp-cli 包目录不再写死本机路径。
# 优先级：环境变量 PKULAW_NODE_EXE / PKULAW_PKG_DIR > shutil.which 探测 > 空（find_cli 抛错提示安装）。
NODE_EXE = os.environ.get("PKULAW_NODE_EXE", "")
PKG_DIR = os.environ.get("PKULAW_PKG_DIR", "")

# 接口返回字段 → 既有 jsonl schema 字段映射（按语义，见 pkulaw skill 3.2）
FIELD_MAP = {
    "title": "Title", "Title": "Title", "original": "Title",
    "url": "Url", "Url": "Url", "source": "Url",
    "category": "Category", "Category": "Category",
    "documentno": "DocumentNO", "DocumentNO": "DocumentNO", "doc_no": "DocumentNO",
    "issuedepartment": "IssueDepartment", "IssueDepartment": "IssueDepartment", "issue_department": "IssueDepartment",
    "issuedate": "IssueDate", "IssueDate": "IssueDate", "issue_date": "IssueDate",
    "implementdate": "ImplementDate", "ImplementDate": "ImplementDate", "implement_date": "ImplementDate",
    "timeliness": "TimelinessDic", "TimelinessDic": "TimelinessDic", "timeliness_dic": "TimelinessDic",
    "effectiveness": "EffectivenessDic", "EffectivenessDic": "EffectivenessDic", "effectiveness_dic": "EffectivenessDic",
}

URL_RE = re.compile(r"\[[^\]]*\]\((https?://[^)]+)\)")


# ---------------------------------------------------------------- CLI 定位
def _resolve_node_exe(node_exe: str) -> str:
    """解析可用的 node 解释器路径（2026-09-19 加固）。

    背景：WorkBuddy 运行时升级 node 时会把 `versions/<ver>` 递增（实测
    `22.22.2-2` → `22.22.2-3`），而用户级环境变量 `PKULAW_NODE_EXE` 仍指向**旧版本目录**
    → `find_cli` 找不到解释器，CLI 静默失联（表现为「未找到 pkulaw-mcp CLI」，易误判为未安装）。

    派生顺序（不写死本机路径）：① 环境给定路径存在即用；② 同层 `versions/current`
    指针文件指向的版本目录内同名可执行文件；③ `shutil.which` 兜底。
    """
    if node_exe and os.path.exists(node_exe):
        return node_exe
    base = os.path.basename(node_exe) or "node.exe"
    if node_exe:
        versions_dir = os.path.dirname(os.path.dirname(node_exe))   # …/node/versions
        cur = os.path.join(versions_dir, "current")
        try:
            if os.path.exists(cur):
                ver = open(cur, encoding="utf-8").read().strip()
                cand = os.path.join(versions_dir, ver, base)
                if ver and os.path.exists(cand):
                    return cand
        except OSError:
            pass
    return shutil.which("node") or ""


def find_cli(node_exe: str = NODE_EXE, pkg_dir: str = PKG_DIR,
             env_cli: str = "") -> list[str]:
    """返回可执行命令列表：优先 node.exe + 包内 JS 入口（规避 Windows cmd 引号问题）"""
    if env_cli and os.path.exists(env_cli):
        return [env_cli]
    node_exe = _resolve_node_exe(node_exe)
    pkg_json = os.path.join(pkg_dir, "package.json")
    if os.path.exists(pkg_json):
        try:
            with open(pkg_json, encoding="utf-8") as fh:
                binmap = json.load(fh).get("bin", {})
            entry = binmap.get("pkulaw-mcp") or (list(binmap.values())[0] if binmap else None)
            if entry:
                js = os.path.join(pkg_dir, entry)
                if os.path.exists(js) and node_exe and os.path.exists(node_exe):
                    return [node_exe, js]
        except Exception:
            pass
    w = shutil.which("pkulaw-mcp")
    if w:
        return [w]
    raise RuntimeError("未找到 pkulaw-mcp CLI，请先: npm install @pkulaw/mcp-cli")


# ---------------------------------------------------------------- Token
def _clean_token(t: str) -> str:
    """兼容：尾部换行、PowerShell UTF-8 BOM、误粘贴的 Bearer 前缀"""
    t = (t or "").strip().lstrip("\ufeff")
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    return t


def load_token(token_file: str | None = None) -> str:
    """Token 优先级：环境变量 PKULAW_TOKEN > 指定文件。仅内存使用，绝不写日志/输出。"""
    tok = os.environ.get("PKULAW_TOKEN", "").strip()
    if tok:
        return _clean_token(tok)
    if token_file and os.path.exists(token_file):
        with open(token_file, encoding="utf-8") as fh:
            return _clean_token(fh.read())
    return ""


# ---------------------------------------------------------------- 调用与解析
def _parse_json_embedded(s: str) -> Any | None:
    """CLI 会在 JSON 前打印进度行（输出到 stdout），提取首个 JSON 对象/数组"""
    if not s:
        return None
    idx = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not idx:
        return None
    i_open = min(idx)
    close_c = "}" if s[i_open] == "{" else "]"
    i_close = s.rfind(close_c)
    if i_close < i_open:
        return None
    try:
        return json.loads(s[i_open:i_close + 1])
    except json.JSONDecodeError:
        return None


def unwrap_cli_response(obj: Any) -> tuple[list[Any], int | None]:
    """CLI 可能返回 {'Message','Data','Total'} 包裹体，也可能直接数组"""
    if isinstance(obj, dict) and "Data" in obj and isinstance(obj["Data"], list):
        return obj["Data"], obj.get("Total")
    if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], list):
        return obj["data"], obj.get("total")
    if isinstance(obj, list):
        return obj, len(obj)
    if isinstance(obj, dict) and "Message" in obj:
        return [], obj.get("Total")
    return [], None


def clean_url(v: Any) -> str:
    """CLI 返回 Url 为 Markdown 链接形态 [北大法宝](https://...)，取裸链"""
    if not v:
        return ""
    m = URL_RE.match(str(v))
    return m.group(1) if m else str(v)


def normalize_records(raw: Any) -> list[dict[str, Any]]:
    """CLI 返回 → 既有 jsonl schema 记录列表（9 字段齐全，全字段保留）"""
    if isinstance(raw, dict):
        for k in ("Data", "data", "Result", "result", "List", "list"):
            if isinstance(raw.get(k), list):
                raw = raw[k]
                break
        else:
            raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rec = {v: "" for v in FIELD_MAP.values()}
        for k, v in item.items():
            std = FIELD_MAP.get(k)
            if std is not None and v is not None:
                rec[std] = clean_url(v) if std == "Url" else v
        out.append(rec)
    return out


def call_cli(cli: list[str], token: str, title: str,
             fulltext: str | None = None,
             timeout: int = 180) -> tuple[list[Any], str | None]:
    """调用 get_law_list。返回 (原始记录列表, 错误信息或None)。Token 经环境变量注入。"""
    env = dict(os.environ)
    env["PKULAW_MCP_AUTHORIZATION"] = "Bearer " + token
    cmd = cli + ["law-keyword", "get_law_list", "--title", title]
    if fulltext:
        cmd += ["--fulltext", fulltext]
    cmd += ["--json"]
    proc = subprocess.run(cmd, shell=False, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return [], "[CLI失败 rc=%d] %s" % (proc.returncode, err[:300])
    try:
        obj = json.loads(proc.stdout)
    except json.JSONDecodeError:
        obj = _parse_json_embedded(proc.stdout)
    if obj is None:
        return [], "[JSON解析失败] stdout前300: %s" % proc.stdout[:300]
    data, _total = unwrap_cli_response(obj)
    return data, None


def get_law_list_records(cli: list[str], token: str, title: str,
                         fulltext: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """便捷：返回归一化记录 + 错误信息"""
    data, err = call_cli(cli, token, title, fulltext)
    if err:
        return [], err
    return normalize_records(data), None


# ---------------------------------------------------------------- 判定工具
def _norm_date(v: Any) -> tuple[int, int, int]:
    if not v:
        return (0, 0, 0)
    parts = [p for p in str(v).strip().replace("/", ".").replace("-", ".").split(".") if p]
    try:
        return tuple(int(p) for p in parts[:3]) + (0,) * (3 - len(parts))
    except ValueError:
        return (0, 0, 0)


def _norm_title(t: Any) -> str:
    """标题归一化（用于同名判定）：全角括号→半角；顿号/逗号/分号/空格→去全部空白。
    解决库内标题（半角空格分隔多机关，如『总局 中国人民银行』）与北大法宝标题
    （顿号『、』分隔、半角括号『()』，如『总局、中国人民银行』）的形态差异。"""
    t = str(t or "")
    t = t.replace("（", "(").replace("）", ")").replace("〔", "[").replace("〕", "]")
    t = t.replace("、", " ").replace("，", " ").replace(",", " ").replace("；", " ").replace(";", " ")
    t = re.sub(r"\s+", "", t)
    return t


def same_doc(query: str, t: Any) -> bool:
    """同名判定：全等 或 以『查询名(』开头（版本形态）；
    排除『实施细则』等以查询名为前缀的扩展文件。
    先对 query/t 做标题归一化（全角括号、顿号/空格分隔），兼容多机关发文标题的形态差异。"""
    if not t:
        return False
    qn, tn = _norm_title(query), _norm_title(t)
    if qn == tn:
        return True
    if tn.startswith(qn):
        rest = tn[len(qn):]
        return rest.startswith("(")
    return False


def norm_docno(s: Any) -> str:
    """文号归一化：去空白/全角→半角/〔〕→[]/去『第』『号』/去括号"""
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"\s+", "", s)
    s = s.replace("〔", "[").replace("〕", "]").replace("（", "(").replace("）", ")")
    s = s.replace("第", "").replace("号", "")
    s = s.replace("[", "").replace("]", "").replace("(", "").replace(")", "")
    return s.strip()


def docno_sig(s: Any) -> tuple[int, int] | None:
    """文号签名 (年份, 序号)：『金规[2025]8号』→(2025,8)；『主席令第26号』→(0,26)"""
    s = norm_docno(s)
    if not s:
        return None
    m = re.findall(r"(\d{4})年?(\d+)|(\d{4})", s)
    if m:
        y, n, y2 = m[-1]
        if y:
            return (int(y), int(n))
        return (int(y2), 0)
    m2 = re.search(r"(\d+)$", s)
    if m2:
        return (0, int(m2.group(1)))
    return (0, 0)


def docno_eq(a: Any, b: Any) -> bool:
    """文号相等：签名（年份+序号）一致即视为同一文号；
    由调用方保证在 same_doc 同名集合内比较（防不同机关同号误配）"""
    sa, sb = docno_sig(a), docno_sig(b)
    if not sa or not sb:
        return False
    return sa == sb


def _tl(rec: dict[str, Any]) -> str:
    return str(rec.get("TimelinessDic", "") or "")


def pick_verdict(records: list[dict[str, Any]], base_title: str) -> tuple[dict[str, Any] | None, int]:
    """同名自版本优先判定（单名称查询用）：现行有效 ∩ 同名自版本 → 取 IssueDate 最新。
    返回 (现行版本记录或None, 现行有效记录数)"""
    cur = [r for r in records if "现行有效" in _tl(r)]
    if not cur:
        return None, 0
    base = base_title.strip()
    selfv = [r for r in cur
             if r.get("Title") == base or str(r.get("Title", "")).startswith(base + "(")]
    pool = sorted(selfv if selfv else cur,
                  key=lambda r: (_norm_date(r.get("IssueDate")), _norm_date(r.get("ImplementDate"))))
    return pool[-1], len(cur)


def match_record(records: list[dict[str, Any]], cand: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """匹配库内记录对应的法宝版本：文号(DocumentNO) → 发布日期 → 最新。
    cand 需含 title / document_number / publish_date。"""
    same = [r for r in records if same_doc(cand["title"], str(r.get("Title", "")))]
    if not same:
        return same, None
    dn = norm_docno(cand.get("document_number", ""))
    if dn:
        for r in same:
            if docno_eq(dn, r.get("DocumentNO", "")):
                return same, r
    pub = cand.get("publish_date") or ""
    if pub and len(same) > 1:
        pd = _norm_date(pub)
        for r in same:
            if _norm_date(r.get("IssueDate")) == pd:
                return same, r
    return same, max(same, key=lambda r: _norm_date(r.get("IssueDate")))


def judge_candidate(records: list[dict[str, Any]], cand: dict[str, Any]) -> tuple[str, str, str, str]:
    """用北大法宝返回记录判定库内记录 → (status, replacement, source, note)"""
    same, matched = match_record(records, cand)
    if matched is None:
        note = "北大法宝无同名命中"
        if same:
            note += "（文号%s未匹配）" % norm_docno(cand.get("document_number", ""))
        return cand.get("timeliness_status", PENDING), cand.get("replacement_document", ""), "规则判断", note

    tl = _tl(matched)
    cur = [r for r in same if "现行有效" in _tl(r)]
    cur_title = max(cur, key=lambda r: _norm_date(r.get("IssueDate"))).get("Title", "") if cur else ""

    if "现行有效" in tl:
        return VALID, "", "北大法宝", "同名现行有效: %s" % matched.get("Title", "")
    if "尚未施行" in tl or "尚未生效" in tl or "未施行" in tl:
        # 已发布但尚未到施行日（如 2026-08 新发、2027-01-01 起施行）：
        # 仍属现行有效范畴（效力状态=valid），"未施行"是实施时点，由 implement_date 承载，
        # 不应误判为 pending/uncertain（北大法宝已确认其当前有效性）。
        return VALID, "", "北大法宝", "同名已发布尚未施行(现行有效): %s" % matched.get("Title", "")
    if "已被修改" in tl:
        return AMENDED, cur_title, "北大法宝", "库内版本已被修改: %s" % matched.get("Title", "")
    if "废止" in tl:
        return REPEALED, cur_title, "北大法宝", "北大法宝标注废止: %s" % matched.get("Title", "")
    if "失效" in tl:
        return EXPIRED, cur_title, "北大法宝", "北大法宝标注失效: %s" % matched.get("Title", "")
    return cand.get("timeliness_status", PENDING), cand.get("replacement_document", ""), "规则判断", "同名但时效标注异常: %s" % tl


# ---------------------------------------------------------------- 查询计划与执行
def build_query_plan(cands: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any] | None]]:
    """查询计划：重复标题且文号可区分 → 逐记录查询（key=标题||文号）；
    其余按标题单次查询。返回 (plan, cand2item[候选下标]=项)"""
    by_title = {}
    for i, c in enumerate(cands):
        by_title.setdefault(c["title"], []).append(i)
    plan = []
    for title, idxs in sorted(by_title.items()):
        docnos = {}
        for i in idxs:
            dn = norm_docno(cands[i].get("document_number", ""))
            docnos.setdefault(dn, []).append(i)
        distinct = [dn for dn in docnos if dn]
        if len(idxs) > 1 and len(distinct) >= 2:
            for dn, sub in docnos.items():
                if dn:
                    plan.append({"key": "%s||%s" % (title, dn), "title": title,
                                 "docno": dn, "idxs": sub})
                else:
                    plan.append({"key": title, "title": title, "docno": "", "idxs": sub})
        else:
            plan.append({"key": title, "title": title, "docno": "", "idxs": idxs})
    cand2item: list[dict[str, Any] | None] = [None] * len(cands)
    for it in plan:
        for i in it["idxs"]:
            cand2item[i] = it
    return plan, cand2item


def run_query(cli: list[str], token: str, title: str, docno: str = "",
              use_fulltext: bool = False) -> dict[str, Any]:
    """单次查询 → 信封记录（含 query_docno/fulltext 审计字段，data 为归一化全字段）"""
    records, err = get_law_list_records(cli, token, title, fulltext=(docno if use_fulltext else None))
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    env: dict[str, Any] = {"query": title, "queried_at": ts}
    if docno:
        env["query_docno"] = docno
    if use_fulltext:
        env["fulltext"] = docno
    if err:
        env["message"] = "失败: " + err[:300]
        env["total"] = 0
        env["data"] = []
        return env
    env["message"] = "成功"
    env["total"] = len(records)
    env["data"] = records
    return env


def fetch_item(cli: list[str], token: str, item: dict[str, Any]) -> dict[str, Any]:
    """单查询项：先标题检索；重复标题项若文号未命中 → fulltext=文号 二次检索兜底"""
    obj = run_query(cli, token, item["title"], item["docno"])
    if item["docno"] and obj["message"] == "成功" and obj["data"]:
        hit = any(docno_eq(item["docno"], r.get("DocumentNO", "")) for r in obj["data"])
        if not hit:
            obj2 = run_query(cli, token, item["title"], item["docno"], use_fulltext=True)
            if obj2["message"] == "成功" and obj2["data"] and \
                    any(docno_eq(item["docno"], r.get("DocumentNO", "")) for r in obj2["data"]):
                obj = obj2
    return obj


def load_checkpoint(checkpoint_path: str) -> dict[str, dict[str, Any]]:
    """读取断点：键 = query 或 query||query_docno"""
    done: dict[str, dict[str, Any]] = {}
    if os.path.exists(checkpoint_path):
        for line in open(checkpoint_path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            key = obj.get("query", "")
            if obj.get("query_docno"):
                key = "%s||%s" % (key, obj["query_docno"])
            done[key] = obj
    return done


# 分块提交粒度（2026-09-19）：提交/收集/落盘按此粒度交错，保证断点实时（见 execute_queries）
_CHUNK = 50


def execute_queries(plan: list[dict[str, Any]], cli: list[str], token: str,
                    checkpoint_path: str, workers: int = 2,
                    retry_failed: bool = False, pause: float = 0.2,
                    log: Callable[[str], None] = print) -> dict[str, dict[str, Any]]:
    """并发断点查询引擎。checkpoint 即查询 JSONL（逐条 append，可中断续跑）。
    安全参数：workers≤2、间隔≥0.2s（实测 6 并发 ~760 次即触发网关拦截/配额耗尽）。"""
    done = load_checkpoint(checkpoint_path)
    if retry_failed:
        todo = [it for it in plan if done.get(it["key"], {}).get("message") != "成功"]
        mode_txt = "重试失败"
    else:
        todo = [it for it in plan if it["key"] not in done]
        mode_txt = "新增"
    if not todo:
        log("checkpoint 无需%s查询" % mode_txt)
        return done
    log("待%s查询 %d / 总 %d（并发 %d）" % (mode_txt, len(todo), len(plan), workers))
    consec_auth_fail = 0
    lock = threading.Lock()
    stop = False
    n = 0
    # 2026-09-19 修复：**分块提交 + 分块收集**（原实现一次性提交全部 todo，再 `as_completed`
    # 收集 → 收集与 checkpoint 落盘被"提交阶段"（pause 0.2s × N，gov 1.2 万条 ≈ 42 min）整体推迟，
    # 表现为**断点长期不落盘**、认证失败门哨（连续 15 次）要等 42 min 后才可能触发。
    # 分块后每 ≤`_CHUNK` 条即落盘一次，中断可续、门哨实时。
    with open(checkpoint_path, "a", encoding="utf-8") as fh:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for start in range(0, len(todo), _CHUNK):
                if stop:
                    break
                futs = {}
                for it in todo[start:start + _CHUNK]:
                    if pause:
                        time.sleep(pause)
                    futs[ex.submit(fetch_item, cli, token, it)] = it
                for fut in as_completed(futs):
                    it = futs[fut]
                    try:
                        obj = fut.result()
                    except Exception as e:  # noqa: BLE001
                        obj = {"query": it["title"], "queried_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "message": "失败: " + str(e)[:300], "total": 0, "data": []}
                        if it["docno"]:
                            obj["query_docno"] = it["docno"]
                    if "90001" in obj["message"] or "积分用尽" in obj["message"]:
                        log("[配额] 北大法宝积分用尽(90001)，停止新增查询")
                        _wl_quota("90001", it)
                        stop = True
                        break
                    if "认证失败" in obj["message"]:
                        consec_auth_fail += 1
                        if consec_auth_fail >= 15:
                            log("[拦截] 连续 %d 次认证失败，暂停续跑（法宝配额耗尽/网关拦截，建议检查控制台）" % consec_auth_fail)
                            _wl_quota("auth_fail", it)
                            stop = True
                            break
                    else:
                        consec_auth_fail = 0
                    with lock:
                        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
                        fh.flush()
                        done[it["key"]] = obj
                    n += 1
                    if n % 100 == 0:
                        log("  进度 %d/%d" % (n, len(todo)))
    return done


def _wl_quota(blocker: str, item: dict) -> None:
    """P2-5（v2 §3.14.3 E 类断点）：配额/认证阻断的**显式化**。

    原状：`[配额]`/`[拦截]` 只写日志 —— 无人值守下"为什么昨晚没继续核验"无人知晓。
    现登记 worklist（处置：检查控制台后重跑 `cli.py timeliness verify`，断点续跑）。
    """
    try:
        from std_lib.common_lib import governance_store as _gs  # noqa: PLC0415
        key = f"{blocker}:{item.get('docno') or item.get('title') or ''}"[:80]
        _gs.worklist_add(
            "ingest_quota_blocked", key, stage="6.9", artifact_key="timeliness_verify",
            payload={"blocker": blocker, "query": item.get("title", ""),
                     "docno": item.get("docno", ""),
                     "message": str(item.get("message", ""))[:300]},
            suggestion="检查北大法宝控制台（积分/鉴权）；恢复后重跑 `cli.py timeliness verify`"
                       "（断点续跑）；确认已恢复 → resolve")
    except Exception:  # noqa: BLE001  旁路设施
        pass
