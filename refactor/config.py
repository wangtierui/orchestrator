# -*- coding: utf-8 -*-
"""refactor 子系统统一配置（单一配置源 + 环境变量覆盖，12-factor）。

设计目标
--------
- 所有散落的配置项（数据库连接、存储后端、RAG 检索参数、抓取/OCR 调优开关）集中到
  ``refactor/config.yaml`` 这一份文件，作为提交进仓库的 dev / 默认配置。
- 同名大写环境变量可覆盖任意一项（用于不同环境 / 密钥），优先级：
  CLI 参数 > 环境变量 > config.yaml > 代码内建默认。
- 导入本模块时会把解析后的值**回填 os.environ**（仅当该变量未设置），
  使既有 ``os.environ.get(...)`` 读取无需改动即可生效（兼容过渡）。
- 新代码直接读 :data:`settings` 单例，不再散落读取环境变量。

URL 归一化
----------
``database.url`` 采用 SQLAlchemy 风格 ``postgresql+psycopg://``（SQLAlchemy 消费者直接用）。
psycopg 直连消费者经 :func:`psycopg_dsn` 自动剥掉 ``+psycopg`` 前缀，消除项目里
原先各处手工 ``.replace("postgresql+psycopg://", "postgresql://")`` 的散落写法。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML 为声明依赖，正常必存在
    yaml = None


_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

# 配置项 (section, field) -> 环境变量名（用于“环境变量覆盖”与“回填 os.environ”）
_ENV_MAP = {
    ("database", "url"): "REFACTOR_DB_URL",
    ("database", "storage_backend"): "REFACTOR_STORAGE",
    ("database", "raw_dir"): "REFACTOR_RAW_DIR",
    ("scrape", "zc_attach_fast"): "ZC_ATTACH_FAST",
    ("scrape", "zc_fast_min_body"): "ZC_FAST_MIN_BODY",
    ("scrape", "zc_no_word_com"): "ZC_NO_WORD_COM",
    ("ocr", "require_ocr"): "REFACTOR_REQUIRE_OCR",
    ("ocr", "ocr_func_timeout"): "REFACTOR_OCR_FUNC_TIMEOUT",
    ("ocr", "tessdata_dir"): "OCR_TESSDATA_DIR",
    ("ocr", "tesseract_bin"): "OCR_TESSERACT_BIN",
    ("ocr", "paddleocr_root"): "OCR_PADDLEOCR_ROOT",
    ("ocr", "lo_bin"): "LO_BIN",
}

# 默认配置（config.yaml 缺失或某项缺省时的兜底）
@dataclass
class DatabaseSettings:
    url: str = "postgresql+psycopg://root:5462@localhost:5432/test"
    storage_backend: str = "postgres"
    raw_dir: str = "data/raw"


@dataclass
class RagSettings:
    # embedding 固定为 BGE-M3（rag/embedding.py），此处只配检索参数。
    top_k: int = 5
    vector_store: dict = field(default_factory=lambda: {
        "name": "pgvector",
        "host": "localhost", "port": 5432,
        "user": "root", "password": "5462",
        "database": "test", "table_name": "clause_chunks",
    })


@dataclass
class ScrapeSettings:
    zc_attach_fast: bool = False
    zc_fast_min_body: int = 200
    zc_no_word_com: bool = False


@dataclass
class OcrSettings:
    require_ocr: bool = False
    ocr_func_timeout: int = 300
    tessdata_dir: str = ""
    tesseract_bin: str = ""
    paddleocr_root: str = ""
    lo_bin: str = ""


@dataclass
class Settings:
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    rag: RagSettings = field(default_factory=RagSettings)
    scrape: ScrapeSettings = field(default_factory=ScrapeSettings)
    ocr: OcrSettings = field(default_factory=OcrSettings)


def _type_name(tp) -> str:
    return tp if isinstance(tp, str) else getattr(tp, "__name__", "")


def _coerce(tp, val):
    """按字段类型把（来自 YAML / 环境变量的）原始值归一。"""
    t = _type_name(tp)
    if t == "bool":
        return str(val).strip().lower() in ("1", "true", "yes", "on")
    if t == "int":
        return int(str(val))
    return str(val)


def _load_yaml() -> dict:
    if yaml is None or not _CONFIG_PATH.exists():
        return {}
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:  # 配置损坏不应让整个程序崩溃，回退到内建默认
        return {}


def _build_settings() -> Settings:
    data = _load_yaml()
    s = Settings()
    sections = (
        ("database", DatabaseSettings, s.database),
        ("rag", RagSettings, s.rag),
        ("scrape", ScrapeSettings, s.scrape),
        ("ocr", OcrSettings, s.ocr),
    )
    # 1) config.yaml 覆盖内建默认
    for sec_name, sec_cls, sec_obj in sections:
        sec_data = data.get(sec_name, {}) or {}
        for f in fields(sec_cls):
            if f.name in sec_data:
                raw = sec_data[f.name]
                if _type_name(f.type) == "dict" or isinstance(raw, dict):
                    setattr(sec_obj, f.name, raw)
                else:
                    setattr(sec_obj, f.name, _coerce(f.type, raw))
    # 2) 环境变量覆盖（最高优先级，仅低于 CLI 参数）
    for (sec_name, field_name), env_name in _ENV_MAP.items():
        env_val = os.environ.get(env_name)
        if env_val is not None:
            sec_obj = getattr(s, sec_name)
            fld = next(f for f in fields(type(sec_obj)) if f.name == field_name)
            setattr(sec_obj, field_name, _coerce(fld.type, env_val))
    return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回（缓存的）Settings 单例。"""
    return _build_settings()


# 模块级单例，导入即可用
settings = get_settings()


def _seed_environ() -> None:
    """把解析后的配置回填 os.environ（仅当该变量未设置），兼容既有 os.environ.get 读取。"""
    for (sec_name, field_name), env_name in _ENV_MAP.items():
        if os.environ.get(env_name) is None:
            val = getattr(getattr(settings, sec_name), field_name)
            os.environ[env_name] = str(val).lower() if isinstance(val, bool) else str(val)


_seed_environ()


# ---------------------------------------------------------------------------
# URL 归一化助手：消除 postgresql+psycopg:// 与 psycopg 直连的写法分歧
# ---------------------------------------------------------------------------
_SA_RE = re.compile(r"^postgresql\+[a-z0-9]+://")


def psycopg_dsn(url: str) -> str:
    """SQLAlchemy 风格（postgresql+psycopg://）转 psycopg 直连串（postgresql://）。"""
    return _SA_RE.sub("postgresql://", url or "")
