#!/usr/bin/env python3
"""
SQLite support  (AutoCorp CLI - brains.templates)  [SQLite Generation Phase 1+2]
===============================================================================

A small, DETERMINISTIC code-generation layer for SQLite-backed desktop apps. It
is the single source of truth for the persistence code an SQLite app needs, so
the generated project always gets correct, runnable code regardless of the model.
The generators are pure (data in -> source string out) and unit-tested directly:
the tests exec the generated code against a real SQLite database and exercise CRUD.

Phase 1 (single table) - unchanged public API:
    detect_entity(request) -> str                 # e.g. "customers"
    columns_for(table) -> list[tuple[str, str]]   # [(name, sqltype), ...]
    database_py(table, columns) -> str            # full database.py source
    crud_py(table, columns) -> str                # full crud.py source

Phase 2 (multiple tables + foreign keys + search):
    Table                                         # a table description
    detect_schema(request) -> list[Table]         # one or more related tables
    schema_database_py(schema) -> str             # database.py for the whole schema
    schema_crud_py(schema) -> str                 # crud.py for the whole schema

A Phase 2 schema models real relationships, e.g. a CRM:
    customers, notes(customer_id -> customers.id), interactions(customer_id -> ...).
Child foreign keys use ON DELETE CASCADE so deleting a parent (a customer) cleanly
removes its children. Per table the CRUD layer provides add/get-all/get-one/update/
delete, a search_* helper over the table's TEXT columns, and get_<table>_for_<parent>()
for FK tables.
"""

from dataclasses import dataclass, field


# =========================================================================== #
# Phase 1 - single-table generators (kept byte-for-byte; still used/tested)
# =========================================================================== #

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
    """Infer the primary table name from the request. Defaults to 'records'."""
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


# =========================================================================== #
# Phase 2 - multi-table schemas (foreign keys + search)
# =========================================================================== #

@dataclass
class Table:
    """One table in a schema.

    columns       : [(name, sqltype), ...]  (excludes the implicit id PK)
    foreign_keys  : [(column, ref_table), ...]  references ref_table(id)
    """
    name: str
    columns: list
    foreign_keys: list = field(default_factory=list)

    @property
    def text_columns(self) -> list:
        return [c for c, t in self.columns if t.upper() == "TEXT"]


# A CRM is the canonical multi-table example: a parent (customers) and two child
# tables that reference it by foreign key.
def _crm_schema() -> list:
    return [
        Table("customers", [("name", "TEXT"), ("email", "TEXT"), ("phone", "TEXT")]),
        Table(
            "notes",
            [("customer_id", "INTEGER"), ("body", "TEXT")],
            [("customer_id", "customers")],
        ),
        Table(
            "interactions",
            [("customer_id", "INTEGER"), ("kind", "TEXT"), ("summary", "TEXT")],
            [("customer_id", "customers")],
        ),
    ]


def detect_schema(request: str) -> list:
    """Return the schema (list of related Tables) for a request.

    A CRM/customers request yields the multi-table customers/notes/interactions
    schema; every other entity yields a single-table schema reusing the Phase 1
    column sets. The first table is always the primary entity the UI manages.
    """
    table = detect_entity(request)
    if table == "customers":
        return _crm_schema()
    return [Table(table, columns_for(table))]


def _create_table_block(table: "Table") -> str:
    """Build the indented `conn.execute(\"\"\"CREATE TABLE ...\"\"\")` statement.

    Foreign keys use ON DELETE CASCADE so deleting a parent row cleanly removes
    the children that reference it.
    """
    lines = ["                id INTEGER PRIMARY KEY AUTOINCREMENT"]
    for name, sqltype in table.columns:
        lines.append(f"                {name} {sqltype}")
    for col, ref in table.foreign_keys:
        lines.append(
            f"                FOREIGN KEY ({col}) REFERENCES {ref}(id) ON DELETE CASCADE"
        )
    cols = ",\n".join(lines)
    return (
        "        conn.execute(\n"
        '            """\n'
        f"            CREATE TABLE IF NOT EXISTS {table.name} (\n"
        f"{cols}\n"
        "            )\n"
        '            """\n'
        "        )"
    )


def schema_database_py(schema: list) -> str:
    """Return database.py for a whole schema: connection (foreign keys enforced)
    and init_db() creating every table (parent tables first)."""
    statements = "\n".join(_create_table_block(t) for t in schema)
    return f'''import os
import sqlite3

# The SQLite database file lives alongside this module, inside the project.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")


def get_connection():
    """Open a SQLite connection (rows by column name) with foreign keys enforced."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create all tables if they do not exist. Safe to call repeatedly."""
    with get_connection() as conn:
{statements}
'''


def _crud_block(table: "Table") -> str:
    """Return the CRUD (+ search + FK query) functions for one table."""
    name = table.name
    one = _singular(name)
    cols = [c for c, _ in table.columns]
    params = ", ".join(cols)
    col_list = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    set_clause = ", ".join(f"{c} = ?" for c in cols)
    insert_args = f"({params},)" if cols else "()"
    update_args = f"({params}, record_id)" if cols else "(record_id,)"

    fns = []
    fns.append(f'''def add_{one}({params}):
    """Insert a new {one} and return its new id."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO {name} ({col_list}) VALUES ({placeholders})",
            {insert_args},
        )
        return cur.lastrowid''')

    fns.append(f'''def get_{name}():
    """Return all {name} as a list of dicts, ordered by id."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM {name} ORDER BY id").fetchall()
        return [dict(row) for row in rows]''')

    fns.append(f'''def get_{one}(record_id):
    """Return a single {one} as a dict, or None if not found."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM {name} WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row else None''')

    fns.append(f'''def update_{one}(record_id, {params}):
    """Update an existing {one} by id."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE {name} SET {set_clause} WHERE id = ?",
            {update_args},
        )''')

    fns.append(f'''def delete_{one}(record_id):
    """Delete a {one} by id (children cascade away via ON DELETE CASCADE)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM {name} WHERE id = ?", (record_id,))''')

    text_cols = table.text_columns
    if text_cols:
        like_clause = " OR ".join(f"{c} LIKE ?" for c in text_cols)
        like_args = "(" + ", ".join("like" for _ in text_cols) + ",)"
        fns.append(f'''def search_{name}(query):
    """Return {name} where any text field matches query (case-insensitive)."""
    like = "%" + (query or "") + "%"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM {name} WHERE {like_clause} ORDER BY id",
            {like_args},
        ).fetchall()
        return [dict(row) for row in rows]''')

    for col, ref in table.foreign_keys:
        ref_one = _singular(ref)
        fns.append(f'''def get_{name}_for_{ref_one}({col}):
    """Return all {name} linked to a {ref_one} by {col}."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM {name} WHERE {col} = ? ORDER BY id", ({col},)
        ).fetchall()
        return [dict(row) for row in rows]''')

    return "\n\n\n".join(fns)


def schema_crud_py(schema: list) -> str:
    """Return crud.py covering every table in the schema."""
    parts = ["from database import get_connection, init_db"]
    parts.extend(_crud_block(t) for t in schema)
    return "\n\n\n".join(parts) + "\n"
