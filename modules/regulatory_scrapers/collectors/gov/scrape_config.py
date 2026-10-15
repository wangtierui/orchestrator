# -*- coding: utf-8 -*-
"""gov.config — gov 采集器的**配置与契约类型**层（E2 拆分，2026-10-16）。

`ScrapeConfig` 是采集配置的**唯一事实源**：`build_config()`（parse 层）构造它，
`write_outputs()` / `merge_and_write()`（outputs 层）消费它——原先它与产出逻辑混在
同一文件，导致「类型定义」与「落盘实现」互相牵扯。故提取为独立层（依赖方向：
config ← outputs ← parse）。

`CSV_COLUMNS` 同理：它是**产物列契约**，属输出侧，故与配置类型同层。
"""

import os as _os
import sys as _sys
from dataclasses import dataclass

_HERE = _os.path.dirname(_os.path.abspath(__file__))
# 三条路径都要：仓根（`std_lib`/`config` 等包）、上层 `collectors`、**本目录**
# （兄弟模块裸名回退所需——被 `importlib` 按路径单独加载时本目录不在 sys.path）。
for _p in (_HERE,
           _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", "..")),
           _os.path.dirname(_HERE)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
del _p, _os, _sys, _HERE



# --------------------------------------------------------------------------- #
# 产物列契约（CSV/JSONL 的列顺序——变更即破坏下游消费，须走 ADR）
# --------------------------------------------------------------------------- #
CSV_COLUMNS = [
    "title", "category", "publish_date", "pub_date_original", "effective_date",
    "issue_organ", "document_number", "detail_url", "summary", "source",
]


# --------------------------------------------------------------------------- #
# 默认请求头（采集 HTTP 的公共头；逐字搬自原 parse.py）
# --------------------------------------------------------------------------- #
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 注意：不要包含 br（brotli）。本机 urllib3 未安装 brotli 解码器，
    # 一旦服务器回 brotli 压缩，resp.content 会是未解压乱码字节，
    # decode_html 解出乱码 → 选择器全部落空 → 详情正文=0（海量 gov 子站因此被误判为空）。
    # 去掉 br 后服务器回退 gzip，urllib3 可正常解压。
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# --------------------------------------------------------------------------- #
# 采集配置契约
# --------------------------------------------------------------------------- #
@dataclass
class ScrapeConfig:
    source: str
    base_url: str
    category: str
    out_dir: str = "data/raw"
    max_pages: int = 0          # 0 = 不限制
    max_items: int = 0          # 0 = 不限制（仅列表条目数）
    fetch_details: bool = True
    details_limit: int = 0      # 仅对前 N 条抓取详情正文（0=全部，需配合 fetch_details）
    resume: bool = False        # 增量续抓：基于 out_dir 最新同源输出跳过已抓条目
    delay_min: float = 1.5      # 列表/详情请求间最小延时（秒）
    delay_max: float = 3.5      # 最大延时（秒）
    timeout: int = 30
    retries: int = 4
    summary_len: int = 200
    # 详情阶段断点落盘间隔（条；0=不落暂存）。供长时子源（zhengceku，全量约 30 小时）
    # 在中断后可续跑——主库只在全部结束后写一次，中途中断必须靠暂存文件兜底。
    checkpoint_every: int = 200
    # ---- N-190（2026-10-08）**增量治理**参数（两子源共用；判据见 zhengceku.should_stop_paging）----
    #: 连续 N 页无「新条目」（detail_url 不在主库）即停止翻页（**仅 zhengceku**；xzfgk 早有等价逻辑）。
    #: 为何需要：zhengceku 列表 629 页、原实现**恒扫全部页**，实测单轮 ~20+ 分钟纯空转
    #: （页面全是主库既有条目）。列表按发布日期倒序 ⇒ 首页起后续只会更旧。
    stop_after_empty_pages: int = 1
    #: 历史存量补全模式：**忽略早停**、扫完全部列表页（月度作业用；不进周度增量）。
    #: 保留原"持续补全 zhengceku 历史正文"的意图，但把它与"追新"**分离**，各自独立排程与计时。
    backfill: bool = False
    #: 详情阶段**软预算**（秒；0=不限）——到达即停止取新详情并**正常落盘退出**（rc=0，
    #: `stopped_reason=budget`）。为何要软预算：撞上层 7200s **硬超时**会被强杀，
    #: 而"未走完流程的产物"仍会落盘并被下游采用（N-152 的教训）。
    max_seconds: float = 0.0
