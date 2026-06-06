"""Tests for the Phase 4 CSV export generator (brains/templates/sqlite_support).

No Ollama needed: the generated database.py + crud.py + export.py are written to a
temp dir, imported, populated via crud, exported to CSV, and the CSV is parsed back
and asserted - so the export code is proven to actually work.
"""

import csv
import importlib
import sys

from brains.templates import sqlite_support as ss
from brains.templates import sqlite_desktop as sql

CRM = "Build a customer CRM desktop app with SQLite"


def _load(tmp_path, schema):
    """Write generated database.py + crud.py + export.py, import fresh, return
    (crud_module, export_module)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "export.py").write_text(ss.export_py(schema))
    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "export"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        export = importlib.import_module("export")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, export


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------- #
# Generated source shape + compiles
# --------------------------------------------------------------------------- #
def test_export_py_has_per_table_and_master_functions():
    src = ss.export_py(ss.detect_schema(CRM))
    for fn in (
        "def export_customers_csv(path)",
        "def export_notes_csv(path)",
        "def export_interactions_csv(path)",
        "def export_customers_with_counts_csv(path)",
        "def _write_csv(path, rows)",
    ):
        assert fn in src, fn
    assert "import csv" in src
    assert "import crud" in src
    compile(src, "export.py", "exec")


def test_single_table_export_has_no_counts_export():
    src = ss.export_py(ss.detect_schema("a desktop inventory manager"))
    assert "def export_products_csv(path)" in src
    assert "with_counts_csv" not in src


# --------------------------------------------------------------------------- #
# Generated code actually works
# --------------------------------------------------------------------------- #
def test_export_customers_csv_round_trip(tmp_path):
    crud, export = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    crud.add_customer("Ada Lovelace", "ada@example.com", "111")
    crud.add_customer("Alan Turing", "alan@example.com", "222")

    out = tmp_path / "customers.csv"
    returned = export.export_customers_csv(str(out))
    assert returned == str(out)

    rows = _read_csv(out)
    assert [r["name"] for r in rows] == ["Ada Lovelace", "Alan Turing"]
    assert set(rows[0].keys()) == {"id", "name", "email", "phone"}


def test_export_master_view_includes_counts(tmp_path):
    crud, export = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    a = crud.add_customer("Ada", "a@x.com", "1")
    crud.add_note(a, "n1")
    crud.add_note(a, "n2")
    crud.add_interaction(a, "call", "hi")

    out = tmp_path / "master.csv"
    export.export_customers_with_counts_csv(str(out))
    rows = _read_csv(out)
    assert rows[0]["notes_count"] == "2"
    assert rows[0]["interactions_count"] == "1"
    assert "name" in rows[0]


def test_export_empty_table_writes_empty_file(tmp_path):
    crud, export = _load(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    out = tmp_path / "empty.csv"
    export.export_customers_csv(str(out))
    assert out.exists()
    assert out.read_text() == ""


# --------------------------------------------------------------------------- #
# Template embeds export.py deterministically
# --------------------------------------------------------------------------- #
def test_template_embeds_export_module():
    plan = sql.build_plan(CRM)
    by_path = {f["path"]: f for f in plan["files"]}
    assert "export.py" in by_path
    content = by_path["export.py"]["content"]
    assert "def export_customers_with_counts_csv(path)" in content
    assert "import csv" in content
