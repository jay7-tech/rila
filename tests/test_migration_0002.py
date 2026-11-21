"""Migration 0002 must work on a database made by 0001 (old schema) and on a brand-new one."""
import importlib.util
import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("m0002", os.path.join(HERE, "..", "migrations", "versions", "0002_documents_evidence.py"))
m0002 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m0002)


def run_upgrade(engine):
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            m0002.upgrade()


def old_schema(engine):
    md = sa.MetaData()
    sa.Table("users", md, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("items", md, sa.Column("id", sa.Integer, primary_key=True), sa.Column("title", sa.Text))
    sa.Table("jobs", md, sa.Column("id", sa.Integer, primary_key=True), sa.Column("url", sa.Text))
    md.create_all(engine)
    with engine.begin() as c:
        c.execute(sa.text("insert into jobs (id, url) values (1, 'https://youtu.be/a')"))
        c.execute(sa.text("insert into items (id, title) values (1, 'Existing')"))


def test_upgrades_an_old_database_and_keeps_its_rows():
    engine = sa.create_engine("sqlite://")
    old_schema(engine)
    run_upgrade(engine)
    insp = sa.inspect(engine)
    assert "documents" in insp.get_table_names()
    assert {"evidence", "evidence_source", "evidence_at", "grounded", "document_id", "venue_en"} <= {c["name"] for c in insp.get_columns("items")}
    assert {"kind", "payload"} <= {c["name"] for c in insp.get_columns("jobs")}
    with engine.connect() as c:
        assert c.execute(sa.text("select kind from jobs where id = 1")).scalar() == "url"  # old jobs default to links
        assert c.execute(sa.text("select title from items")).scalar() == "Existing"


def test_running_twice_or_on_a_fresh_database_changes_nothing():
    from app import db as dbmod

    dbmod.init_db("sqlite://")  # fresh schema from the current models
    engine = dbmod._engine
    run_upgrade(engine)
    run_upgrade(engine)
    assert "documents" in sa.inspect(engine).get_table_names()
    dbmod.Base.metadata.drop_all(engine)
