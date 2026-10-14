# -*- coding: utf-8 -*-
"""std_lib.common_lib.governance_store — 治理库（governance.db）唯一读写实现

定位（见 `reports/数据流转与存储交互优化方案_20260917.md` §4）：
    本模块是 **阶段 1「治理库骨架」的唯一实现**，承载「三轨制」中的**治理轨**——
    只放"会被人改、会被跨模块读、需要历史"的**小体量元数据**（目标 < 50 MB），
    **不承载语料正文、原件、交付文档**（那些留在文件系统 = 内容轨）。

**W4-c 五拆**（2026-10，方案 §7.5.1；原 1507 行单文件）：
    _db.py          Schema/连接（TABLES/_DDL/connect/db_path/init_db/now_iso）
    ledger.py       运行台账（run_log/run_step/gate_result + 版本指纹）
    watermark.py    产物水位与依赖边
    worklist.py     待办队列（v2 §3.14.3）
    projections.py  元数据投影四表 + audit/artifact + 快照导出

公共 API 经本门面 re-export（import 面不变——契约测试 tests/test_import_surface.py
冻结）；包内分层引用方向：ledger/watermark/worklist/projections → _db。

设计纪律（不变）：
  1) **不硬编码绝对路径**：库路径经 `paths.DATA_DIR` 派生，环境变量
     `REG_ORCH_GOVERNANCE_DB` 可覆盖（测试隔离 / 非标准部署）。
  2) **失败不阻断主链**：本模块是**旁路观测设施**，任何写入异常在调用侧须降级为告警。
  3) **WAL + busy_timeout**：读并发友好；写入经 sqlite 事务（跨表原子）。
  4) 主键/代理键**不可变**（RFN/IPN 教训）：本模块不重算任何现有代理键。
"""

from __future__ import annotations

# _SCHEMA_INT/_TZ 为**转供 re-export**（noqa F401）：测试与消费方的既有引用路径
# （如 tests/test_governance_watermark.py 的迁移断言）经包门面访问。
from ._db import (  # noqa: F401
    _SCHEMA_INT,
    _TZ,
    SCHEMA_VERSION,
    TABLES,
    WORKLIST_OPEN_UNIQUE,
    connect,
    db_path,
    enabled,
    init_db,
    now_iso,
    repo_root,
)
from .ledger import (
    current_run_id,
    file_version,
    last_run_with_steps,
    list_gate_results,
    list_runs,
    record_gate_results,
    run_finish,
    run_start,
    step_record,
    steps_of,
    version_of_file,
    version_of_files,
)
from .projections import (
    export_snapshot,
    list_artifacts,
    list_audit,
    log_audit,
    project_metadata,
    snapshot,
    table_count,
    table_digest,
    table_rows,
    upsert_artifacts,
    verify_projection,
)
from .watermark import (
    check_dependencies,
    dependency_edges,
    get_watermark,
    list_watermarks,
    record_watermark,
)
from .worklist import (
    worklist_add,
    worklist_list,
    worklist_resolve,
    worklist_stats,
)

__all__ = [
    "SCHEMA_VERSION",
    "TABLES",
    "WORKLIST_OPEN_UNIQUE",
    "repo_root",
    "db_path",
    "enabled",
    "connect",
    "init_db",
    "now_iso",
    "current_run_id",
    "run_start",
    "run_finish",
    "step_record",
    "steps_of",
    "last_run_with_steps",
    "list_runs",
    "version_of_file",
    "file_version",
    "version_of_files",
    "record_gate_results",
    "list_gate_results",
    "record_watermark",
    "get_watermark",
    "list_watermarks",
    "dependency_edges",
    "check_dependencies",
    "worklist_add",
    "worklist_list",
    "worklist_resolve",
    "worklist_stats",
    "log_audit",
    "list_audit",
    "upsert_artifacts",
    "list_artifacts",
    "project_metadata",
    "table_rows",
    "table_count",
    "table_digest",
    "verify_projection",
    "export_snapshot",
    "snapshot",
]
