"""Tests for the deterministic SQLite code generators (brains/templates/sqlite_support).

These do NOT need Ollama: they generate database.py + crud.py as source, write them
to a temp dir, import them, and exercise full CRUD against a real SQLite file. That
proves init_db / CREATE TABLE / CRUD all actually work.
"""

import importlib
import sys

import pytest

from brains.templates import sqlite_support as ss


def _load_crud(tmp_path, table, columns):
    """Write generated database.py + crud.py into tmp_path, import fresh, return
    (database_module, crud_module). DB_PATH resolves to tmp_path/app.db."""
    (tmp_path / "database.py").write_text(ss.database_py(table, columns))
    (tmp_path / "crud.py").write_text(ss.crud_py(table, columns))
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
# Entity detection / schema
# --------------------------------------------------------------------------- #
def test_detect_entity():
    assert ss.detect_entity("Build a customer CRM desktop app with SQLite") == "customers"
    assert ss.detect_entity("a desktop inventory manager") == "products"
    assert ss.detect_entity("a GUI todo app with a database") == "tasks"
    assert ss.detect_entity("some generic data app") == "records"
    assert ss.detect_entity("") == "records"


def test_columns_for_known_and_default():
    assert ss.columns_for("customers") == [("name", "TEXT"), ("email", "TEXT"), ("phone", "TEXT")]
    # Unknown table falls back to the generic records schema.
    assert ss.columns_for("unknown") == ss.ENTITIES["records"]


# --------------------------------------------------------------------------- #
# Generated source: shape + compiles
# --------------------------------------------------------------------------- #
def test_database_py_contains_init_and_create_table():
    src = ss.database_py("customers", ss.columns_for("customers"))
    assert "def get_connection" in src
    assert "def init_db" in src
    assert "sqlite3.connect" in src
    assert "CREATE TABLE IF NOT EXISTS customers" in src
    assert "id INTEGER PRIMARY KEY AUTOINCREMENT" in src
    for col in ("name TEXT", "email TEXT", "phone TEXT"):
        assert col in src
    compile(src, "database.py", "exec")  # must be valid Python


def test_crud_py_contains_all_helpers():
    src = ss.crud_py("customers", ss.columns_for("customers"))
    assert "from database import get_connection, init_db" in src
    for fn in ("def add_customer", "def get_customers",
               "def update_customer", "def delete_customer"):
        assert fn in src
    assert "INSERT INTO customers" in src
    assert "SELECT * FROM customers" in src
    assert "UPDATE customers SET" in src
    assert "DELETE FROM customers" in src
    compile(src, "crud.py", "exec")  # must be valid Python


# --------------------------------------------------------------------------- #
# Generated code actually works (full CRUD round-trip)
# --------------------------------------------------------------------------- #
def test_full_crud_round_trip(tmp_path):
    _db, crud = _load_crud(tmp_path, "customers", ss.columns_for("customers"))
    crud.init_db()

    id1 = crud.add_customer("Ada Lovelace", "ada@example.com", "111")
    id2 = crud.add_customer("Alan Turing", "alan@example.com", "222")
    assert id1 == 1 and id2 == 2

    rows = crud.get_customers()
    assert len(rows) == 2
    assert rows[0]["name"] == "Ada Lovelace"
    assert rows[0]["email"] == "ada@example.com"
    assert set(rows[0].keys()) == {"id", "name", "email", "phone"}

    crud.update_customer(id1, "Ada L.", "ada@new.com", "999")
    updated = crud.get_customers()[0]
    assert updated["name"] == "Ada L."
    assert updated["email"] == "ada@new.com"

    crud.delete_customer(id2)
    remaining = crud.get_customers()
    assert len(remaining) == 1
    assert remaining[0]["id"] == id1


def test_init_db_is_idempotent(tmp_path):
    _db, crud = _load_crud(tmp_path, "products", ss.columns_for("products"))
    crud.init_db()
    crud.add_product("Widget", 5, 9.99)
    crud.init_db()  # second call must not wipe or error
    rows = crud.get_products()
    assert len(rows) == 1
    assert rows[0]["name"] == "Widget"
    assert rows[0]["quantity"] == 5
    assert rows[0]["price"] == pytest.approx(9.99)
