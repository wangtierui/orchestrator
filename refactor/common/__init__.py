# -*- coding: utf-8 -*-
"""common —— refactor 内跨包共享的基础工具（单一事实源）。

目前只放 collectors 与 clean 都需要的文本/日期原子：
  - normalize_date  日期归一（此前 collectors/lib/text_utils 与 clean/core/cleaner
                    各有一份且契约不一致，现统一）
  - content_sha256 字符串 sha256（clean 的 content_hash 与 collectors 的
                    content_sha256 同义，现统一）
  - doc_number     文号抽取/规范化（原 collectors/lib/doc_number 与 clean/core/doc_number
                    两份 near-identical 副本，现统一到本包，envelope 落库即规范化）

注意：本包只放"真正跨包共用"的纯函数，不包含 collectors 专属逻辑
（http/ocr/pipeline/incremental/paths… 仍留在 collectors/lib）。
"""
