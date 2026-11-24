import sys
import types

import pytest
from sqlalchemy import select

from app import cli
from app.config import get_settings
from app.models import Item
from app.services import embeddings as emb
from conftest import make_item


class FakeTextEmbedding:
    dim = 384

    def __init__(self, model_name):
        self.model_name = model_name

    def embed(self, texts):
        for _ in texts:
            yield [0.1] * FakeTextEmbedding.dim

    def query_embed(self, texts):
        return self.embed(texts)


@pytest.fixture()
def fake_fastembed(monkeypatch):
    mod = types.ModuleType("fastembed")
    mod.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", mod)
    monkeypatch.setattr(emb, "_model", None)
    FakeTextEmbedding.dim = 384
    yield
    monkeypatch.setattr(emb, "_model", None)


def test_embed_model_setting_has_an_english_default_and_can_be_changed(monkeypatch):
    assert get_settings().embed_model == "BAAI/bge-small-en-v1.5"
    monkeypatch.setenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    assert get_settings().embed_model.endswith("multilingual-MiniLM-L12-v2")


def test_model_is_loaded_from_settings_and_dimension_checked(fake_fastembed, monkeypatch):
    monkeypatch.setenv("EMBED_MODEL", "some/model")
    assert emb._get_model().model_name == "some/model"
    assert len(emb.embed_query("biryani")) == emb.EMBED_DIM


def test_wrong_dimension_is_refused_with_a_clear_message(fake_fastembed, monkeypatch):
    FakeTextEmbedding.dim = 1024
    monkeypatch.setenv("EMBED_MODEL", "intfloat/multilingual-e5-large")
    with pytest.raises(ValueError, match="1024 numbers .* 384"):
        emb.embed_texts(["x"])


def test_reembed_rewrites_every_vector(fake_fastembed, session, user, monkeypatch, capsys):
    class Ctx:
        def __enter__(self_inner):
            return session

        def __exit__(self_inner, *a):
            return False

    monkeypatch.setattr(cli, "_db", lambda: Ctx())
    a = make_item(session, user, "Dosa", kind="place", venue="Dosa Camp")
    b = make_item(session, user, "Walk", kind="place", venue="Lake")
    a.embedding = [0.9] * 384
    session.commit()
    assert cli.main(["reembed"]) == 0
    assert "re-embedded 2 items" in capsys.readouterr().out
    vecs = [i.embedding for i in session.scalars(select(Item))]
    assert all(v is not None and abs(float(v[0]) - 0.1) < 1e-6 for v in vecs)
