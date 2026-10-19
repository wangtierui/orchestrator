# -*- coding: utf-8 -*-
from .base import Embedder
from .registry import get_embedder, register_embedder

__all__ = ["Embedder", "get_embedder", "register_embedder"]
