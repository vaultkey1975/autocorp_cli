"""Phase 6 — inline child-row editing in the deterministic UI framework.

Separate from the stable Phase 5 suite (tests/test_sqlite_ui.py). These tests are
written FIRST (TDD) and are expected to fail until _ui_config gains a child
`update_fn` and MasterDetailWindow gains `_on_child_select` / `_edit_child` plus
per-child `selected_id` tracking. The window is constructed offscreen against a real
SQLite DB. No Ollama needed.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must precede PySide6 import

import importlib
import sys

import pytest

from brains.templates import sqlite_support as ss

CRM = "Build a customer CRM desktop app with SQLite"


@pytest.fixture(scope="session", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _load_project(tmp_path, schema):
    """Materialise db/crud/export + ui package, import fresh, return (crud, md)."""
    (tmp_path / "database.py").write_text(ss.schema_database_py(schema))
    (tmp_path / "crud.py").write_text(ss.schema_crud_py(schema))
    (tmp_path / "export.py").write_text(ss.export_py(schema))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "__init__.py").write_text("# ui package\n")
    (ui / "widgets.py").write_text(ss.widgets_py())
    (ui / "master_detail.py").write_text(ss.master_detail_py(schema))

    sys.path.insert(0, str(tmp_path))
    for name in ("database", "crud", "export", "ui", "ui.widgets", "ui.master_detail"):
        sys.modules.pop(name, None)
    try:
        crud = importlib.import_module("crud")
        master_detail = importlib.import_module("ui.master_detail")
    finally:
        sys.path.remove(str(tmp_path))
    return crud, master_detail


def _select_customer(crud, md, tmp_path):
    """Build a window with one customer selected; return (win, customer_id)."""
    win = md.MasterDetailWindow()
    win.table.setCurrentCell(0, 0)
    win.on_select()
    return win


# --------------------------------------------------------------------------- #
# CONFIG / source
# --------------------------------------------------------------------------- #
def test_child_config_has_update_fn():
    cfg = ss._ui_config(ss.detect_schema(CRM))
    assert cfg["children"][0]["update_fn"] == "update_note"
    assert cfg["children"][1]["update_fn"] == "update_interaction"


def test_master_detail_source_has_edit_methods():
    src = ss.master_detail_py(ss.detect_schema(CRM))
    assert "_on_child_select" in src
    assert "_edit_child" in src
    compile(src, "master_detail.py", "exec")


# --------------------------------------------------------------------------- #
# Behaviour (constructed offscreen)
# --------------------------------------------------------------------------- #
def test_inline_child_edit_offscreen(tmp_path):
    crud, md = _load_project(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    cid = crud.add_customer("Ada", "ada@x.com", "1")
    note_id = crud.add_note(cid, "draft")

    win = md.MasterDetailWindow()
    win.table.setCurrentCell(0, 0)
    win.on_select()
    notes_state = win.children_state[0]
    assert notes_state["list"].count() == 1

    # Select the child row -> fields populate, id remembered.
    notes_state["list"].setCurrentRow(0)
    win._on_child_select(notes_state)
    assert notes_state["selected_id"] == note_id
    assert notes_state["fields"]["body"].text() == "draft"

    # Edit it.
    notes_state["fields"]["body"].setText("final")
    win._edit_child(notes_state)
    assert crud.get_note(note_id)["body"] == "final"
    assert notes_state["list"].item(0).text() == "final"


def test_inline_interaction_edit_offscreen(tmp_path):
    crud, md = _load_project(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    cid = crud.add_customer("Ada", "ada@x.com", "1")
    iid = crud.add_interaction(cid, "call", "intro")

    win = md.MasterDetailWindow()
    win.table.setCurrentCell(0, 0)
    win.on_select()
    inter_state = win.children_state[1]
    assert inter_state["cfg"]["title"] == "Interactions"

    inter_state["list"].setCurrentRow(0)
    win._on_child_select(inter_state)
    assert inter_state["selected_id"] == iid

    inter_state["fields"]["kind"].setText("email")
    inter_state["fields"]["summary"].setText("sent deck")
    win._edit_child(inter_state)
    updated = crud.get_interaction(iid)
    assert updated["kind"] == "email"
    assert updated["summary"] == "sent deck"
    assert updated["customer_id"] == cid   # FK stays pinned to the parent


def test_child_selected_id_resets_on_reload(tmp_path):
    crud, md = _load_project(tmp_path, ss.detect_schema(CRM))
    crud.init_db()
    cid = crud.add_customer("Ada", "ada@x.com", "1")
    crud.add_note(cid, "n1")

    win = md.MasterDetailWindow()
    win.table.setCurrentCell(0, 0)
    win.on_select()
    notes_state = win.children_state[0]
    notes_state["list"].setCurrentRow(0)
    win._on_child_select(notes_state)
    assert notes_state["selected_id"] is not None

    # Reloading the detail pane must clear the stale child selection.
    win._load_children()
    assert notes_state["selected_id"] is None
