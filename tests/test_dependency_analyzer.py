"""Tests for import-aware build ordering (brains/dependency_analyzer).

Per spec: a file that imports another PROJECT file is built AFTER it; non-project
imports are ignored; the order is deterministic; and any problem (cycle, invalid
syntax) degrades gracefully to the original file order without raising.
"""

from brains.dependency_analyzer import derive_build_order


def test_imported_file_built_before_importer():
    files = [
        {"path": "app.py", "content": "import util\nprint(util.x)\n"},
        {"path": "util.py", "content": "x = 1\n"},
    ]
    order = derive_build_order(files)
    assert order.index("util.py") < order.index("app.py")


def test_from_import_is_detected():
    files = [
        {"path": "app.py", "content": "from util import x\n"},
        {"path": "util.py", "content": "x = 1\n"},
    ]
    order = derive_build_order(files)
    assert order.index("util.py") < order.index("app.py")


def test_non_project_imports_ignored_keeps_original_order():
    files = [
        {"path": "a.py", "content": "import os\nimport sys\n"},
        {"path": "b.py", "content": "import json\n"},
    ]
    # No project-to-project edges -> stable original order.
    assert derive_build_order(files) == ["a.py", "b.py"]


def test_circular_dependency_falls_back_without_raising():
    files = [
        {"path": "a.py", "content": "import b\n"},
        {"path": "b.py", "content": "import a\n"},
    ]
    order = derive_build_order(files)
    assert order == ["a.py", "b.py"]  # original order, no exception


def test_invalid_syntax_falls_back_without_raising():
    files = [
        {"path": "a.py", "content": "def (this is not python\n"},
        {"path": "b.py", "content": "x = 1\n"},
    ]
    order = derive_build_order(files)
    assert order == ["a.py", "b.py"]


def test_single_file_returned_as_is():
    assert derive_build_order([{"path": "only.py", "content": "x=1\n"}]) == ["only.py"]
    assert derive_build_order([]) == []
