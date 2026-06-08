#!/usr/bin/env python3
"""
SQLite support  (AutoCorp CLI - brains.templates)  [SQLite Generation Phase 1-6]
===============================================================================

A small, DETERMINISTIC code-generation layer for SQLite-backed desktop apps. It
is the single source of truth for the persistence + UI-framework code an SQLite
app needs, so the generated project always gets correct, runnable code regardless
of the model. The generators are pure (data in -> source string out) and unit-
tested directly: the tests exec the generated code against a real SQLite database
(and, for the UI, construct the window offscreen) and exercise real behaviour.

Phase 1 (single table): detect_entity / columns_for / database_py / crud_py.
Phase 2 (multi-table + FK + search): Table / detect_schema / schema_database_py / schema_crud_py.
Phase 3 (master-detail data): schema_crud_py() emits get_<primary>_with_counts().
Phase 4 (CSV export): export_py(schema) -> export.py.
Phase 5 (deterministic UI): widgets_py() + master_detail_py(schema) - a generic
    config-driven MasterDetailWindow (constant) + a small generated CONFIG.
Phase 6 (inline child editing): the CONFIG children carry an update_fn and the
    MasterDetailWindow supports selecting a child row (loading it into the child
    fields) and editing it in place via crud.update_<child>().

A Phase 2-6 schema models real relationships, e.g. a CRM:
    customers, notes(customer_id -> customers.id), interactions(customer_id -> ...).
"""

from dataclasses import dataclass, field


# =========================================================================== #
# Phase 1 - single-table generators (kept byte-for-byte; still used/tested)
# =========================================================================== #

ENTITIES = {
    "customers": [("name", "TEXT"), ("email", "TEXT"), ("phone", "TEXT")],
    "products": [("name", "TEXT"), ("quantity", "INTEGER"), ("price", "REAL")],
    "tasks": [("title", "TEXT"), ("status", "TEXT")],
    "records": [("name", "TEXT"), ("value", "TEXT")],
}

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
    """Return the complete source of database.py for `table` with `columns`."""
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
    """Return the complete source of crud.py for `table` with `columns`."""
    one = _singular(table)
    names = [name for name, _ in columns]
    col_list = ", ".join(names)
    placeholders = ", ".join("?" for _ in names)
    set_clause = ", ".join(f"{name} = ?" for name in names)
    params = ", ".join(names)
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
# Phase 2-6 - schemas (FK + search + master-detail + export + UI framework)
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
    """Return the schema (list of related Tables) for a request."""
    table = detect_entity(request)
    if table == "customers":
        return _crm_schema()
    return [Table(table, columns_for(table))]


def _children_of(schema: list, parent_name: str) -> list:
    """Return [(child_table, fk_column), ...] for tables referencing `parent_name`."""
    out = []
    for table in schema:
        for col, ref in table.foreign_keys:
            if ref == parent_name:
                out.append((table.name, col))
    return out


def _create_table_block(table: "Table") -> str:
    """Build the indented conn.execute(CREATE TABLE ...) statement (FK ON DELETE CASCADE)."""
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
    """Return database.py for a whole schema (foreign keys enforced)."""
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


def _counts_function(primary: str, children: list) -> str:
    """Master-view helper: each primary row annotated with a <child>_count column."""
    count_lines = ",\n".join(
        f"                (SELECT COUNT(*) FROM {child} WHERE {child}.{fk} = c.id) "
        f"AS {child}_count"
        for child, fk in children
    )
    return f'''def get_{primary}_with_counts():
    """Return all {primary}, each annotated with counts of related child rows."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*,
{count_lines}
            FROM {primary} c
            ORDER BY c.id
            """
        ).fetchall()
        return [dict(row) for row in rows]'''


def schema_crud_py(schema: list) -> str:
    """Return crud.py covering every table, plus get_<primary>_with_counts() when
    the primary table has child tables."""
    parts = ["from database import get_connection, init_db"]
    parts.extend(_crud_block(t) for t in schema)
    primary = schema[0]
    children = _children_of(schema, primary.name)
    if children:
        parts.append(_counts_function(primary.name, children))
    return "\n\n\n".join(parts) + "\n"


def export_py(schema: list) -> str:
    """Return export.py: CSV export helpers over the crud get_* functions."""
    primary = schema[0]
    children = _children_of(schema, primary.name)
    fns = []
    for table in schema:
        fns.append(f'''def export_{table.name}_csv(path):
    """Export all {table.name} to a CSV file at `path`. Returns the path."""
    return _write_csv(path, crud.get_{table.name}())''')
    if children:
        fns.append(f'''def export_{primary.name}_with_counts_csv(path):
    """Export the master view ({primary.name} + child counts) to CSV at `path`."""
    return _write_csv(path, crud.get_{primary.name}_with_counts())''')
    body = "\n\n\n".join(fns)
    return f'''import csv

import crud


def _write_csv(path, rows):
    """Write a list of dict rows to a CSV at `path` (header from the first row).
    An empty result writes a header-less empty file. Returns the path."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return path


{body}
'''


# =========================================================================== #
# Phase 7 - deterministic reporting layer (reports.py)
# =========================================================================== #

def reports_py(schema: list) -> str:
    """Return reports.py: read-only analytics over the schema.

    For every table a count_<table>() (SELECT COUNT(*)); for every foreign key a
    avg_<child>_per_<parent>() (children / parents, 0 when there are no parents);
    a summary() returning {table: row count} for all tables; export_summary_csv()
    writing summary() to a CSV (metric,value, schema order); and export_metrics_csv()
    writing all metrics (counts then averages) to a CSV. Pure SQL + stdlib csv via
    database.get_connection(); generic across any schema.
    """
    fns = []

    for table in schema:
        name = table.name
        fns.append(f'''def count_{name}():
    """Return the number of rows in {name}."""
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM {name}").fetchone()[0]''')

    for table in schema:
        for _col, ref in table.foreign_keys:
            ref_one = _singular(ref)
            name = table.name
            fns.append(f'''def avg_{name}_per_{ref_one}():
    """Average number of {name} per {ref_one} (0 when there are none)."""
    with get_connection() as conn:
        parents = conn.execute("SELECT COUNT(*) FROM {ref}").fetchone()[0]
        children = conn.execute("SELECT COUNT(*) FROM {name}").fetchone()[0]
        return children / parents if parents else 0''')

    summary_entries = "\n".join(
        f'        "{t.name}": count_{t.name}(),' for t in schema
    )
    summary_fn = (
        "def summary():\n"
        '    """Return a {table: row count} mapping for every table."""\n'
        "    return {\n"
        + summary_entries + "\n"
        "    }"
    )
    fns.append(summary_fn)

    fns.append('''def export_summary_csv(path):
    """Write summary() to a CSV file (metric,value columns) and return the path."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        for metric, value in summary().items():
            writer.writerow([metric, value])
    return path''')

    metric_names = [f"count_{t.name}" for t in schema]
    for table in schema:
        for _col, ref in table.foreign_keys:
            metric_names.append(f"avg_{table.name}_per_{_singular(ref)}")
    metric_entries = "\n".join(
        f'        ("{metric}", {metric}),' for metric in metric_names
    )
    metrics_fn = (
        "def export_metrics_csv(path):\n"
        '    """Write all metrics (counts then averages) to a CSV (metric,value) and return the path."""\n'
        "    metrics = [\n"
        + metric_entries + "\n"
        "    ]\n"
        '    with open(path, "w", newline="", encoding="utf-8") as handle:\n'
        "        writer = csv.writer(handle)\n"
        '        writer.writerow(["metric", "value"])\n'
        "        for metric, function in metrics:\n"
        "            writer.writerow([metric, function()])\n"
        "    return path"
    )
    fns.append(metrics_fn)

    body = "\n\n\n".join(fns)
    return "import csv\n\nfrom database import get_connection\n\n\n" + body + "\n"


# =========================================================================== #
# Phase 5/6 - deterministic UI framework (ui/widgets.py + ui/master_detail.py)
# =========================================================================== #

# Generic, schema-independent UI helpers. Constant: identical for every app.
_WIDGETS_PY = '''from PySide6.QtWidgets import QTableWidgetItem


def populate_table(table, rows, columns):
    """Fill a QTableWidget from a list of dict `rows` using `columns` as keys."""
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels([c.replace("_", " ").title() for c in columns])
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, key in enumerate(columns):
            table.setItem(r, c, QTableWidgetItem(str(row.get(key, ""))))


def fill_list(list_widget, rows, key):
    """Replace a QListWidget's items with str(row[key]) for each row."""
    list_widget.clear()
    for row in rows:
        list_widget.addItem(str(row.get(key, "")))


def selected_row_value(widget, rows, key="id"):
    """Return rows[current_row][key] for a QTableWidget/QListWidget, or None."""
    index = widget.currentRow()
    if index is None or index < 0 or index >= len(rows):
        return None
    return rows[index].get(key)


def read_form(fields):
    """fields: {name: QLineEdit}. Return {name: text}."""
    return {name: widget.text() for name, widget in fields.items()}


def clear_form(fields):
    """Clear every QLineEdit in `fields`."""
    for widget in fields.values():
        widget.clear()


def fill_form(fields, row):
    """Set each QLineEdit in `fields` from `row` (or clear when row is None)."""
    for name, widget in fields.items():
        widget.setText(str(row.get(name, "")) if row else "")
'''


# Generic, config-driven master-detail window. Constant: ALL the wiring lives here
# and is tested once; only the CONFIG literal below it is generated per schema.
# Phase 6 adds inline child editing: selecting a child row loads it into the child
# fields (_on_child_select) and an 'Edit' button updates it (_edit_child).
_MASTER_DETAIL_CLASS = '''class MasterDetailWindow(QMainWindow):
    """A generic master-detail window driven entirely by CONFIG. The master table
    lists the primary entity (optionally with child counts); selecting a row loads
    that row into the edit form and loads each child table into its detail list.
    Child rows can themselves be selected and edited in place. All data access goes
    through the crud / export modules by name."""

    def __init__(self):
        super().__init__()
        crud.init_db()
        self.primary = CONFIG["primary"]
        self.selected_id = None
        self._rows = []

        self.setWindowTitle(CONFIG["title"])
        self.resize(1000, 650)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Toolbar: search / refresh / export.
        bar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search " + CONFIG["title"])
        bar.addWidget(self.search_input)
        self._button(bar, "Search", self.on_search)
        self._button(bar, "Refresh", self.refresh)
        if self.primary.get("export_fn"):
            self._button(bar, "Export CSV", self.on_export)
        root.addLayout(bar)

        # Master table.
        self.table = QTableWidget()
        self.table.itemSelectionChanged.connect(self.on_select)
        root.addWidget(self.table)

        # Primary edit form + actions.
        form = QHBoxLayout()
        self.fields = {}
        for name in self.primary["fields"]:
            edit = QLineEdit()
            edit.setPlaceholderText(name)
            self.fields[name] = edit
            form.addWidget(edit)
        self._button(form, "Add", self.on_add)
        self._button(form, "Edit", self.on_edit)
        self._button(form, "Delete", self.on_delete)
        root.addLayout(form)

        # Detail panels, one per child table (add / edit / delete).
        self.children_state = []
        for child in CONFIG.get("children", []):
            root.addWidget(QLabel(child["title"]))
            list_widget = QListWidget()
            root.addWidget(list_widget)
            cform = QHBoxLayout()
            cfields = {}
            for name in child["fields"]:
                edit = QLineEdit()
                edit.setPlaceholderText(name)
                cfields[name] = edit
                cform.addWidget(edit)
            state = {"cfg": child, "list": list_widget, "fields": cfields,
                     "rows": [], "selected_id": None}
            list_widget.itemSelectionChanged.connect(
                lambda s=state: self._on_child_select(s))
            self._button(cform, "Add " + child["title"],
                         lambda _=False, s=state: self._add_child(s))
            self._button(cform, "Edit " + child["title"],
                         lambda _=False, s=state: self._edit_child(s))
            self._button(cform, "Delete " + child["title"],
                         lambda _=False, s=state: self._delete_child(s))
            root.addLayout(cform)
            self.children_state.append(state)

        self.refresh()

    # ------------------------------------------------------------------ #
    def _button(self, layout, label, handler):
        button = QPushButton(label)
        button.clicked.connect(handler)
        layout.addWidget(button)

    def _crud(self, name):
        return getattr(crud, name)

    # ----- master -----
    def refresh(self):
        self._rows = self._crud(self.primary["list_fn"])()
        widgets.populate_table(self.table, self._rows, self.primary["columns"])
        self._load_children()

    def on_search(self):
        name = self.primary.get("search_fn")
        if not name:
            return
        self._rows = self._crud(name)(self.search_input.text())
        widgets.populate_table(self.table, self._rows, self.primary["columns"])

    def on_select(self):
        self.selected_id = widgets.selected_row_value(self.table, self._rows)
        row = next((r for r in self._rows if r.get("id") == self.selected_id), None)
        widgets.fill_form(self.fields, row)
        self._load_children()

    def on_add(self):
        data = widgets.read_form(self.fields)
        self._crud(self.primary["add_fn"])(*[data[f] for f in self.primary["fields"]])
        widgets.clear_form(self.fields)
        self.refresh()

    def on_edit(self):
        if self.selected_id is None:
            return
        data = widgets.read_form(self.fields)
        self._crud(self.primary["update_fn"])(
            self.selected_id, *[data[f] for f in self.primary["fields"]]
        )
        self.refresh()

    def on_delete(self):
        if self.selected_id is None:
            return
        self._crud(self.primary["delete_fn"])(self.selected_id)
        self.selected_id = None
        self.refresh()

    def on_export(self):
        getattr(export, self.primary["export_fn"])("export.csv")

    # ----- detail (add / edit / delete child rows) -----
    def _load_children(self):
        for state in self.children_state:
            child = state["cfg"]
            if self.selected_id is None:
                state["rows"] = []
            else:
                state["rows"] = self._crud(child["list_fn"])(self.selected_id)
            state["selected_id"] = None
            # Repopulate without re-triggering _on_child_select (clear() would
            # otherwise restore a stale child selection during the refresh).
            state["list"].blockSignals(True)
            widgets.fill_list(state["list"], state["rows"], child["display"])
            state["list"].blockSignals(False)

    def _on_child_select(self, state):
        state["selected_id"] = widgets.selected_row_value(state["list"], state["rows"])
        row = next(
            (r for r in state["rows"] if r.get("id") == state["selected_id"]), None
        )
        widgets.fill_form(state["fields"], row)

    def _add_child(self, state):
        if self.selected_id is None:
            return
        child = state["cfg"]
        data = widgets.read_form(state["fields"])
        self._crud(child["add_fn"])(
            self.selected_id, *[data[f] for f in child["fields"]]
        )
        widgets.clear_form(state["fields"])
        self._load_children()

    def _edit_child(self, state):
        if state.get("selected_id") is None:
            return
        child = state["cfg"]
        data = widgets.read_form(state["fields"])
        self._crud(child["update_fn"])(
            state["selected_id"], self.selected_id,
            *[data[f] for f in child["fields"]]
        )
        self._load_children()

    def _delete_child(self, state):
        child = state["cfg"]
        record_id = widgets.selected_row_value(state["list"], state["rows"])
        if record_id is None:
            return
        self._crud(child["delete_fn"])(record_id)
        self._load_children()
'''


def widgets_py() -> str:
    """Return ui/widgets.py - the generic, schema-independent UI helpers."""
    return _WIDGETS_PY


def _ui_config(schema: list) -> dict:
    """Build the CONFIG dict that drives the generic MasterDetailWindow."""
    primary = schema[0]
    one = _singular(primary.name)
    pfields = [c for c, _ in primary.columns]
    children = _children_of(schema, primary.name)
    has_children = bool(children)

    columns = ["id"] + pfields + [f"{child}_count" for child, _ in children]
    primary_cfg = {
        "fields": pfields,
        "columns": columns,
        "list_fn": f"get_{primary.name}_with_counts" if has_children else f"get_{primary.name}",
        "search_fn": f"search_{primary.name}" if primary.text_columns else None,
        "add_fn": f"add_{one}",
        "update_fn": f"update_{one}",
        "delete_fn": f"delete_{one}",
        "export_fn": (
            f"export_{primary.name}_with_counts_csv" if has_children
            else f"export_{primary.name}_csv"
        ),
    }

    children_cfg = []
    for table in schema:
        fk = next((c for c, ref in table.foreign_keys if ref == primary.name), None)
        if not fk:
            continue
        child_one = _singular(table.name)
        cfields = [c for c, _ in table.columns if c != fk]
        children_cfg.append({
            "title": table.name.capitalize(),
            "fields": cfields,
            "display": cfields[0] if cfields else "id",
            "list_fn": f"get_{table.name}_for_{one}",
            "add_fn": f"add_{child_one}",
            "update_fn": f"update_{child_one}",
            "delete_fn": f"delete_{child_one}",
        })

    return {"title": primary.name.capitalize(), "primary": primary_cfg, "children": children_cfg}


def master_detail_py(schema: list) -> str:
    """Return ui/master_detail.py: a generated CONFIG for this schema plus the
    constant, generic MasterDetailWindow class."""
    config = _ui_config(schema)
    return (
        "import crud\n"
        "import export\n"
        "from PySide6.QtWidgets import (\n"
        "    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,\n"
        "    QLineEdit, QPushButton, QTableWidget, QListWidget,\n"
        ")\n"
        "from ui import widgets\n\n\n"
        f"CONFIG = {config!r}\n\n\n"
        + _MASTER_DETAIL_CLASS
    )


# =========================================================================== #
# Phase 7 - deterministic dashboard widget (ui/dashboard.py)
# =========================================================================== #

# Generic, config-driven dashboard. Constant class; only the DASHBOARD literal is
# generated per schema. format_value() formats counts (grouped integers) and averages
# (one decimal) consistently; refresh() recomputes every card and toggles an
# empty-state message based on the primary table's count.
_DASHBOARD_WIDGET = '''try:
    from ui.charts import ChartWidget
except Exception:
    ChartWidget = None


def format_value(kind, value):
    """Format a metric for display: counts as grouped integers (e.g. 1,234) and
    averages to one decimal place (e.g. 1.5, 0.0)."""
    if kind == "average":
        return "{:.1f}".format(float(value))
    return "{:,}".format(int(value))


class DashboardWidget(QWidget):
    """A generic grid of summary cards driven by DASHBOARD. Each card shows a label
    and a formatted live value from the reports module; refresh() recomputes them and
    toggles an empty-state message. The reports functions query the database directly,
    so refresh() always reflects current data."""

    COLUMNS = 3

    def __init__(self):
        super().__init__()
        self._values = {}
        self._cards = list(DASHBOARD["cards"])
        self.is_empty = True
        self.chart = None
        root = QVBoxLayout(self)
        root.addWidget(QLabel(DASHBOARD["title"] + " Dashboard"))
        self.empty_label = QLabel("No data yet - add records in the Manage Data tab.")
        root.addWidget(self.empty_label)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)
        root.addWidget(refresh_button)
        export_button = QPushButton("Export Summary")
        export_button.clicked.connect(self.on_export_summary)
        root.addWidget(export_button)
        export_metrics_button = QPushButton("Export Metrics")
        export_metrics_button.clicked.connect(self.on_export_metrics)
        root.addWidget(export_metrics_button)
        grid = QGridLayout()
        root.addLayout(grid)
        for index, card in enumerate(self._cards):
            frame = QFrame()
            box = QVBoxLayout(frame)
            box.addWidget(QLabel(card["label"]))
            value = QLabel("0")
            self._values[card["metric_fn"]] = value
            box.addWidget(value)
            grid.addWidget(frame, index // self.COLUMNS, index % self.COLUMNS)
        if ChartWidget is not None:
            self.chart = ChartWidget()
            root.addWidget(self.chart)
        self.refresh()

    def refresh(self):
        for card in self._cards:
            value = getattr(reports, card["metric_fn"])()
            self._values[card["metric_fn"]].setText(format_value(card["kind"], value))
        primary = getattr(reports, DASHBOARD["primary_metric"])()
        self.is_empty = (primary == 0)
        self.empty_label.setVisible(self.is_empty)
        if self.chart is not None:
            self.chart.refresh()

    def on_export_summary(self):
        reports.export_summary_csv("summary.csv")

    def on_export_metrics(self):
        reports.export_metrics_csv("metrics.csv")
'''


def _dashboard_config(schema: list) -> dict:
    """Build the DASHBOARD dict (summary cards) for a schema: a count card per table
    and an average card per foreign key (child per parent). Table names are rendered
    human-friendly (order_items -> Order Items); connector words ("Total", "Avg",
    "per") are left as-is so existing labels stay stable. Each card carries a `kind`
    and the config carries the `primary_metric` used for empty-state detection."""
    primary = schema[0]

    def label_of(name):
        return name.replace("_", " ").title()

    cards = []
    for table in schema:
        cards.append({
            "label": "Total " + label_of(table.name),
            "metric_fn": "count_" + table.name,
            "kind": "count",
        })
    for table in schema:
        for _col, ref in table.foreign_keys:
            ref_one = _singular(ref)
            cards.append({
                "label": "Avg " + label_of(table.name) + " per " + label_of(ref_one),
                "metric_fn": "avg_" + table.name + "_per_" + ref_one,
                "kind": "average",
            })
    return {
        "title": label_of(primary.name),
        "primary_metric": "count_" + primary.name,
        "cards": cards,
    }


def dashboard_py(schema: list) -> str:
    """Return ui/dashboard.py: a generated DASHBOARD config for this schema plus
    the constant, generic DashboardWidget class."""
    config = _dashboard_config(schema)
    return (
        "import reports\n"
        "from PySide6.QtWidgets import (\n"
        "    QWidget, QVBoxLayout, QGridLayout, QFrame, QLabel, QPushButton,\n"
        ")\n\n\n"
        f"DASHBOARD = {config!r}\n\n\n"
        + _DASHBOARD_WIDGET
    )


# =========================================================================== #
# Phase 7 - deterministic application shell (ui/app_window.py)
# =========================================================================== #

# The tabbed shell. Schema-independent CONSTANT: it composes the existing
# DashboardWidget and MasterDetailWindow without embedding a QMainWindow in a tab.
# ManageWidget (a thin QWidget) hosts MasterDetailWindow's central panel via the
# window's public centralWidget() API, so MasterDetailWindow stays unchanged.
# The MasterDetailWindow is built FIRST (it calls crud.init_db()), so the tables
# exist before the DashboardWidget queries the reports layer.
_APP_WINDOW = '''from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget
from ui.dashboard import DashboardWidget, DASHBOARD
from ui.master_detail import MasterDetailWindow


class ManageWidget(QWidget):
    """Thin wrapper that hosts the existing MasterDetailWindow's panel so the
    master-detail UI can live in a tab without putting a QMainWindow inside a
    QTabWidget. MasterDetailWindow itself is unchanged - we reuse its central
    widget through its public API."""

    def __init__(self):
        super().__init__()
        self.master = MasterDetailWindow()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.master.centralWidget())


class AppWindow(QMainWindow):
    """Application shell: a tabbed window with a Dashboard and a Manage Data tab.
    Activating the Dashboard tab refreshes its summary cards from the live DB."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(DASHBOARD["title"])
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Build Manage first: MasterDetailWindow.__init__ calls crud.init_db(), so
        # the tables exist before the dashboard queries the reports layer.
        self.manage = ManageWidget()
        self.dashboard = DashboardWidget()

        self.tabs.addTab(self.dashboard, "Dashboard")
        self.tabs.addTab(self.manage, "Manage Data")
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index):
        if self.tabs.widget(index) is self.dashboard:
            self.dashboard.refresh()
'''


def app_window_py(schema=None) -> str:
    """Return ui/app_window.py - the constant, schema-independent tabbed shell
    (ManageWidget + AppWindow). `schema` is accepted for generator-signature
    consistency but unused (the shell composes the per-schema widgets at runtime)."""
    return _APP_WINDOW


# =========================================================================== #
# Phase 7 - deterministic dashboard charts (ui/charts.py)
# =========================================================================== #

# Metadata-driven bar chart over the reporting layer. The constant ChartWidget renders
# with core Qt (QProgressBar) - no charting dependency. chart_data() computes live
# values + normalized ratios; refresh() updates the bars and toggles an empty-state label.
_CHART_WIDGET = '''def chart_data():
    """Return live chart data from the reports layer:
    {"title", "bars": [{"label", "value", "ratio"}], "max", "is_empty"}.
    ratio = value / max (0.0 when max is 0); is_empty is True when max is 0."""
    pairs = [(bar["label"], getattr(reports, bar["metric_fn"])()) for bar in CHARTS["bars"]]
    max_value = max((value for _label, value in pairs), default=0)
    bars = [
        {"label": label, "value": value,
         "ratio": (value / max_value) if max_value else 0.0}
        for label, value in pairs
    ]
    avg_pairs = [(a["label"], getattr(reports, a["metric_fn"])()) for a in CHARTS["averages"]]
    max_avg = max((value for _label, value in avg_pairs), default=0)
    averages = [
        {"label": label, "value": value,
         "ratio": (value / max_avg) if max_avg else 0.0}
        for label, value in avg_pairs
    ]
    total = sum(value for _label, value in pairs)
    return {"title": CHARTS["title"], "bars": bars, "averages": averages,
            "max": max_value, "total": total, "is_empty": max_value == 0}


class ChartWidget(QWidget):
    """A simple, dependency-free bar chart: one labelled QProgressBar per metric,
    sized by its share of the largest value. refresh() recomputes from chart_data()
    and shows an empty-state label when there is no data."""

    def __init__(self):
        super().__init__()
        self.bars = {}
        self.avg_bars = {}
        self.is_empty = True
        root = QVBoxLayout(self)
        root.addWidget(QLabel(CHARTS["title"]))
        self.empty_label = QLabel("No data to chart yet.")
        root.addWidget(self.empty_label)
        for bar in CHARTS["bars"]:
            row = QHBoxLayout()
            row.addWidget(QLabel(bar["label"]))
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(0)
            self.bars[bar["label"]] = progress
            row.addWidget(progress)
            root.addLayout(row)
        self.total_label = QLabel("Total: 0")
        root.addWidget(self.total_label)
        if CHARTS["averages"]:
            root.addWidget(QLabel("Averages"))
            for bar in CHARTS["averages"]:
                row = QHBoxLayout()
                row.addWidget(QLabel(bar["label"]))
                progress = QProgressBar()
                progress.setRange(0, 100)
                progress.setValue(0)
                self.avg_bars[bar["label"]] = progress
                row.addWidget(progress)
                root.addLayout(row)
        self.refresh()

    def refresh(self):
        data = chart_data()
        self.is_empty = data["is_empty"]
        for bar in data["bars"]:
            progress = self.bars[bar["label"]]
            progress.setValue(int(round(bar["ratio"] * 100)))
            progress.setFormat(str(bar["value"]))
        for bar in data["averages"]:
            progress = self.avg_bars[bar["label"]]
            progress.setValue(int(round(bar["ratio"] * 100)))
            progress.setFormat(str(bar["value"]))
        self.empty_label.setVisible(self.is_empty)
        self.total_label.setText("Total: " + "{:,}".format(data["total"]))
'''


def _charts_config(schema: list) -> dict:
    """Build the CHARTS dict: one bar per table (count_<table>), human-friendly
    labels, in schema/table order (deterministic)."""
    def label_of(name):
        return name.replace("_", " ").title()

    bars = [
        {"label": label_of(table.name), "metric_fn": "count_" + table.name}
        for table in schema
    ]
    averages = []
    for table in schema:
        for _col, ref in table.foreign_keys:
            ref_one = _singular(ref)
            averages.append({
                "label": "Avg " + label_of(table.name) + " per " + label_of(ref_one),
                "metric_fn": "avg_" + table.name + "_per_" + ref_one,
            })
    return {"title": "Records by Table", "bars": bars, "averages": averages}


def charts_py(schema: list) -> str:
    """Return ui/charts.py: a generated CHARTS config plus the constant, generic
    ChartWidget (a dependency-free QProgressBar bar chart over reports)."""
    config = _charts_config(schema)
    return (
        "import reports\n"
        "from PySide6.QtWidgets import (\n"
        "    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,\n"
        ")\n\n\n"
        f"CHARTS = {config!r}\n\n\n"
        + _CHART_WIDGET
    )
