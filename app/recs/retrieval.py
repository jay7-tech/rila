"""Search over the user's saved items: lexical (BM25F) + semantic (dense vectors), fused, then de-duplicated for variety.

Why three stages:
1. BM25F scores exact and near-exact words, weighting fields (a hit in the venue name counts more than one in the summary),
   with typo tolerance (a query word missing from the vocabulary is expanded to its closest known word, so
   "biriyani" finds "biryani"). It is the strong baseline for names and numbers, which embeddings blur.
2. Dense retrieval (cosine distance of embeddings, computed elsewhere) catches meaning ("budget breakfast" ~ "Rs 60 idli vada").
3. Reciprocal Rank Fusion merges the two rankings without needing their scores to be comparable:
       RRF(d) = sum over rankings r of 1 / (k + rank_r(d)),   k = 60
   Then Maximal Marginal Relevance picks results one at a time, trading relevance against similarity to what is already
   chosen:   argmax  lambda * relevance(d) - (1 - lambda) * max_{s in chosen} sim(d, s)
   so five reels about the same biryani shop do not fill the first page.
Pure functions: tested without a database or a model.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from rapidfuzz import fuzz, process

from ..core.text import normalize

K1, B = 1.5, 0.75
RRF_K = 60
FIELD_WEIGHTS = {"venue": 3.0, "title": 3.0, "category": 1.5, "area": 1.5, "city": 1.0, "price": 1.0, "summary": 1.0}
TYPO_MIN_LEN = 4  # shorter words are not expanded: too many false friends
TYPO_RATIO = 82
STOP = {"the", "a", "an", "in", "at", "of", "for", "and", "to", "near", "me", "my", "best", "good"}


def tokens(text: Optional[str]) -> List[str]:
    return [t for t in normalize(text or "").split() if t not in STOP]


class BM25F:
    """BM25 with weighted fields. Each document is a dict field -> text."""

    def __init__(self, docs: Dict[int, Dict[str, str]]):
        self.tf: Dict[int, Counter] = {}
        self.len: Dict[int, float] = {}
        df: Counter = Counter()
        for doc_id, fields in docs.items():
            tf: Counter = Counter()
            for name, text in fields.items():
                w = FIELD_WEIGHTS.get(name, 1.0)
                for t in tokens(text):
                    tf[t] += w
            self.tf[doc_id] = tf
            self.len[doc_id] = sum(tf.values())
            df.update(tf.keys())
        self.n = max(len(docs), 1)
        self.avg = (sum(self.len.values()) / self.n) if docs else 1.0
        self.df = df
        self.vocab = list(df.keys())

    def idf(self, term: str) -> float:
        d = self.df.get(term, 0)
        return math.log(1.0 + (self.n - d + 0.5) / (d + 0.5))

    def expand(self, term: str) -> Tuple[str, float]:
        """A query word -> (word to score, weight). Unknown words are mapped to the nearest known word at a discount."""
        if term in self.df or len(term) < TYPO_MIN_LEN or not self.vocab:
            return term, 1.0
        hit = process.extractOne(term, self.vocab, scorer=fuzz.ratio, score_cutoff=TYPO_RATIO)
        return (hit[0], hit[1] / 100.0 * 0.85) if hit else (term, 1.0)

    def score(self, query: str) -> Dict[int, float]:
        terms = [self.expand(t) for t in tokens(query)]
        out: Dict[int, float] = {}
        for doc_id, tf in self.tf.items():
            s = 0.0
            for term, weight in terms:
                f = tf.get(term, 0.0)
                if f:
                    norm = f * (K1 + 1.0) / (f + K1 * (1.0 - B + B * self.len[doc_id] / self.avg))
                    s += weight * self.idf(term) * norm
            if s > 0:
                out[doc_id] = s
        return out

    def matched_terms(self, query: str, doc_id: int) -> List[str]:
        tf = self.tf.get(doc_id, {})
        return [t for t, _ in (self.expand(q) for q in tokens(query)) if t in tf]


def rank(scores: Dict[int, float], descending: bool = True) -> List[int]:
    return [k for k, _ in sorted(scores.items(), key=lambda kv: (-kv[1] if descending else kv[1], kv[0]))]


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K, weights: Optional[Sequence[float]] = None) -> Dict[int, float]:
    """Reciprocal Rank Fusion. Rankings are lists of ids, best first."""
    out: Dict[int, float] = {}
    for idx, ranking in enumerate(rankings):
        w = weights[idx] if weights else 1.0
        for pos, doc_id in enumerate(ranking, start=1):
            out[doc_id] = out.get(doc_id, 0.0) + w / (k + pos)
    return out


def mmr(relevance: Dict[int, float], sim: Callable[[int, int], float], k: int, lam: float = 0.7) -> List[int]:
    """Greedy Maximal Marginal Relevance. Relevance is rescaled to 0-1 so lambda means the same thing for every query."""
    if not relevance:
        return []
    hi, lo = max(relevance.values()), min(relevance.values())
    rel = {d: (v - lo) / (hi - lo) if hi > lo else 1.0 for d, v in relevance.items()}
    chosen: List[int] = []
    pool = sorted(rel, key=lambda d: (-rel[d], d))
    while pool and len(chosen) < k:
        best = max(pool, key=lambda d: (lam * rel[d] - (1 - lam) * max((sim(d, s) for s in chosen), default=0.0), -d))
        chosen.append(best)
        pool.remove(best)
    return chosen


def jaccard(a: Set[str], b: Set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def item_similarity(features: Dict[int, Set[str]], same_venue: Optional[Dict[int, str]] = None) -> Callable[[int, int], float]:
    """Similarity for diversification: word overlap, and 1.0 when two results are the same venue."""
    def sim(a: int, b: int) -> float:
        if same_venue and same_venue.get(a) and same_venue.get(a) == same_venue.get(b):
            return 1.0
        return jaccard(features.get(a, set()), features.get(b, set()))
    return sim


def search(
    docs: Dict[int, Dict[str, str]], query: str, dense_distance: Optional[Dict[int, float]] = None,
    k: int = 10, dense_max: float = 0.55, lam: float = 0.7, venues: Optional[Dict[int, str]] = None,
) -> List[dict]:
    """Rank documents for a query. Returns [{id, score, terms, semantic}] best first.

    `dense_distance` maps id -> cosine distance (smaller is closer); documents farther than `dense_max` are not
    semantic matches. A document must match by words or by meaning to be returned.
    """
    bm = BM25F(docs)
    lexical = bm.score(query)
    semantic = {i: d for i, d in (dense_distance or {}).items() if d <= dense_max and i in docs}
    lists = [rank(lexical)]
    weights = [1.0]
    if semantic:
        lists.append(rank(semantic, descending=False))
        weights.append(1.0)
    fused = rrf(lists, weights=weights)
    if not fused:
        return []
    feats = {i: set(tokens(" ".join(docs[i].values()))) for i in fused}
    order = mmr(fused, item_similarity(feats, venues), k, lam)
    return [{"id": i, "score": round(fused[i], 5), "terms": bm.matched_terms(query, i), "semantic": i in semantic} for i in order]
