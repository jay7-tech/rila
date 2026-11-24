"""Text embeddings for semantic search (small ONNX model, no PyTorch).

The model is EMBED_MODEL (default BAAI/bge-small-en-v1.5, English). It downloads once on first use into
FASTEMBED_CACHE_PATH. The database column is vector(384), so any model used here must output 384 numbers:
  - BAAI/bge-small-en-v1.5                                      English only (default)
  - sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  50+ languages incl. Hindi, Marathi, Gujarati,
                                                                 Urdu; NOT Kannada, Tamil or Telugu
Vectors from different models are not comparable, so after changing EMBED_MODEL run `python -m app.cli reembed`.
Other modules call these functions through the module so tests can replace them.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from ..config import get_settings
from ..models import Item

EMBED_DIM = 384

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        name = get_settings().embed_model
        model = TextEmbedding(model_name=name)
        dim = len(next(iter(model.embed(["dimension check"]))))
        if dim != EMBED_DIM:
            raise ValueError(f"EMBED_MODEL {name} outputs {dim} numbers but the database column holds {EMBED_DIM}. "
                             f"Pick a {EMBED_DIM}-dimension model, or add a migration to change the column first.")
        _model = model
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
