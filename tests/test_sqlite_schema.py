"""Tests for the Phase 2 multi-table SQLite layer (brains/templates/sqlite_support).

No Ollama needed: the generated multi-table database.py + crud.py are written to a
temp dir, imported, and exercised against a real SQLite database - including
foreign-key enforcement, ON DELETE CASCADE, per-table CRUD, search, and FK
relationship queries.
"""

import importlib
import sqlite3
import sys

import pytest

from brains.templates import sqlite_support as ss
from brains.templates import sqlite_desktop as sql

CRM = "Build a customer CRM desktop app with SQLite"


def _load(tmp_path, schema):
    """Write generated multi-table database.py + crud.py, import fresh, return
    (database_module, crud_module)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud"):
        sys.modules.pop(name, None)
    try:
        database = importlib.import_module("database")
        crud = importlib.import_module("crud")
    finally:
        sys.path.remove(str(tmp_path))
    return database, crud


# --------------------------------------------------------------------------- #
# Schema detection
# --------------------------------------------------------------------------- #
def test_detect_schema_crm_has_three_related_tables():
    schema = ss.detect_schema(CRM)
    assert [t.name for t in schema] == ["customers", "notes", "interactions"]
    assert schema[0].foreign_keys == []
    assert schema[1].foreign_keys == [("customer_id", "customers")]
    assert schema[2].foreign_keys == [("customer_id", "customers")]


def test_detect_schema_single_table_for_non_crm():
    assert [t.name for t in ss.detect_schema("a desktop inventory manager")] == ["products"]


# --------------------------------------------------------------------------- #
# Generated source shape + compiles
# --------------------------------------------------------------------------- #
def test_database_py_creates_all_tables_with_fk_and_pragma():
    src = ss.schema_database_py(ss.detect_schema(CRM))
    assert "PRAGMA foreign_keys = ON" in src
    for table in ("customers", "notes", "interactions"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in src
    assert "FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE" in src
    compile(src, "database.py", "exec")


def test_crud_py_has_crud_search_and_fk_helpers():
    src = ss.schema_crud_py(ss.detect_schema(CRM))
    for fn in (
        "def add_customer(name, email, phone)",
        "def get_customers()",
        "def get_customer(record_id)",
        "def update_customer(record_id, name, email, phone)",
        "def delete_customer(record_id)",
        "def search_customers(query)",
        "def add_note(customer_id, body)",
        "def get_notes_for_customer(customer_id)",
        "def add_interaction(customer_id, kind, summary)",
        "def get_interactions_for_customer(customer_id)",
    ):
        assert fn in src, fn
    assert "VALUES (?, ?, ?)" in src  # customers INSERT placeholder count
    compile(src, "crud.py", "exec")


# --------------------------------------------------------------------------- #
# Generated code actually works
# --------------------------------------------------------------------------- #
def test_multi_table_crud_and_search_round_trip(tmp_path):
    _db, crud = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()

    cid = crud.add_customer("Ada Lovelace", "ada@example.com", "111")
    crud.add_customer("Alan Turing", "alan@example.com", "222")
    assert len(crud.get_customers()) == 2
    assert crud.get_customer(cid)["name"] == "Ada Lovelace"

    crud.update_customer(cid, "Ada L.", "ada@new.com", "999")
    assert crud.get_customer(cid)["email"] == "ada@new.com"

    hits = crud.search_customers("Alan")
    assert len(hits) == 1 and hits[0]["name"] == "Alan Turing"
    assert len(crud.search_customers("turing")) == 1   # search is case-insensitive
    assert len(crud.search_customers("zzz")) == 0       # no matches

    crud.delete_customer(cid)
    assert len(crud.get_customers()) == 1


def test_foreign_key_relationship_and_enforcement(tmp_path):
    _db, crud = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    cid = crud.add_customer("Grace", "grace@x.com", "7")

    nid = crud.add_note(cid, "first note")
    assert nid == 1
    notes = crud.get_notes_for_customer(cid)
    assert len(notes) == 1 and notes[0]["body"] == "first note"

    crud.add_interaction(cid, "call", "intro call")
    assert len(crud.get_interactions_for_customer(cid)) == 1

    # Inserting a child row for a non-existent customer must violate the FK.
    with pytest.raises(sqlite3.IntegrityError):
        crud.add_note(99999, "orphan note")


def test_delete_customer_cascades_to_children(tmp_path):
    # Deleting a customer that has notes/interactions must NOT raise; the children
    # cascade away (ON DELETE CASCADE). This is the acceptance "delete customer".
    _db, crud = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    cid = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_note(cid, "n1")
    crud.add_interaction(cid, "call", "hi")
    assert len(crud.get_notes_for_customer(cid)) == 1

    crud.delete_customer(cid)  # must succeed despite child rows

    assert crud.get_customers() == []
    assert crud.get_notes_for_customer(cid) == []
    assert crud.get_interactions_for_customer(cid) == []


# --------------------------------------------------------------------------- #
# Template embeds the Phase 2 schema deterministically
# --------------------------------------------------------------------------- #
def _content(plan, path):
    return next(f.get("content", "") for f in plan["files"] if f["path"] == path)


def test_template_embeds_multitable_schema_and_search():
    plan = sql.build_plan(CRM)
    db = _content(plan, "database.py")
    crud = _content(plan, "crud.py")
    assert "CREATE TABLE IF NOT EXISTS notes" in db
    assert "FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE" in db
    assert "PRAGMA foreign_keys = ON" in db
    assert "def search_customers(query)" in crud
    assert "def get_notes_for_customer(customer_id)" in crud
