# -*- coding: utf-8 -*-
"""容错编排层（横切关注点，对采集器透明）。

四源共用同一套容错：全局/按源熔断、失败分类、死信持久化、冷却补抓、可观测。
采集器无需写任何重试/冷却逻辑——它们只声明端点、解析、永久状态码；本层在
统一请求路径与 pipeline 包装点自动施加。

设计约束
--------
- 失败项不静默丢弃：临时失败（403/429/5xx/网络）进死信表，可冷却补抓或人工旁路重试。
- 永久失败（404/410）直接跳过，不进死信。
- 不死循环：单条 attempt_count 硬上限（默认 4）+ 单 run 补抓趟数硬上限（默认 3）
  + 某趟零回收即早停，三重保险。
- 死信不丢：达上限的行保持 exhausted + needs_review，永不被删；专用命令可重置重试。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

LOG = logging.getLogger("refactor.collectors.lib.resilience")

UTC = timezone.utc


class FailureKind(str, Enum):
    TRANSIENT = "transient"   # 403/429/5xx/网络异常：可重试，进死信
    PERMANENT = "permanent"   # 404/410：永久跳过
    PARSE = "parse"           # 解析/其它异常：按源处理（仍进死信，受上限约束）


# 与 lib/http 的默认状态集合保持一致（单一事实来源见 lib/http.DEFAULT_*）。
PERMANENT_STATUSES = frozenset({404, 410})
TRANSIENT_STATUSES = frozenset({403, 429, 500, 502, 503, 504})


def extract_status(exc: BaseException | str) -> Optional[int]:
    """从异常/错误消息中尽力抽取 HTTP 状态码；取不到返回 None。"""
    if exc is None:
        return None
    if hasattr(exc, "status") and isinstance(getattr(exc, "status"), int):
        return exc.status  # type: ignore[attr-defined]
    s = str(exc)
    # 形如 "HTTP 403" / "HTTP 403 @ ..." / requests 的 "403 Client Error"
    idx = s.find("HTTP ")
    if idx >= 0:
        tail = s[idx + 4:idx + 8].strip()
        digits = "".join(ch for ch in tail if ch.isdigit())
        if digits:
            return int(digits)
    if "403" in s or "Forbidden" in s:
        return 403
    if "429" in s or "Too Many" in s:
        return 429
    return None


class FailureClassifier:
    """HTTP 状态 / 异常 → 失败类别。"""

    @staticmethod
    def classify(status: Optional[int], error: Optional[str] = None) -> FailureKind:
        if status in PERMANENT_STATUSES:
            return FailureKind.PERMANENT
        if status in TRANSIENT_STATUSES or status is None:
            return FailureKind.TRANSIENT
        # 其它状态码（如 400/401）或解析异常：归为 parse（仍进死信但受上限约束）
        return FailureKind.PARSE


@dataclass
class CircuitBreakerConfig:
    window: int = 20                 # 滑动窗口样本数
    failure_rate_threshold: float = 0.5  # 失败率阈值（触发冷却）
    cooldown_seconds: float = 45.0   # 冷却时长


class CircuitBreaker:
    """按源熔断：滑动窗口内失败率超阈值则触发冷却，期间暂停该源抓取。"""

    def __init__(self, cfg: CircuitBreakerConfig | None = None):
        self.cfg = cfg or CircuitBreakerConfig()
        self._window: list[bool] = []
        self._open_until = 0.0
        self._lock = threading.Lock()

    def record(self, success: bool) -> None:
        with self._lock:
            self._window.append(success)
            if len(self._window) > self.cfg.window:
                self._window.pop(0)

    def tripped(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        with self._lock:
            if now < self._open_until:
                return True
            if len(self._window) < self.cfg.window:
                return False
            fails = sum(1 for s in self._window if not s)
            rate = fails / len(self._window)
            if rate >= self.cfg.failure_rate_threshold:
                self._open_until = now + self.cfg.cooldown_seconds
                LOG.warning(
                    "熔断触发：近 %d 请求失败率 %.0f%%，冷却 %.0fs",
                    len(self._window), 100 * rate, self.cfg.cooldown_seconds,
                )
                return True
            return False

    def remaining_cooldown(self, now: float | None = None) -> float:
        now = now if now is not None else time.monotonic()
        with self._lock:
            return max(0.0, self._open_until - now)


@dataclass
class FetchMetrics:
    """单次抓取运行的结构化指标（可观测）。"""
    attempted: int = 0
    ok: int = 0
    status_403: int = 0
    status_429: int = 0
    status_5xx: int = 0
    status_404_perm: int = 0
    status_net: int = 0
    parse_err: int = 0
    recovered: int = 0
    remaining: int = 0

    def record_attempt(self) -> None:
        self.attempted += 1

    def record_ok(self) -> None:
        self.ok += 1

    def record_status(self, status: Optional[int]) -> None:
        if status == 403:
            self.status_403 += 1
        elif status == 429:
            self.status_429 += 1
        elif status is not None and 500 <= status <= 599:
            self.status_5xx += 1
        elif status in PERMANENT_STATUSES:
            self.status_404_perm += 1
        elif status is None:
            self.status_net += 1        # 网络层失败（连接/超时）
        else:
            self.parse_err += 1

    def to_dict(self) -> dict:
        return {
            "attempted": self.attempted,
            "ok": self.ok,
            "status_403": self.status_403,
            "status_429": self.status_429,
            "status_5xx": self.status_5xx,
            "status_404_perm": self.status_404_perm,
            "status_net": self.status_net,
            "parse_err": self.parse_err,
            "recovered": self.recovered,
            "remaining": self.remaining,
        }


class DeadLetterStore:
    """失败项死信存储。支持 PG 后端（持久化）与内存后端（测试/无 PG 时降级）。

    设计：失败项不静默丢弃，持久化后可被冷却补抓趟重查；达上限标记为
    exhausted + needs_review，永不删除；专用命令可重置重试。
    """

    def __init__(self, backend: str = "pg", db_url: str | None = None):
        self.backend = backend
        self._db_url = db_url
        self._engine = None
        self._lock = threading.Lock()
        self._mem: dict[tuple[str, str], "DeadLetter"] = {}
        if backend == "pg":
            self._init_pg()

    # ---- PG 后端 ----
    def _init_pg(self) -> None:
        try:
            from sqlalchemy import create_engine
            if not self._db_url:
                from ..storage.config import StorageSettings
                self._db_url = StorageSettings.load().db_url
            if not self._db_url:
                raise ValueError("backend=pg 但 REFACTOR_DB_URL 未设置")
            self._engine = create_engine(self._db_url, future=True)
            self._ensure_tables()
        except Exception as e:  # noqa: BLE001
            LOG.warning("死信存储 PG 不可用，降级为内存：%s", e)
            self.backend = "memory"
            self._engine = None

    def _ensure_tables(self) -> None:
        from sqlalchemy import text
        from ..storage.raw_store import FETCH_DEAD_LETTER, FETCH_RUNS
        with self._engine.begin() as conn:
            FETCH_DEAD_LETTER.create(conn, checkfirst=True)
            FETCH_RUNS.create(conn, checkfirst=True)
            # fetch_runs 用 raw DDL 保证列齐（raw_store 的 Table 已含全部列）
            conn.execute(text(
                "COMMENT ON TABLE fetch_dead_letter IS 'fetch_dead_letter'"))

    # ---- 公共 API ----
    def enqueue(self, dl: "DeadLetter") -> None:
        if self.backend == "memory":
            with self._lock:
                self._mem[(dl.source, dl.doc_key)] = dl
            return
        from sqlalchemy import insert
        from ..storage.raw_store import FETCH_DEAD_LETTER
        now = datetime.now(UTC)
        with self._engine.begin() as conn:
            conn.execute(insert(FETCH_DEAD_LETTER).values(
                source=dl.source, doc_key=dl.doc_key, entry=dl.entry,
                endpoint=dl.endpoint, params=dl.params,
                attempt_count=dl.attempt_count, max_attempts=dl.max_attempts,
                last_status=dl.last_status, last_error=dl.last_error,
                status=dl.status, needs_review=dl.needs_review,
                next_attempt_after=dl.next_attempt_after,
                created_at=now, updated_at=now,
            ))

    def get(self, source: str, doc_key: str) -> Optional["DeadLetter"]:
        if self.backend == "memory":
            return self._mem.get((source, doc_key))
        from sqlalchemy import select
        from ..storage.raw_store import FETCH_DEAD_LETTER
        with self._engine.connect() as conn:
            row = conn.execute(
                select(FETCH_DEAD_LETTER).where(
                    FETCH_DEAD_LETTER.c.source == source,
                    FETCH_DEAD_LETTER.c.doc_key == doc_key)
            ).mappings().first()
            return _row_to_dl(row) if row else None

    def pending_due(self, source: str, now: datetime) -> list["DeadLetter"]:
        if self.backend == "memory":
            out = [dl for dl in self._mem.values()
                   if dl.source == source and dl.status == "pending"
                   and dl.attempt_count < dl.max_attempts
                   and (dl.next_attempt_after is None or dl.next_attempt_after <= now)]
            return out
        from sqlalchemy import select
        from ..storage.raw_store import FETCH_DEAD_LETTER
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(FETCH_DEAD_LETTER).where(
                    FETCH_DEAD_LETTER.c.source == source,
                    FETCH_DEAD_LETTER.c.status == "pending",
                    FETCH_DEAD_LETTER.c.attempt_count < FETCH_DEAD_LETTER.c.max_attempts,
                    (FETCH_DEAD_LETTER.c.next_attempt_after.is_(None)
                     | (FETCH_DEAD_LETTER.c.next_attempt_after <= now)),
                )
            ).mappings().all()
            return [_row_to_dl(r) for r in rows]

    def mark_retried(self, source: str, doc_key: str, last_status: Optional[int],
                     last_error: Optional[str], next_attempt_after: datetime,
                     now: datetime) -> None:
        dl = self.get(source, doc_key)
        attempt = (dl.attempt_count if dl else 0) + 1
        max_a = dl.max_attempts if dl else 4
        exhausted = attempt >= max_a
        if self.backend == "memory":
            with self._lock:
                cur = self._mem.get((source, doc_key))
                if cur is None:
                    return
                cur.attempt_count = attempt
                cur.last_status = last_status
                cur.last_error = last_error
                cur.updated_at = now
                if exhausted:
                    cur.status = "exhausted"
                    cur.needs_review = True
                    cur.next_attempt_after = None
                else:
                    cur.status = "pending"
                    cur.next_attempt_after = next_attempt_after
            return
        from sqlalchemy import update
        from ..storage.raw_store import FETCH_DEAD_LETTER
        with self._engine.begin() as conn:
            conn.execute(update(FETCH_DEAD_LETTER).where(
                FETCH_DEAD_LETTER.c.source == source,
                FETCH_DEAD_LETTER.c.doc_key == doc_key).values(
                attempt_count=attempt, last_status=last_status,
                last_error=last_error, updated_at=now,
                status="exhausted" if exhausted else "pending",
                needs_review=exhausted,
                next_attempt_after=None if exhausted else next_attempt_after,
            ))

    def mark_resolved(self, source: str, doc_key: str) -> None:
        self._set_status(source, doc_key, "resolved", needs_review=False)

    def mark_exhausted(self, source: str, doc_key: str, last_error: Optional[str] = None) -> None:
        if self.backend == "memory":
            with self._lock:
                cur = self._mem.get((source, doc_key))
                if cur is None:
                    return
                cur.status = "exhausted"
                cur.needs_review = True
                cur.last_error = last_error
                cur.updated_at = datetime.now(UTC)
            return
        from sqlalchemy import update
        from ..storage.raw_store import FETCH_DEAD_LETTER
        with self._engine.begin() as conn:
            conn.execute(update(FETCH_DEAD_LETTER).where(
                FETCH_DEAD_LETTER.c.source == source,
                FETCH_DEAD_LETTER.c.doc_key == doc_key).values(
                status="exhausted", needs_review=True, last_error=last_error,
                updated_at=datetime.now(UTC)))

    def reset_for_retry(self, source: str, *, only_review: bool = True) -> int:
        """人工/旁路重试：重置待重试项（默认仅 needs_review），返回重置条数。"""
        if self.backend == "memory":
            n = 0
            with self._lock:
                for dl in self._mem.values():
                    if dl.source != source:
                        continue
                    if only_review and not dl.needs_review:
                        continue
                    dl.attempt_count = 0
                    dl.status = "pending"
                    dl.needs_review = False
                    dl.next_attempt_after = None
                    n += 1
            return n
        from sqlalchemy import update
        from ..storage.raw_store import FETCH_DEAD_LETTER
        now = datetime.now(UTC)
        with self._engine.begin() as conn:
            stmt = update(FETCH_DEAD_LETTER).where(
                FETCH_DEAD_LETTER.c.source == source)
            if only_review:
                stmt = stmt.where(FETCH_DEAD_LETTER.c.needs_review.is_(True))
            stmt = stmt.values(
                attempt_count=0, status="pending", needs_review=False,
                next_attempt_after=None, updated_at=now)
            res = conn.execute(stmt)
            return res.rowcount or 0

    def count_remaining(self, source: str) -> int:
        if self.backend == "memory":
            return sum(1 for dl in self._mem.values()
                       if dl.source == source and dl.status in ("pending", "exhausted"))
        from sqlalchemy import select, func
        from ..storage.raw_store import FETCH_DEAD_LETTER
        with self._engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(FETCH_DEAD_LETTER).where(
                FETCH_DEAD_LETTER.c.source == source,
                FETCH_DEAD_LETTER.c.status.in_(["pending", "exhausted"]))).scalar_one()

    def _set_status(self, source: str, doc_key: str, status: str, needs_review: bool) -> None:
        if self.backend == "memory":
            with self._lock:
                cur = self._mem.get((source, doc_key))
                if cur is None:
                    return
                cur.status = status
                cur.needs_review = needs_review
                cur.updated_at = datetime.now(UTC)
            return
        from sqlalchemy import update
        from ..storage.raw_store import FETCH_DEAD_LETTER
        with self._engine.begin() as conn:
            conn.execute(update(FETCH_DEAD_LETTER).where(
                FETCH_DEAD_LETTER.c.source == source,
                FETCH_DEAD_LETTER.c.doc_key == doc_key).values(
                status=status, needs_review=needs_review, updated_at=datetime.now(UTC)))

    # ---- 运行指标账本 ----
    def write_run(self, source: str, started_at: datetime, finished_at: datetime,
                  metrics: FetchMetrics) -> None:
        if self.backend == "memory":
            return
        from sqlalchemy import insert
        from ..storage.raw_store import FETCH_RUNS
        try:
            with self._engine.begin() as conn:
                conn.execute(insert(FETCH_RUNS).values(
                    source=source, started_at=started_at, finished_at=finished_at,
                    attempted=metrics.attempted, ok=metrics.ok,
                    status_403=metrics.status_403, status_429=metrics.status_429,
                    status_5xx=metrics.status_5xx, status_404_perm=metrics.status_404_perm,
                    status_net=metrics.status_net,
                    parse_err=metrics.parse_err, recovered=metrics.recovered,
                    remaining=metrics.remaining, summary=metrics.to_dict()))
        except Exception as e:  # noqa: BLE001
            LOG.warning("写入 fetch_runs 失败：%s", e)


@dataclass
class DeadLetter:
    source: str
    doc_key: str
    entry: Any = None
    endpoint: str = ""
    params: dict = field(default_factory=dict)
    attempt_count: int = 0
    max_attempts: int = 4
    last_status: Optional[int] = None
    last_error: Optional[str] = None
    status: str = "pending"   # pending | exhausted | resolved
    needs_review: bool = False
    next_attempt_after: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _row_to_dl(row) -> DeadLetter:
    return DeadLetter(
        source=row["source"], doc_key=row["doc_key"], entry=row.get("entry"),
        endpoint=row.get("endpoint") or "", params=row.get("params") or {},
        attempt_count=row.get("attempt_count") or 0,
        max_attempts=row.get("max_attempts") or 4,
        last_status=row.get("last_status"), last_error=row.get("last_error"),
        status=row.get("status") or "pending",
        needs_review=bool(row.get("needs_review")),
        next_attempt_after=row.get("next_attempt_after"),
        created_at=row.get("created_at"), updated_at=row.get("updated_at"),
    )


class ResilienceCoordinator:
    """容错编排中枢：按源熔断、指标、死信读写、补抓调度。transport 与 pipeline 共用。"""

    def __init__(self, dead_letter_store: DeadLetterStore | None = None,
                 breaker_cfg: CircuitBreakerConfig | None = None):
        self._store = dead_letter_store or DeadLetterStore(backend="pg")
        self._breakers: dict[str, CircuitBreaker] = {}
        self._breaker_cfg = breaker_cfg or CircuitBreakerConfig()
        self._metrics = FetchMetrics()
        self._active_source: str | None = None
        self._lock = threading.Lock()
        self._run_started_at: datetime | None = None

    # ---- 源上下文 ----
    def set_source(self, source: str) -> None:
        self._active_source = source

    @property
    def active_source(self) -> str:
        return self._active_source or "default"

    def _breaker(self, source: str) -> CircuitBreaker:
        with self._lock:
            b = self._breakers.get(source)
            if b is None:
                b = CircuitBreaker(self._breaker_cfg)
                self._breakers[source] = b
            return b

    # ---- 指标 ----
    @property
    def metrics(self) -> FetchMetrics:
        return self._metrics

    def reset_metrics(self) -> None:
        self._metrics = FetchMetrics()

    # ---- 熔断 ----
    def record_request(self, status: Optional[int]) -> None:
        """transport 每次请求后调用：仅更新按源熔断（指标由 pipeline 按条目计）。"""
        try:
            source = self.active_source
            success = status is not None and 200 <= status < 400
            self._breaker(source).record(success)
        except Exception:  # noqa: BLE001  容错层自身永不抛出
            pass

    def pause_if_needed(self, source: str | None = None) -> None:
        """抓取前调用：若该源熔断冷却中则暂停等待。"""
        try:
            src = source or self.active_source
            cd = self._breaker(src).remaining_cooldown()
            if cd > 0:
                LOG.warning("源 %s 熔断冷却中，暂停 %.0fs", src, cd)
                time.sleep(min(cd, self._breaker_cfg.cooldown_seconds))
        except Exception:  # noqa: BLE001
            pass

    def breaker_open(self, source: str | None = None) -> bool:
        return self._breaker(source or self.active_source).tripped()

    # ---- 失败分类 + 死信产出 ----
    def classify(self, status: Optional[int], error: Optional[str] = None) -> FailureKind:
        return FailureClassifier.classify(status, error)

    def enqueue_failure(self, source: str, doc_key: str, entry: Any,
                        status: Optional[int], error: Optional[str],
                        endpoint: str = "", params: dict | None = None,
                        cooldown_seconds: float | None = None) -> None:
        """handle_new 抛异常时调用：按分类产出死信（永久失败不进死信）。

        注意：指标计数（含 404_perm）由 pipeline 统一在异常处 record_status 完成，
        此处仅负责死信持久化，避免重复计数。
        """
        kind = self.classify(status, error)
        if kind == FailureKind.PERMANENT:
            return
        cd = cooldown_seconds or self._breaker_cfg.cooldown_seconds
        now = datetime.now(UTC)
        dl = DeadLetter(
            source=source, doc_key=str(doc_key), entry=entry, endpoint=endpoint,
            params=params or {}, last_status=status, last_error=(error or "")[:500],
            status="pending", needs_review=False,
            next_attempt_after=now + timedelta(seconds=cd),
            created_at=now, updated_at=now,
        )
        try:
            self._store.enqueue(dl)
        except Exception as e:  # noqa: BLE001
            LOG.warning("死信入队失败（%s/%s）：%s", source, doc_key, e)

    # ---- 冷却补抓趟 ----
    def drain(self, source: str, retry_fn, *, max_passes: int = 3,
              pass_cooldown: float = 30.0) -> tuple[int, int]:
        """冷却补抓：重放 pending 死信的 handle_new(entry)。

        三重保险：单条 attempt_count 硬上限（store 内）、单 run 补抓趟数硬上限、
        某趟零回收即早停。返回 (recovered, remaining)。
        """
        recovered = 0
        for _ in range(max_passes):
            now = datetime.now(UTC)
            due = self._store.pending_due(source, now)
            if not due:
                break
            pass_recovered = 0
            for dl in due:
                self.pause_if_needed(source)
                try:
                    rec = retry_fn(dl.entry)
                except Exception as e:  # noqa: BLE001
                    st = extract_status(e)
                    self._store.mark_retried(
                        source, dl.doc_key, st, str(e)[:500],
                        next_attempt_after=datetime.now(UTC)
                        + timedelta(seconds=pass_cooldown),
                        now=datetime.now(UTC))
                    continue
                if rec is None:
                    self._store.mark_resolved(source, dl.doc_key)
                    pass_recovered += 1
                    continue
                # 复抓成功：交由调用方落库（retry_fn 仅返回记录；落库在 pipeline 做）
                self._store.mark_resolved(source, dl.doc_key)
                pass_recovered += 1
                _DRAIN_RECOVERED_RECORDS.append(rec)
            recovered += pass_recovered
            if pass_recovered == 0:  # 自适应早停
                break
        self._metrics.recovered += recovered
        self._metrics.remaining = self._store.count_remaining(source)
        return recovered, self._metrics.remaining

    # ---- 运行账本 ----
    def start_run(self, source: str) -> None:
        self.set_source(source)
        self.reset_metrics()
        self._run_started_at = datetime.now(UTC)

    def end_run(self, source: str | None = None) -> dict:
        src = source or self.active_source
        finished = datetime.now(UTC)
        self._metrics.remaining = self._store.count_remaining(src)
        summary = self._metrics.to_dict()
        try:
            self._store.write_run(src, self._run_started_at or finished, finished, self._metrics)
        except Exception as e:  # noqa: BLE001
            LOG.warning("fetch_runs 写入失败：%s", e)
        return summary


# drain 期间复抓成功的记录暂存（由 pipeline 统一落库），避免死信层耦合存储。
_DRAIN_RECOVERED_RECORDS: list = []


def take_drain_records() -> list:
    out = _DRAIN_RECOVERED_RECORDS[:]
    _DRAIN_RECOVERED_RECORDS.clear()
    return out


_COORDINATOR: ResilienceCoordinator | None = None
_COORD_LOCK = threading.Lock()


def get_coordinator() -> ResilienceCoordinator:
    global _COORDINATOR
    if _COORDINATOR is None:
        with _COORD_LOCK:
            if _COORDINATOR is None:
                _COORDINATOR = ResilienceCoordinator()
    return _COORDINATOR
