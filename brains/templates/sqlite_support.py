#!/usr/bin/env python3
"""
SQLite support  (AutoCorp CLI - brains.templates)  [SQLite Generation Phase 1]
=============================================================================

A small, DETERMINISTIC code-generation layer for SQLite-backed desktop apps.

It is the single source of truth for the persistence code an SQLite app needs:

    * database.py   - connection helper + init_db() + CREATE TABLE (IF NOT EXISTS)
    * crud.py       - add / get / update / delete helpers for one entity

The `sqlite_desktop` template embeds the output of these generators verbatim in
its file purposes, so the generated project always gets correct, runnable
persistence code regardless of the model. Because the generators are pure
(string in -> string out), they are unit-tested directly: the tests exec the
generated code against a real SQLite database and exercise full CRUD.

Phase 1 scope: a single entity (table) inferred from the request, with a small,
fixed column set. Reusable and easy to extend later (more tables / columns).

Public API:
    detect_entity(request) -> str                 # e.g. "customers"
    columns_for(table) -> list[tuple[str, str]]   # [(name, sqltype), ...]
    database_py(table, columns) -> str            # full database.py source
    crud_py(table, columns) -> str                # full crud.py source
"""

# Known entities and their (column, SQL type) schemas. The first keyword group
# that appears in the request selects the entity; otherwise a generic "records"
# table is used. id INTEGER PRIMARY KEY AUTOINCREMENT is always added implicitly.
ENTITIES = {
    "customers": [("name", "TEXT"), ("email", "TEXT"), ("phone", "TEXT")],
    "products": [("name", "TEXT"), ("quantity", "INTEGER"), ("price", "REAL")],
    "tasks": [("title", "TEXT"), ("status", "TEXT")],
    "records": [("name", "TEXT"), ("value", "TEXT")],
}

# request keyword -> entity table name (checked in order).
_ENTITY_KEYWORDS = [
    (("crm", "customer", "client", "contact"), "customers"),
    (("inventory", "product", "stock", "warehouse"), "products"),
    (("task", "todo", "to-do", "ticket"), "tasks"),
]


def detect_entity(request: str) -> str:
    """Infer the table name from the request. Defaults to 'records'."""
    text = (request or "").lower()
    for keywords, table in _ENTITY_KEYWORDS:
        if any(k in text for k in keywords):
            return table
    return "records"


def columns_for(table: str) -> list:
    """Return the [(column, sqltype), ...] schema for a known table."""
    return ENTITIES.get(table, ENTITIES["records"])


def _singular(table: str) -> str:
    """'customers' -> 'customer' (used for add_/update_/delete_ function names)."""
    return table[:-1] if table.endswith("s") and len(table) > 1 else table


def database_py(table: str, columns: list) -> str:
    """Return the complete source of database.py for `table` with `columns`.

    Provides get_connection() (Row factory) and init_db() that creates the table
    if it does not already exist. The DB file lives next to this module.
    """
    cols_sql = ",\n                ".join(f"{name} {sqltype}" for name, sqltype in columns)
    return f'''import os
import sqlite3

# The SQLite database file lives alongside this module, inside the project.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")


def get_connection():
    """Open a SQLite connection that returns rows accessible by column name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the {table} table if it does not exist. Safe to call repeatedly."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {cols_sql}
            )
            """
        )
'''


def crud_py(table: str, columns: list) -> str:
    """Return the complete source of crud.py for `table` with `columns`.

    Generates add_<entity>, get_<table>, update_<entity>, delete_<entity>, all
    using parameterised queries via database.get_connection().
    """
    one = _singular(table)
    names = [name for name, _ in columns]
    col_list = ", ".join(names)
    placeholders = ", ".join("?" for _ in names)
    set_clause = ", ".join(f"{name} = ?" for name in names)
    params = ", ".join(names)
    # Always trailing-comma the INSERT args so a single-column tuple is valid.
    insert_args = f"({params},)" if names else "()"
    update_args = f"({params}, record_id)" if names else "(record_id,)"
    return f'''from database import get_connection, init_db


def add_{one}({params}):
    """Insert a new {one} row and return its new id."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
            {insert_args},
        )
        return cur.lastrowid


def get_{table}():
    """Return all {table} as a list of dicts, ordered by id."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM {table} ORDER BY id").fetchall()
        return [dict(row) for row in rows]


def update_{one}(record_id, {params}):
    """Update an existing {one} by id."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE {table} SET {set_clause} WHERE id = ?",
            {update_args},
        )


def delete_{one}(record_id):
    """Delete a {one} by id."""
    with get_connection() as conn:
        conn.execute("DELETE FROM {table} WHERE id = ?", (record_id,))
'''
