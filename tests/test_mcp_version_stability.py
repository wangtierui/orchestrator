# -*- coding: utf-8 -*-
"""tests/test_mcp_version_stability.py — MCP 资源**版本稳定性**守卫（第一百零六批）。

事故（2026-10-21，全量测试实测偶发失败）
----------------------------------------
`tests/test_mcp_server.py::test_list_and_read_versions_agree` 偶发 FAIL：

    AssertionError: orch://backlog_health
    assert '51d83b29df1e' == '7c02881bef71'

**根因是时序**：`_backlog_health()` 的 payload 含
`generated_at = datetime.now().isoformat(timespec="seconds")`。
`resources/list` 与 `resources/read` **各调一次**计算函数；若两次**跨过秒边界**，
payload 就差一个字段 ⇒ `_sha12` 不同 ⇒ 断言失败。
（这解释了"有时过、有时不过"，且与机器快慢/前序耗时有关。）

**修法**：版本哈希前先剔除**易变字段**（`_VOLATILE_KEYS`）。
语义依据：`version` 回答的是"**事实源**变了吗"（agent 据此判断缓存是否过期）；
时间戳每次都在变，算进去会让版本永不稳，缓存机制失去意义。

守卫四组
--------
A 只差时间戳 ⇒ 版本**相同**
B 内容真变 ⇒ 版本**不同**（不能把变化一起吞掉）
C 嵌套层的时间戳也被剔除
D 真实资源面 `list` 与 `read` 的版本一致（行为级）
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestVolatileIgnored:
    """A. 只差时间戳 ⇒ 同版本。"""

    @pytest.mark.parametrize("key", ["generated_at", "as_of", "fetched_at",
                                     "computed_at", "timestamp", "updated_at"])
    def test_timestamp_does_not_change_version(self, key: str) -> None:
        from mcp_server.resources import _sha12

        a = {"n": 1, key: "2026-10-21T10:00:00"}
        b = {"n": 1, key: "2026-10-21T10:00:01"}
        assert _sha12(a) == _sha12(b), (
            f"{key} 参与了版本哈希 ⇒ 跨秒即版本漂移（时序 flake 的根因）")


class TestRealChangeStillDetected:
    """B. 事实源真变 ⇒ 版本必须变（别把有用信号一起吞掉）。"""

    def test_value_change_changes_version(self) -> None:
        from mcp_server.resources import _sha12

        assert _sha12({"open": 10}) != _sha12({"open": 11})

    def test_structure_change_changes_version(self) -> None:
        from mcp_server.resources import _sha12

        assert _sha12({"a": 1}) != _sha12({"a": 1, "b": 2})


class TestNestedVolatile:
    """C. 嵌套层的时间戳同样剔除。"""

    def test_nested_timestamp_ignored(self) -> None:
        from mcp_server.resources import _sha12

        a = {"metrics": {"open": 5, "generated_at": "t1"},
             "verdict": {"ok": True}}
        b = {"metrics": {"open": 5, "generated_at": "t2"},
             "verdict": {"ok": True}}
        assert _sha12(a) == _sha12(b)

    def test_nested_value_still_detected(self) -> None:
        from mcp_server.resources import _sha12

        a = {"metrics": {"open": 5, "generated_at": "t1"}}
        b = {"metrics": {"open": 6, "generated_at": "t1"}}
        assert _sha12(a) != _sha12(b)


class TestRealResourceSurface:
    """D. 行为级：真实资源面 list/read 版本必须一致（可重复）。"""

    def test_versions_agree_repeatedly(self) -> None:
        from mcp_server.resources import list_resources, read_resource

        for _ in range(3):          # 重复多轮，跨秒也须一致
            for r in list_resources():
                got = read_resource(r["uri"])
                assert got.get("version") == r["version"], (
                    f'{r["uri"]} 的 list/read 版本不一致（缓存语义被破坏）')
