import pytest

from app.recs import retrieval as R

DOCS = {
    1: {"venue": "Meghana Foods", "title": "Meghana Foods", "category": "food", "summary": "Andhra biryani, always a queue"},
    2: {"venue": "Biryani Zone", "title": "Biryani Zone", "category": "food", "summary": "Hyderabadi dum biryani and kebabs"},
    3: {"venue": "Third Wave Coffee", "title": "Third Wave Coffee", "category": "food", "summary": "Cold brew and quiet corners"},
    4: {"venue": "Cubbon Park", "title": "Cubbon Park Walk", "category": "attraction", "summary": "Morning walk with bamboo grove"},
    5: {"venue": "Meghana Foods", "title": "Meghana biryani offer", "category": "food", "summary": "1+1 on starters biryani"},
}


def test_tokens_drop_stop_words_and_punctuation():
    assert R.tokens("The Best Biryani, near me!") == ["biryani"]


def test_bm25_ranks_rarer_and_field_weighted_matches_higher():
    bm = R.BM25F(DOCS)
    s = bm.score("biryani")
    assert set(s) == {1, 2, 5} and s[2] > s[1]  # biryani is in the venue and title of doc 2, only the summary of doc 1
    assert bm.score("walk") .keys() == {4}
    assert bm.score("nonexistentword") == {}


def test_typo_tolerance_maps_unknown_words_to_the_nearest_known_word():
    bm = R.BM25F(DOCS)
    assert bm.expand("biriyani")[0] == "biryani" and bm.expand("biriyani")[1] < 1.0
    assert 2 in bm.score("biriyani")
    assert bm.expand("cat") == ("cat", 1.0)  # too short to guess
    assert bm.score("zzzzzzzz") == {}


def test_rrf_rewards_agreement_between_rankings():
    fused = R.rrf([[1, 2, 3], [3, 1, 2]])
    assert R.rank(fused)[0] == 1 and fused[1] > fused[2]
    assert R.rrf([[7]], k=60)[7] == pytest.approx(1 / 61)
    assert R.rrf([[1, 2], [2, 1]], weights=[1.0, 0.0])[1] > R.rrf([[1, 2], [2, 1]], weights=[1.0, 0.0])[2]


def test_mmr_keeps_relevance_order_when_lambda_is_one_and_diversifies_otherwise():
    rel = {1: 1.0, 2: 0.95, 3: 0.5}
    sim = lambda a, b: 1.0 if {a, b} == {1, 2} else 0.0  # 1 and 2 are near-duplicates
    assert R.mmr(rel, sim, 3, lam=1.0) == [1, 2, 3]
    assert R.mmr(rel, sim, 3, lam=0.5) == [1, 3, 2]  # the duplicate is pushed down
    assert R.mmr({}, sim, 3) == [] and R.mmr(rel, sim, 1) == [1]


def test_search_returns_matched_terms_and_diversifies_same_venue():
    venues = {1: "meghana foods", 5: "meghana foods", 2: "biryani zone"}
    out = R.search(DOCS, "biryani", k=3, venues=venues)
    ids = [h["id"] for h in out]
    assert set(ids) == {1, 2, 5}
    assert ids.index(2) < max(ids.index(1), ids.index(5))  # the second Meghana mention is not allowed to crowd out a different venue
    assert out[0]["terms"] == ["biryani"] and not out[0]["semantic"]


def test_search_fuses_meaning_with_words():
    # "breakfast" appears nowhere in the text, but doc 3 is semantically close
    out = R.search(DOCS, "breakfast", dense_distance={3: 0.2, 1: 0.9}, k=5)
    assert [h["id"] for h in out] == [3] and out[0]["semantic"]
    both = R.search(DOCS, "biryani", dense_distance={2: 0.1, 4: 0.3}, k=5)
    assert both[0]["id"] == 2 and {h["id"] for h in both} >= {1, 2, 4, 5}


def test_search_with_nothing_relevant_returns_nothing():
    assert R.search(DOCS, "zzz qqq", k=5) == []
    assert R.search({}, "biryani", k=5) == []
