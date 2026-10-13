# -*- coding: utf-8 -*-
"""clean 载入层：双格式原始记录读取（JSONL 逐行 / .json 流式）。"""
import json
from typing import Any


def load_raw_records(path: str) -> list[dict[str, Any]]:
    if path.endswith(".jsonl"):
        out: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict) and len(rec) == 1 and "_meta" in rec:
                    continue
                out.append(rec)
        return out
    dec = json.JSONDecoder()
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        buf = ""
        start = -1
        while start < 0:
            chunk = f.read(1 << 20)
            if not chunk:
                raise ValueError(f"无法识别的原始数据格式：{path}")
            buf += chunk
            head = buf.lstrip()
            if head.startswith("["):
                start = buf.index("[")
            else:
                for key in ('"records"', '"items"', '"data"', '"results"'):
                    k = buf.find(key)
                    if k >= 0:
                        b = buf.find("[", k + len(key))
                        if b >= 0:
                            start = b
                            break
        buf = buf[start + 1:]
        while True:
            buf = buf.lstrip()
            if buf.startswith(","):
                buf = buf[1:]
                continue
            if buf.startswith("]"):
                break
            if not buf:
                chunk = f.read(1 << 22)
                if not chunk:
                    break
                buf += chunk
                continue
            try:
                rec, end = dec.raw_decode(buf)
            except ValueError:
                chunk = f.read(1 << 22)
                if not chunk:
                    break
                buf += chunk
                continue
            if isinstance(rec, dict):
                records.append(rec)
            buf = buf[end:]
    return records
