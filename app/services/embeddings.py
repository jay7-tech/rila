"""Text embeddings for semantic search (small ONNX model, no PyTorch).

The model (BAAI/bge-small-en-v1.5, 384 dimensions) downloads once on first
use into FASTEMBED_CACHE_PATH. Other modules call these functions through
the module so tests can replace them.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from ..models import Item

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _model


def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    return [[float(x) for x in v] for v in _get_model().embed(list(texts))]


def embed_query(text: str) -> List[float]:
    return [float(x) for x in next(iter(_get_model().query_embed([text])))]


def item_text(it: Item) -> str:
    parts = [it.category, it.title, it.venue, it.area, it.city, it.summary, it.price_text, it.duration_text]
    return ". ".join(p for p in parts if p)


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)
