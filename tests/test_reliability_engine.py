#!/usr/bin/env python3
"""Unit tests for the Reliability Engine modules."""

import importlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from memory import store
from brains.builder import BuilderBrain
from reliability_engine import grammar_constraints, patch_apply, state_store
from reliability_engine.config_loader import load_config
from reliability_engine.dep_graph import DependencyGraph
from reliability_engine.edit_router import classify_request, read_targets
from reliability_engine.env_isolation import clean_subprocess_env, isolated_test_environment
from reliability_engine.model_availability import ReliabilityModelRouter
from reliability_engine.planner_spec import normalize_planner_output, persist_subtasks
from reliability_engine.rag_index import CodebaseRAGIndex
from reliability_engine.regression_runner import RegressionRunner
from reliability_engine.self_consistency import GeneratedCandidate, SelfConsistencyRunner
from reliability_engine.static_gate import StaticGate
from reliability_engine.test_loop import ReliabilityTestLoop, requested_identifier, test_coverage_expected
from reliability_engine.worktree_sandbox import WorktreeSandbox
from safety.gate import AllowAllGate

unittest = importlib.import_module("unittest")


class KeywordEmbeddingFunction:
    @staticmethod
    def name():
        return "keyword"

    @staticmethod
    def is_legacy():
        return False

    @staticmethod
    def default_space():
        return "l2"

    @staticmethod
    def supported_spaces():
        return ["l2"]

    @staticmethod
    def get_config():
        return {}

    @staticmethod
    def build_from_config(config):
        return KeywordEmbeddingFunction()

    def _embed(self, docs):
        terms = ["sqlite", "memory", "console", "builder", "lessons", "rich"]
        vectors = []
        for doc in docs:
            words = set(str(doc).lower().replace("_", " ").split())
            vectors.append([1.0 if term in words else 0.0 for term in terms])
        return vectors

    def __call__(self, input):
        return self._embed(input)

    def embed_query(self, input):
        return self._embed(input)

    def embed_documents(self, input):
        return self._embed(input)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TempDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = store.DATA_DIR
        self.old_db_path = store.DB_PATH
        store.DATA_DIR = self.tmp.name
        store.DB_PATH = os.path.join(self.tmp.name, "test.db")

    def tearDown(self):
        store.DATA_DIR = self.old_data_dir
        store.DB_PATH = self.old_db_path
        self.tmp.cleanup()


class TestStateStore(TempDBTest):
    def test_schema_and_records_round_trip(self):
        subtask_id = state_store.create_subtask("edit module", ["pkg/a.py"], 2)
        attempt_id = state_store.record_attempt(
            subtask_id, 1, "coder", "--- a\n+++ b\n@@ -1 +1 @@\n-a\n+b\n",
            "passed", "failed", "trace", raw_output="```diff\nraw\n```", prompt="SYSTEM:\ns\n\nUSER:\np",
        )
        issue_id = state_store.record_known_issue(subtask_id, "still failing", "trace")
        self.assertGreater(subtask_id, 0)
        self.assertGreater(attempt_id, 0)
        self.assertGreater(issue_id, 0)
        rows = state_store.list_subtasks()
        self.assertEqual(rows[0]["files_touched"], ["pkg/a.py"])
        self.assertEqual(state_store.increment_attempts(subtask_id), 1)
        with store._connect() as conn:
            attempt = conn.execute("SELECT raw_output, prompt FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        self.assertEqual(attempt["raw_output"], "```diff\nraw\n```")
        self.assertEqual(attempt["prompt"], "SYSTEM:\ns\n\nUSER:\np")

    def test_existing_attempts_table_gets_raw_output_column(self):
        os.makedirs(self.tmp.name, exist_ok=True)
        with store._connect() as conn:
            conn.execute(
                "CREATE TABLE attempts ("
                "id INTEGER PRIMARY KEY, subtask_id INTEGER, attempt_num INTEGER, "
                "model_used TEXT, diff TEXT, static_gate_result TEXT, test_result TEXT, "
                "error_trace TEXT, timestamp TEXT)"
            )
        state_store.init_state()
        with store._connect() as conn:
            columns = [row["name"] for row in conn.execute("PRAGMA table_info(attempts)").fetchall()]
        self.assertIn("raw_output", columns)
        self.assertIn("prompt", columns)

    def test_project_state_written(self):
        subtask_id = state_store.create_subtask("one", ["a.py"])
        path = state_store.write_project_state(self.tmp.name)
        self.assertTrue(os.path.exists(path))
        loaded = state_store.load_project_state(self.tmp.name)
        self.assertEqual(loaded["subtasks"][0]["id"], subtask_id)


class TestPlannerSpec(TempDBTest):
    def test_normalizes_existing_project_plan_files(self):
        subtasks = normalize_planner_output({
            "files": [{"path": "app.py", "purpose": "main app"}],
        })
        self.assertEqual(subtasks[0]["description"], "main app")
        self.assertEqual(subtasks[0]["files_touched"], ["app.py"])

    def test_rejects_unsafe_path(self):
        with self.assertRaises(ValueError):
            normalize_planner_output({
                "subtasks": [{"description": "bad", "files_touched": ["../x.py"]}],
            })

    def test_persists_subtasks(self):
        persisted = persist_subtasks([{
            "description": "do it",
            "status": "pending",
            "files_touched": ["x.py"],
            "blast_radius": 0,
            "attempts": 0,
        }], self.tmp.name)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(state_store.list_subtasks()[0]["description"], "do it")


class TestEditRouter(TempDBTest):
    def test_existing_file_routes_to_edit_and_builder_prompt_has_real_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            os.makedirs(os.path.join(tmp, "memory"))
            write_text(os.path.join(tmp, "memory", "store.py"), "REAL_SENTINEL = 42\n")
            subprocess.run(["git", "add", "memory/store.py"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)

            route = classify_request(tmp, "Add count_subtasks to memory/store.py")
            self.assertTrue(route.is_edit)
            self.assertEqual(route.targets, ["memory/store.py"])
            self.assertFalse(route.inferred_target)

            contents = read_targets(tmp, route.targets)

            class CaptureEngine:
                name = "capture"

                def __init__(self):
                    self.prompt = ""
                    self.system = ""

                def generate(self, prompt, system=""):
                    self.prompt = prompt
                    self.system = system
                    return (
                        "--- a/memory/store.py\n+++ b/memory/store.py\n@@ -1 +1 @@\n"
                        "-REAL_SENTINEL = 42\n+REAL_SENTINEL = 43\n"
                    )

            engine = CaptureEngine()
            BuilderBrain(None, engine=engine).generate_edit_diff(
                "Add count_subtasks to memory/store.py",
                "memory/store.py",
                contents["memory/store.py"],
                [],
            )
            self.assertIn("CURRENT CONTENT OF TARGET FILES", engine.prompt)
            self.assertIn("REAL_SENTINEL = 42", engine.prompt)
            self.assertIn("Keep the edit focused on the target file and the requested change", engine.system)

    def test_multi_target_edit_prompt_includes_file_markers_and_all_targets(self):
        class CaptureEngine:
            name = "capture"

            def __init__(self):
                self.prompt = ""

            def generate(self, prompt, system=""):
                self.prompt = prompt
                return "<<<<<<< FIND\nx\n=======\ny\n>>>>>>> REPLACE\n"

        engine = CaptureEngine()
        BuilderBrain(None, engine=engine).generate_edit_diff(
            "Add x with tests",
            "pkg/x.py",
            "def x():\n    return 1\n",
            [],
            target_contents={
                "pkg/x.py": "def x():\n    return 1\n",
                "tests/test_x.py": "def test_old():\n    pass\n",
            },
        )
        self.assertIn("<<<<<<< FILE relative/path.py", engine.prompt)
        self.assertIn("--- pkg/x.py ---", engine.prompt)
        self.assertIn("--- tests/test_x.py ---", engine.prompt)

    def test_no_existing_file_path_can_be_marked_as_inferred_edit_target(self):
        route = classify_request(
            self.tmp.name,
            "Add a count helper for subtasks",
            inferred_target="memory/store.py",
        )
        self.assertTrue(route.is_edit)
        self.assertTrue(route.inferred_target)
        subtask_id = state_store.create_subtask(
            "Add a count helper for subtasks",
            route.targets,
            inferred_target=route.inferred_target,
        )
        with store._connect() as conn:
            row = conn.execute("SELECT inferred_target FROM subtasks WHERE id = ?", (subtask_id,)).fetchone()
        self.assertEqual(row["inferred_target"], 1)

    def test_greenfield_request_without_existing_file_stays_greenfield(self):
        route = classify_request(self.tmp.name, "Build a new CLI calculator app")
        self.assertFalse(route.is_edit)
        self.assertEqual(route.mode, "greenfield")

    def test_explicit_edit_prompt_omits_dependency_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            os.makedirs(os.path.join(tmp, "memory"))
            os.makedirs(os.path.join(tmp, "tests"))
            write_text(os.path.join(tmp, "memory", "__init__.py"), "")
            write_text(os.path.join(tmp, "memory", "store.py"), "REAL_TARGET = 1\n")
            write_text(
                os.path.join(tmp, "tests", "test_store.py"),
                "from memory import store\nassert store.REAL_TARGET == 1\n",
            )
            subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)

            graph = DependencyGraph(tmp).build()
            from reliability_engine.orchestrator import ReliabilityOrchestrator
            orchestrator = ReliabilityOrchestrator(AllowAllGate(), repo_root=tmp, assume_yes=True)
            self.assertTrue(orchestrator._dependency_context(graph, ["memory/store.py"]))

            class CaptureEngine:
                name = "capture"

                def __init__(self):
                    self.prompt = ""

                def generate(self, prompt, system=""):
                    self.prompt = prompt
                    return (
                        "--- a/memory/store.py\n+++ b/memory/store.py\n@@ -1 +1 @@\n"
                        "-REAL_TARGET = 1\n+REAL_TARGET = 2\n"
                    )

            engine = CaptureEngine()
            BuilderBrain(None, engine=engine).generate_edit_diff(
                "Change memory/store.py",
                "memory/store.py",
                read_text(os.path.join(tmp, "memory", "store.py")),
                [],
                "candidate sample 1",
            )
            self.assertIn("CURRENT CONTENT OF TARGET FILES", engine.prompt)
            self.assertIn("REAL_TARGET = 1", engine.prompt)
            self.assertIn("PREVIOUS FAILURE:\ncandidate sample 1", engine.prompt)
            self.assertNotIn("tests/test_store.py", engine.prompt)
            self.assertNotIn("REFERENCE CODE", engine.prompt)

    def test_no_context_prompt_grounding_regression(self):
        current = (
            "def stats() -> dict:\n"
            "    out = {\"builds\": 0, \"lessons\": 0}\n"
            "    return out\n"
        )
        replacement = (
            "<<<<<<< FIND\n"
            "def stats() -> dict:\n"
            "    out = {\"builds\": 0, \"lessons\": 0}\n"
            "    return out\n"
            "=======\n"
            "def stats() -> dict:\n"
            "    out = {\"builds\": 0, \"lessons\": 0, \"subtasks\": 0}\n"
            "    return out\n"
            "\n"
            "def count_subtasks() -> int:\n"
            "    return 0\n"
            ">>>>>>> REPLACE\n"
        )

        class CaptureEngine:
            def generate(self, prompt, system=""):
                self.prompt = prompt
                return replacement

        engine = CaptureEngine()
        raw = BuilderBrain(None, engine=engine).generate_edit_diff(
            "Add a function to memory/store.py named count_subtasks",
            "memory/store.py",
            current,
            [],
        )
        blocks = patch_apply.parse_replace_blocks(raw)
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "memory"))
            write_text(os.path.join(tmp, "memory", "store.py"), current)
            self.assertTrue(patch_apply.verify_replace_blocks(tmp, "memory/store.py", blocks).ok)
        self.assertNotIn("REFERENCE CODE", engine.prompt)
        self.assertTrue(any("count_subtasks" in block.replace for block in blocks))

    def test_edit_context_excludes_prompt_construction_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            for directory in ("memory", "reliability_engine", "brains", "app"):
                os.makedirs(os.path.join(tmp, directory))
            write_text(os.path.join(tmp, "memory", "__init__.py"), "")
            write_text(os.path.join(tmp, "memory", "store.py"), "VALUE = 1\n")
            write_text(os.path.join(tmp, "app", "__init__.py"), "")
            write_text(os.path.join(tmp, "app", "consumer.py"), "from memory import store\nprint(store.VALUE)\n")
            write_text(
                os.path.join(tmp, "reliability_engine", "orchestrator.py"),
                "from memory import store\nPROMPT = 'ONLY JSON'\n",
            )
            write_text(
                os.path.join(tmp, "brains", "planner.py"),
                "from memory import store\nSYSTEM_PROMPT = 'ONLY JSON'\n",
            )
            write_text(
                os.path.join(tmp, "brains", "builder.py"),
                "from memory import store\nSYSTEM_PROMPT = 'ONLY CODE'\n",
            )
            subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)

            graph = DependencyGraph(tmp).build()
            from reliability_engine.orchestrator import ReliabilityOrchestrator
            context = ReliabilityOrchestrator(
                AllowAllGate(), repo_root=tmp, assume_yes=True
            )._dependency_context(graph, ["memory/store.py"], limit=10)
            paths = [item["metadata"]["path"] for item in context]
            self.assertIn("app/consumer.py", paths)
            self.assertNotIn("reliability_engine/orchestrator.py", paths)
            self.assertNotIn("brains/planner.py", paths)
            self.assertNotIn("brains/builder.py", paths)

    def test_builder_edit_prompt_uses_neutral_wording(self):
        class CaptureEngine:
            def generate(self, prompt, system=""):
                self.prompt = prompt
                self.system = system
                return "<<<<<<< FIND\nx\n=======\ny\n>>>>>>> REPLACE\n"

        engine = CaptureEngine()
        BuilderBrain(None, engine=engine).generate_edit_diff("change x", "x.py", "x\n", [])
        combined = engine.system + "\n" + engine.prompt
        self.assertIn(
            "Choose a short unchanged snippet from the target file and copy it into FIND.",
            combined,
        )
        self.assertIn("Keep the edit focused on the target file and the requested change.", combined)
        self.assertNotIn("Do not create a new project", combined)
        self.assertNotIn("Do not rewrite unrelated code", combined)
        self.assertNotIn("Do not paraphrase", combined)
        self.assertNotIn("character-for-character", combined)


class TestDepGraph(unittest.TestCase):
    def test_import_graph_blast_radius(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "pkg"))
            write_text(os.path.join(tmp, "pkg", "__init__.py"), "")
            write_text(os.path.join(tmp, "pkg", "a.py"), "def f():\n    return 1\n")
            write_text(os.path.join(tmp, "pkg", "b.py"), "from pkg import a\nprint(a.f())\n")
            graph = DependencyGraph(tmp).build()
            self.assertEqual(graph.blast_radius(["pkg/a.py"]), 1)
            self.assertIn("f", graph.calls["pkg/b.py"])


class TestGrammarAndPatch(unittest.TestCase):
    def test_validates_unified_diff(self):
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(grammar_constraints.validate_builder_patch(diff), diff)
        with self.assertRaises(ValueError):
            grammar_constraints.validate_builder_patch("not a diff")

    def test_validates_fenced_unified_diff_after_stripping(self):
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(grammar_constraints.validate_builder_patch(f"```diff\n{diff}```\n"), diff)
        self.assertEqual(grammar_constraints.validate_builder_patch(f"```patch\n{diff}```"), diff)
        self.assertEqual(grammar_constraints.validate_builder_patch(f"```\n{diff}```"), diff)
        with self.assertRaises(ValueError):
            grammar_constraints.validate_builder_patch("```diff\nnot a diff\n```")

    def test_apply_diff_and_full_file_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "a = 1\n")
            subprocess.run(["git", "add", "x.py"], cwd=tmp, check=True, capture_output=True)
            diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"
            self.assertTrue(patch_apply.apply_diff(tmp, diff).ok)
            self.assertEqual(read_text(os.path.join(tmp, "x.py")), "a = 2\n")
            self.assertFalse(patch_apply.apply_full_file(tmp, "x.py", "1\n2\n3", 2).ok)
            self.assertTrue(patch_apply.apply_full_file(tmp, "x.py", "short\n", 2).ok)

    def test_grounding_check_detects_mismatched_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            diff = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n fictional = 0\n-value = 1\n+value = 2\n"
            result = patch_apply.verify_diff_grounding(tmp, diff)
            self.assertFalse(result.ok)
            self.assertIn("fictional = 0", result.message)

    def test_grounding_check_accepts_matching_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            diff = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-value = 1\n+value = 2\n keep = 3\n"
            self.assertTrue(patch_apply.verify_diff_grounding(tmp, diff).ok)

    def test_find_replace_block_that_matches_applies_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            raw = "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
            blocks = patch_apply.parse_replace_blocks(raw)
            result = patch_apply.apply_replace_blocks(tmp, "x.py", blocks)
            self.assertTrue(result.ok, result.stderr)
            self.assertEqual(read_text(os.path.join(tmp, "x.py")), "value = 2\nkeep = 3\n")

    def test_find_replace_block_mismatch_is_caught_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.py")
            write_text(path, "value = 1\nkeep = 3\n")
            raw = "<<<<<<< FIND\nfictional = 0\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
            blocks = patch_apply.parse_replace_blocks(raw)
            grounding = patch_apply.verify_replace_blocks(tmp, "x.py", blocks)
            self.assertFalse(grounding.ok)
            self.assertIn("FIND TEXT", grounding.message)
            result = patch_apply.apply_replace_blocks(tmp, "x.py", blocks)
            self.assertFalse(result.ok)
            self.assertEqual(read_text(path), "value = 1\nkeep = 3\n")

    def test_multiple_find_replace_blocks_apply_in_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_text(os.path.join(tmp, "x.py"), "a = 1\nb = 2\n")
            raw = (
                "<<<<<<< FIND\n"
                "a = 1\n"
                "=======\n"
                "a = 10\n"
                ">>>>>>> REPLACE\n"
                "<<<<<<< FIND\n"
                "b = 2\n"
                "=======\n"
                "b = 20\n"
                ">>>>>>> REPLACE\n"
            )
            blocks = patch_apply.parse_replace_blocks(raw)
            result = patch_apply.apply_replace_blocks(tmp, "x.py", blocks)
            self.assertTrue(result.ok, result.stderr)
            self.assertEqual(read_text(os.path.join(tmp, "x.py")), "a = 10\nb = 20\n")

    def test_find_replace_blocks_can_target_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tests"))
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            write_text(os.path.join(tmp, "tests", "test_x.py"), "def test_old():\n    pass\n")
            raw = (
                "<<<<<<< FILE x.py\n"
                "<<<<<<< FIND\n"
                "value = 1\n"
                "=======\n"
                "value = 2\n"
                ">>>>>>> REPLACE\n"
                "<<<<<<< FILE tests/test_x.py\n"
                "<<<<<<< FIND\n"
                "def test_old():\n"
                "    pass\n"
                "=======\n"
                "def test_value():\n"
                "    assert value == 2\n"
                ">>>>>>> REPLACE\n"
            )
            blocks = patch_apply.parse_replace_blocks(raw)
            self.assertEqual([block.path for block in blocks], ["x.py", "tests/test_x.py"])
            grounding = patch_apply.verify_replace_blocks(tmp, "x.py", blocks)
            self.assertTrue(grounding.ok, grounding.message)
            result = patch_apply.apply_replace_blocks(tmp, "x.py", blocks)
            self.assertTrue(result.ok, result.stderr)
            self.assertIn("value = 2", read_text(os.path.join(tmp, "x.py")))
            self.assertIn("test_value", read_text(os.path.join(tmp, "tests", "test_x.py")))

    def test_find_replace_blocks_accept_shorthand_file_marker(self):
        raw = (
            "```diff\n"
            "<<<<<<< tests/test_x.py\n"
            "def test_old():\n"
            "    pass\n"
            "=======\n"
            "def test_new():\n"
            "    pass\n"
            ">>>>>>> REPLACE\n"
            "```\n"
        )
        blocks = patch_apply.parse_replace_blocks(raw)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].path, "tests/test_x.py")
        self.assertIn("test_old", blocks[0].find)


class TestStaticAndRegression(unittest.TestCase):
    def test_static_gate_blocks_syntax_error_and_passes_clean_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_text(
                os.path.join(tmp, "ok.py"),
                "def add(a: int, b: int) -> int:\n    return a + b\n",
            )
            clean = StaticGate().run(tmp)
            self.assertTrue(clean.ok, clean.output)
            write_text(os.path.join(tmp, "bad.py"), "def nope(:\n")
            result = StaticGate().run(tmp)
            self.assertFalse(result.ok)
            self.assertEqual(result.stage, "syntax")

    def test_static_gate_scopes_to_touched_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_text(os.path.join(tmp, "dirty.py"), "import os\n")
            write_text(os.path.join(tmp, "clean.py"), "def add(a: int, b: int) -> int:\n    return a + b\n")
            result = StaticGate(require_mypy=False).run(tmp, ["clean.py"])
            self.assertTrue(result.ok, result.output)
            unscoped = StaticGate(require_mypy=False).run(tmp)
            self.assertFalse(unscoped.ok)
            self.assertEqual(unscoped.stage, "lint")

    def test_static_gate_delta_allows_preexisting_mypy_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.py")
            write_text(
                path,
                "def broken() -> int:\n"
                "    return 'bad'\n"
                "\n"
                "def clean() -> int:\n"
                "    return 1\n",
            )
            gate = StaticGate(require_lint=False)
            before = gate.collect_issues(tmp, ["x.py"])
            write_text(
                path,
                "def broken() -> int:\n"
                "    return 'bad'\n"
                "\n"
                "def clean() -> int:\n"
                "    return 2\n",
            )
            result = gate.run_delta(tmp, ["x.py"], before)
            self.assertTrue(result.ok, result.output)
            self.assertTrue(result.details["pre_existing"])

    def test_static_gate_delta_blocks_new_mypy_error_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.py")
            write_text(
                path,
                "def broken() -> int:\n"
                "    return 'bad'\n"
                "\n"
                "def clean() -> int:\n"
                "    return 1\n",
            )
            gate = StaticGate(require_lint=False)
            before = gate.collect_issues(tmp, ["x.py"])
            write_text(
                path,
                "def broken() -> int:\n"
                "    return 'bad'\n"
                "\n"
                "def clean() -> int:\n"
                "    return 1 + 'bad'\n",
            )
            result = gate.run_delta(tmp, ["x.py"], before)
            self.assertFalse(result.ok)
            self.assertEqual(result.stage, "mypy")
            self.assertIn("Unsupported operand types", result.output)
            self.assertNotIn("return 'bad'", result.output)

    def test_static_gate_delta_clean_file_clean_change_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.py")
            write_text(path, "def clean() -> int:\n    return 1\n")
            gate = StaticGate()
            before = gate.collect_issues(tmp, ["x.py"])
            write_text(path, "def clean() -> int:\n    return 2\n")
            result = gate.run_delta(tmp, ["x.py"], before)
            self.assertTrue(result.ok, result.output)
            self.assertEqual(result.details["new"], [])

    def test_regression_runner_runs_pytest(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tests"))
            write_text(os.path.join(tmp, "tests", "test_ok.py"),
                "def test_ok():\n    assert True\n"
            )
            result = RegressionRunner().run(tmp)
            self.assertTrue(result.ok, result.output)

    def test_regression_runner_uses_current_interpreter(self):
        command = RegressionRunner.default_command()
        self.assertIn(shlex.quote(sys.executable), command)
        self.assertIn("-m pytest -q", command)
        self.assertNotEqual(command.split()[0], "python")
        self.assertNotEqual(command.split()[0], "python3")

    def test_clean_subprocess_env_removes_orchestration_model_override(self):
        env = clean_subprocess_env({"AUTOCORP_MODEL": "qwen2.5-coder:14b", "PATH": "/bin"})
        self.assertNotIn("AUTOCORP_MODEL", env)
        self.assertEqual(env["PATH"], "/bin")

    def test_isolated_test_environment_temporarily_removes_model_override(self):
        old_value = os.environ.get("AUTOCORP_MODEL")
        os.environ["AUTOCORP_MODEL"] = "qwen2.5-coder:14b"
        try:
            with isolated_test_environment():
                self.assertNotIn("AUTOCORP_MODEL", os.environ)
            self.assertEqual(os.environ["AUTOCORP_MODEL"], "qwen2.5-coder:14b")
        finally:
            if old_value is None:
                os.environ.pop("AUTOCORP_MODEL", None)
            else:
                os.environ["AUTOCORP_MODEL"] = old_value

    def test_regression_runner_passes_clean_environment(self):
        import reliability_engine.regression_runner as regression_runner

        captured = {}

        class Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs["env"]
            return Proc()

        old_run = regression_runner.subprocess.run
        old_value = os.environ.get("AUTOCORP_MODEL")
        os.environ["AUTOCORP_MODEL"] = "qwen2.5-coder:14b"
        regression_runner.subprocess.run = fake_run
        try:
            result = RegressionRunner("true").run(os.getcwd())
        finally:
            regression_runner.subprocess.run = old_run
            if old_value is None:
                os.environ.pop("AUTOCORP_MODEL", None)
            else:
                os.environ["AUTOCORP_MODEL"] = old_value

        self.assertTrue(result.ok)
        self.assertEqual(captured["command"], "true")
        self.assertNotIn("AUTOCORP_MODEL", captured["env"])


class TestConfigRouterRag(unittest.TestCase):
    def test_config_merges_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cfg.yaml")
            write_text(path, "test_loop:\n  max_attempts: 7\n")
            cfg = load_config(path)
            self.assertEqual(cfg["test_loop"]["max_attempts"], 7)
            self.assertEqual(cfg["model_router"]["fallback_model"], "qwen2.5:14b")

    def test_model_router_falls_back(self):
        old = __import__("core.llm").llm.check_ollama
        def check(model):
            return (model == "general", "message")
        __import__("core.llm").llm.check_ollama = check
        try:
            decision = ReliabilityModelRouter("coder", "general").route()
        finally:
            __import__("core.llm").llm.check_ollama = old
        self.assertTrue(decision.fallback_used)
        self.assertEqual(decision.model, "general")

    def test_rag_indexes_real_repo_files_and_returns_relevant_results(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "memory"))
            os.makedirs(os.path.join(tmp, "core"))
            shutil.copy(
                os.path.join(repo_root, "memory", "store.py"),
                os.path.join(tmp, "memory", "store.py"),
            )
            shutil.copy(
                os.path.join(repo_root, "core", "console.py"),
                os.path.join(tmp, "core", "console.py"),
            )
            idx = CodebaseRAGIndex(
                tmp,
                persist_dir=os.path.join(tmp, ".chroma"),
                collection_name="real_repo_subset",
                embedding_function=KeywordEmbeddingFunction(),
            )
            self.assertGreater(idx.rebuild(), 0)
            results = idx.query("sqlite memory lessons", n_results=2)
            self.assertTrue(results)
            self.assertEqual(results[0]["metadata"]["path"], os.path.join("memory", "store.py"))
            self.assertIn("sqlite", results[0]["document"].lower())


class TestWorktreeSandbox(unittest.TestCase):
    def test_create_merge_and_cleanup_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            write_text(os.path.join(tmp, "x.txt"), "one\n")
            subprocess.run(["git", "add", "x.txt"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)
            sandbox = WorktreeSandbox(tmp, scratch_dir=os.path.join(tmp, "scratch"))
            worktree = sandbox.create(1)
            write_text(os.path.join(worktree.path, "x.txt"), "two\n")
            self.assertTrue(sandbox.has_changes(worktree))
            sandbox.merge_to_main(worktree)
            self.assertEqual(read_text(os.path.join(tmp, "x.txt")), "two\n")
            sandbox.rollback(worktree, keep=False)
            self.assertFalse(os.path.exists(worktree.path))

    def test_reused_subtask_id_across_runs_does_not_destroy_preserved_worktree(self):
        """Regression test: subtask ids come from an autoincrement-less SQLite
        column that state_store.reset_subtasks() clears at the start of every
        run, so the same id (e.g. 1) is reused across separate, unrelated
        runs. Before the run_id namespacing fix, a worktree deliberately kept
        after a `blocked` result (rollback(keep=True)) would be silently
        destroyed the next time a new run's WorktreeSandbox created a worktree
        for the same reused id."""
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            write_text(os.path.join(tmp, "x.txt"), "one\n")
            subprocess.run(["git", "add", "x.txt"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)

            scratch = os.path.join(tmp, "scratch")
            run_a = WorktreeSandbox(tmp, scratch_dir=scratch)
            worktree_a = run_a.create(1)
            write_text(os.path.join(worktree_a.path, "diagnostic.txt"), "blocked-state-evidence\n")
            run_a.rollback(worktree_a, keep=True)  # simulate a `blocked` result being preserved
            self.assertTrue(os.path.exists(worktree_a.path))

            run_b = WorktreeSandbox(tmp, scratch_dir=scratch)
            self.assertNotEqual(run_a.run_id, run_b.run_id)
            worktree_b = run_b.create(1)  # same reused subtask id, different run

            self.assertTrue(os.path.exists(worktree_a.path))
            self.assertEqual(
                read_text(os.path.join(worktree_a.path, "diagnostic.txt")), "blocked-state-evidence\n"
            )
            self.assertNotEqual(worktree_a.path, worktree_b.path)
            self.assertNotEqual(worktree_a.branch, worktree_b.branch)

            run_a.rollback(worktree_a, keep=False)
            run_b.rollback(worktree_b, keep=False)


class TestReliabilityOrchestratorEndToEnd(TempDBTest):
    def test_run_executes_disposable_edit_workflow_and_cleans_worktree(self):
        """End-to-end proof for the production entry point:
        ReliabilityOrchestrator.run() creates a real git worktree, plans an
        edit request, applies a generated FIND/REPLACE patch, validates it,
        runs regression tests, merges into only the disposable target repo, and
        cleans the successful worktree. The AutoCorp repository itself is only
        observed before/after so this test cannot mutate the user's real repo.
        """
        import reliability_engine.orchestrator as orchestrator_module

        autocorp_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        autocorp_before = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=autocorp_root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

        class DeterministicDecision:
            model = "deterministic-builder"
            fallback_used = False
            reason = "test"

        class DeterministicRouter:
            def __init__(self, builder_model, fallback_model):
                self.builder_model = builder_model
                self.fallback_model = fallback_model

            def route(self):
                return DeterministicDecision()

        class DeterministicEngine:
            model = "deterministic-builder"
            name = "deterministic-builder"

            def __init__(self, model=None):
                self.model = model or self.model

            def generate(self, prompt, system=""):
                return (
                    "<<<<<<< FILE app.py\n"
                    "<<<<<<< FIND\n"
                    "def answer():\n"
                    "    return 1\n"
                    "=======\n"
                    "def answer():\n"
                    "    return 42\n"
                    ">>>>>>> REPLACE\n"
                    "<<<<<<< FILE tests/test_app.py\n"
                    "<<<<<<< FIND\n"
                    "from app import answer\n"
                    "\n"
                    "\n"
                    "def test_answer():\n"
                    "    assert answer() == 1\n"
                    "=======\n"
                    "from app import answer\n"
                    "\n"
                    "\n"
                    "def test_answer():\n"
                    "    assert answer() == 42\n"
                    ">>>>>>> REPLACE\n"
                )

        old_router = orchestrator_module.ReliabilityModelRouter
        old_engine = orchestrator_module.LocalEngine
        orchestrator_module.ReliabilityModelRouter = DeterministicRouter
        orchestrator_module.LocalEngine = DeterministicEngine
        try:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
                subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
                os.makedirs(os.path.join(tmp, "tests"))
                write_text(os.path.join(tmp, "app.py"), "def answer():\n    return 1\n")
                write_text(
                    os.path.join(tmp, "tests", "test_app.py"),
                    "from app import answer\n\n\n"
                    "def test_answer():\n"
                    "    assert answer() == 1\n",
                )
                subprocess.run(["git", "add", "app.py", "tests/test_app.py"], cwd=tmp, check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)
                disposable_before = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip()

                orchestrator = orchestrator_module.ReliabilityOrchestrator(
                    AllowAllGate(), repo_root=tmp, assume_yes=True
                )
                result = orchestrator.run("Update app.py answer and tests/test_app.py so answer returns 42 with tests")

                self.assertEqual(result["status"], "done")
                self.assertEqual(result["subtasks"], [{"id": 1, "status": "done"}])
                self.assertEqual(read_text(os.path.join(tmp, "app.py")), "def answer():\n    return 42\n")
                self.assertIn("assert answer() == 42", read_text(os.path.join(tmp, "tests", "test_app.py")))
                subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=tmp, check=True, capture_output=True)
                disposable_after = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip()
                self.assertEqual(disposable_before, disposable_after)
                self.assertIn(" M app.py", subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=tmp,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout)
                worktrees = subprocess.run(
                    ["git", "worktree", "list", "--porcelain"],
                    cwd=tmp,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout
                self.assertNotIn(".reliability_worktrees/subtask-", worktrees)
                state_path = os.path.join(result["state_workspace"], state_store.PROJECT_STATE)
                self.assertTrue(os.path.isfile(state_path))
                self.assertEqual(state_store.list_subtasks()[0]["status"], "done")
        finally:
            orchestrator_module.ReliabilityModelRouter = old_router
            orchestrator_module.LocalEngine = old_engine

        autocorp_after = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=autocorp_root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(autocorp_before, autocorp_after)


class SimpleEngine:
    model = "simple"

    def __init__(self, diff):
        self.outputs = list(diff) if isinstance(diff, list) else [diff]
        self.calls = []

    def generate(self, prompt, system=""):
        self.calls.append(prompt)
        if len(self.outputs) > 1:
            return self.outputs.pop(0)
        return self.outputs[0]


class SimpleTester:
    def test(self, workspace, plan):
        class Result:
            ok = read_text(os.path.join(workspace, "x.py")) == "value = 2\n"
            blocked = False
            output = "" if ok else "value was not 2"
        return Result()


class TwoLineTester:
    def test(self, workspace, plan):
        class Result:
            ok = read_text(os.path.join(workspace, "x.py")) == "value = 2\nkeep = 3\n"
            blocked = False
            output = "" if ok else "value was not updated"
        return Result()


class SequenceTester:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def test(self, workspace, plan):
        ok = self.outcomes.pop(0) if self.outcomes else False

        class Result:
            blocked = False
            output = "" if ok else "forced failure"

        Result.ok = ok
        return Result()


class TestLoops(TempDBTest):
    def test_test_loop_applies_patch_and_runs_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
            subtask_id = state_store.create_subtask("fix x", ["x.py"])
            loop = ReliabilityTestLoop(
                SimpleTester(), SimpleEngine(diff), max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id, "description": "fix x", "files_touched": ["x.py"],
            }, [])
            self.assertTrue(result.ok, result.error)

    def test_format_reprompt_does_not_burn_extra_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            bad = "Here is the patch:\n```diff\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n```\n"
            good = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
            engine = SimpleEngine([bad, good])
            subtask_id = state_store.create_subtask("fix x", ["x.py"])
            loop = ReliabilityTestLoop(
                SimpleTester(), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id, "description": "fix x", "files_touched": ["x.py"],
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.attempts, 1)
            self.assertEqual(state_store.list_subtasks()[0]["attempts"], 1)
            self.assertEqual(len(engine.calls), 2)
            self.assertIn("Your last output was not a raw unified diff", engine.calls[1])
            with store._connect() as conn:
                attempt = conn.execute(
                    "SELECT raw_output, diff FROM attempts WHERE subtask_id = ?",
                    (subtask_id,),
                ).fetchone()
            self.assertEqual(attempt["raw_output"], good)
            self.assertEqual(attempt["diff"], good)

    def test_ungrounded_diff_gets_one_repair_without_burning_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            bad = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n fictional = 0\n-value = 1\n+value = 2\n"
            good = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-value = 1\n+value = 2\n keep = 3\n"
            engine = SimpleEngine([bad, good])
            subtask_id = state_store.create_subtask("fix x", ["x.py"])
            calls = []
            original_apply = patch_apply.apply_diff

            def counting_apply(workspace, diff):
                calls.append(diff)
                return original_apply(workspace, diff)

            patch_apply.apply_diff = counting_apply
            try:
                loop = ReliabilityTestLoop(
                    TwoLineTester(), engine, max_attempts=1,
                    static_gate=StaticGate(require_mypy=False, require_lint=False),
                )
                result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                    "id": subtask_id, "description": "fix x", "files_touched": ["x.py"],
                }, [])
            finally:
                patch_apply.apply_diff = original_apply
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.attempts, 1)
            self.assertEqual(state_store.list_subtasks()[0]["attempts"], 1)
            self.assertEqual(len(engine.calls), 2)
            self.assertIn("does not match the real file", engine.calls[1])
            self.assertEqual(calls, [good])

    def test_grounded_diff_proceeds_to_git_apply_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            good = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-value = 1\n+value = 2\n keep = 3\n"
            engine = SimpleEngine(good)
            subtask_id = state_store.create_subtask("fix x", ["x.py"])
            loop = ReliabilityTestLoop(
                TwoLineTester(), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id, "description": "fix x", "files_touched": ["x.py"],
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(len(engine.calls), 1)

    def test_edit_block_mismatch_gets_one_repair_without_burning_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            bad = "<<<<<<< FIND\nfictional = 0\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
            good = "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
            engine = SimpleEngine([bad, good])
            subtask_id = state_store.create_subtask("fix x", ["x.py"])
            loop = ReliabilityTestLoop(
                TwoLineTester(), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id, "description": "fix x", "files_touched": ["x.py"], "edit_mode": True,
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.attempts, 1)
            self.assertEqual(state_store.list_subtasks()[0]["attempts"], 1)
            self.assertEqual(len(engine.calls), 2)
            self.assertIn("FIND block does not match the real file", engine.calls[1])
            self.assertEqual(read_text(os.path.join(tmp, "x.py")), "value = 2\nkeep = 3\n")

    def test_edit_block_matching_proceeds_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            good = "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
            engine = SimpleEngine(good)
            subtask_id = state_store.create_subtask("fix x", ["x.py"])
            loop = ReliabilityTestLoop(
                TwoLineTester(), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id, "description": "fix x", "files_touched": ["x.py"], "edit_mode": True,
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(len(engine.calls), 1)

    def test_requested_identifier_extraction(self):
        self.assertEqual(
            requested_identifier("Add a function to x.py named count_subtasks"),
            "count_subtasks",
        )
        self.assertEqual(requested_identifier("Add a helper for subtasks"), "")

    def test_test_coverage_expectation_extraction(self):
        self.assertTrue(test_coverage_expected("Add count_subtasks with tests"))
        self.assertTrue(test_coverage_expected("add or update a real test for it"))
        self.assertTrue(test_coverage_expected("write a test for the helper"))
        self.assertFalse(test_coverage_expected("Add a helper for subtasks"))

    def test_edit_block_with_requested_identifier_passes_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            good = (
                "<<<<<<< FIND\n"
                "value = 1\n"
                "=======\n"
                "def count_subtasks():\n"
                "    return 1\n"
                ">>>>>>> REPLACE\n"
            )
            engine = SimpleEngine(good)
            subtask_id = state_store.create_subtask("add a function named count_subtasks", ["x.py"])
            loop = ReliabilityTestLoop(
                SimpleTester(), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id,
                "description": "add a function named count_subtasks",
                "files_touched": ["x.py"],
                "edit_mode": True,
            }, [])
            self.assertFalse(result.ok)
            self.assertEqual(len(engine.calls), 1)
            self.assertIn("count_subtasks", read_text(os.path.join(tmp, "x.py")))

    def test_edit_block_without_requested_identifier_gets_one_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            bad = "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
            good = (
                "<<<<<<< FIND\n"
                "value = 1\n"
                "=======\n"
                "def count_subtasks():\n"
                "    return 1\n"
                ">>>>>>> REPLACE\n"
            )
            engine = SimpleEngine([bad, good])
            subtask_id = state_store.create_subtask("add a function named count_subtasks", ["x.py"])
            loop = ReliabilityTestLoop(
                SimpleTester(), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id,
                "description": "add a function named count_subtasks",
                "files_touched": ["x.py"],
                "edit_mode": True,
            }, [])
            self.assertFalse(result.ok)
            self.assertEqual(state_store.list_subtasks()[0]["attempts"], 1)
            self.assertEqual(len(engine.calls), 2)
            self.assertIn("task asked for an identifier named count_subtasks", engine.calls[1])
            self.assertIn("count_subtasks", read_text(os.path.join(tmp, "x.py")))

    def test_edit_block_without_named_identifier_skips_relevance_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\nkeep = 3\n")
            block = "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
            engine = SimpleEngine(block)
            subtask_id = state_store.create_subtask("update the value", ["x.py"])
            loop = ReliabilityTestLoop(
                TwoLineTester(), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id,
                "description": "update the value",
                "files_touched": ["x.py"],
                "edit_mode": True,
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(len(engine.calls), 1)

    def test_request_with_tests_and_test_block_referencing_identifier_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            os.makedirs(os.path.join(tmp, "tests"))
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            write_text(os.path.join(tmp, "tests", "test_x.py"), "def test_old():\n    pass\n")
            subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)
            raw = (
                "<<<<<<< FILE x.py\n"
                "<<<<<<< FIND\n"
                "value = 1\n"
                "=======\n"
                "def count_subtasks():\n"
                "    return 1\n"
                ">>>>>>> REPLACE\n"
                "<<<<<<< FILE tests/test_x.py\n"
                "<<<<<<< FIND\n"
                "def test_old():\n"
                "    pass\n"
                "=======\n"
                "from x import count_subtasks\n"
                "\n"
                "\n"
                "def test_count_subtasks():\n"
                "    assert count_subtasks() == 1\n"
                ">>>>>>> REPLACE\n"
            )
            engine = SimpleEngine(raw)
            subtask_id = state_store.create_subtask(
                "add a function named count_subtasks with tests",
                ["x.py", "tests/test_x.py"],
            )
            loop = ReliabilityTestLoop(
                SequenceTester([True]), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id,
                "description": "add a function named count_subtasks with tests",
                "files_touched": ["x.py", "tests/test_x.py"],
                "edit_mode": True,
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(len(engine.calls), 1)
            self.assertIn("count_subtasks", read_text(os.path.join(tmp, "tests", "test_x.py")))

    def test_request_with_tests_but_no_test_block_gets_one_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            os.makedirs(os.path.join(tmp, "tests"))
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            write_text(os.path.join(tmp, "tests", "test_x.py"), "def test_old():\n    pass\n")
            subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)
            bad = (
                "<<<<<<< FILE x.py\n"
                "<<<<<<< FIND\n"
                "value = 1\n"
                "=======\n"
                "def count_subtasks():\n"
                "    return 1\n"
                ">>>>>>> REPLACE\n"
            )
            good = (
                "<<<<<<< FILE x.py\n"
                "<<<<<<< FIND\n"
                "value = 1\n"
                "=======\n"
                "def count_subtasks():\n"
                "    return 1\n"
                ">>>>>>> REPLACE\n"
                "<<<<<<< FILE tests/test_x.py\n"
                "<<<<<<< FIND\n"
                "def test_old():\n"
                "    pass\n"
                "=======\n"
                "from x import count_subtasks\n"
                "\n"
                "\n"
                "def test_count_subtasks():\n"
                "    assert count_subtasks() == 1\n"
                ">>>>>>> REPLACE\n"
            )
            engine = SimpleEngine([bad, good])
            subtask_id = state_store.create_subtask(
                "add a function named count_subtasks and add a test",
                ["x.py", "tests/test_x.py"],
            )
            loop = ReliabilityTestLoop(
                SequenceTester([True]), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id,
                "description": "add a function named count_subtasks and add a test",
                "files_touched": ["x.py", "tests/test_x.py"],
                "edit_mode": True,
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(state_store.list_subtasks()[0]["attempts"], 1)
            self.assertEqual(len(engine.calls), 2)
            self.assertIn("task asked for a test", engine.calls[1])
            self.assertIn("count_subtasks", read_text(os.path.join(tmp, "tests", "test_x.py")))

    def test_request_without_test_expectation_skips_coverage_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            raw = (
                "<<<<<<< FIND\n"
                "value = 1\n"
                "=======\n"
                "def count_subtasks():\n"
                "    return 1\n"
                ">>>>>>> REPLACE\n"
            )
            engine = SimpleEngine(raw)
            subtask_id = state_store.create_subtask("add a function named count_subtasks", ["x.py"])
            loop = ReliabilityTestLoop(
                SequenceTester([True]), engine, max_attempts=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id,
                "description": "add a function named count_subtasks",
                "files_touched": ["x.py"],
                "edit_mode": True,
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(len(engine.calls), 1)

    def test_failed_edit_attempt_resets_target_before_next_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            subprocess.run(["git", "add", "x.py"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)

            class InspectingEngine:
                model = "inspect"

                def __init__(self):
                    self.calls = 0
                    self.start_contents = []

                def generate(self, prompt, system=""):
                    self.calls += 1
                    self.start_contents.append(read_text(os.path.join(tmp, "x.py")))
                    if self.calls == 1:
                        return "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
                    return "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 3\n>>>>>>> REPLACE\n"

            engine = InspectingEngine()
            subtask_id = state_store.create_subtask("update the value", ["x.py"])
            loop = ReliabilityTestLoop(
                SequenceTester([False, True]), engine, max_attempts=2,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = loop.run(tmp, {"files": [{"path": "x.py"}], "test_command": "unused"}, {
                "id": subtask_id,
                "description": "update the value",
                "files_touched": ["x.py"],
                "edit_mode": True,
            }, [])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(engine.start_contents, ["value = 1\n", "value = 1\n"])
            self.assertEqual(read_text(os.path.join(tmp, "x.py")), "value = 3\n")

    def test_edit_self_consistency_evaluates_clean_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            subprocess.run(["git", "add", "x.py"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)
            starts = []
            candidates = [
                "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n",
                "<<<<<<< FIND\nvalue = 1\n=======\nvalue = 3\n>>>>>>> REPLACE\n",
            ]

            def candidate_generator(index, error_trace):
                starts.append(read_text(os.path.join(tmp, "x.py")))
                return GeneratedCandidate(candidates[index - 1], prompt=f"prompt {index}")

            class ValueThreeTester:
                def test(self, workspace, plan):
                    class Result:
                        ok = read_text(os.path.join(workspace, "x.py")) == "value = 3\n"
                        blocked = False
                        output = "" if ok else "not three"
                    return Result()

            subtask_id = state_store.create_subtask("update the value", ["x.py"])
            runner = SelfConsistencyRunner(
                ValueThreeTester(), SimpleEngine(""), n_samples=2,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            result = runner.choose_edit(
                tmp,
                {"files": [{"path": "x.py"}], "test_command": "unused"},
                {"id": subtask_id, "description": "update the value", "files_touched": ["x.py"]},
                candidate_generator,
                model_used="simple",
            )
            self.assertTrue(result.ok, result.error)
            self.assertEqual(starts, ["value = 1\n", "value = 1\n"])
            self.assertEqual(read_text(os.path.join(tmp, "x.py")), "value = 3\n")
            self.assertEqual(state_store.list_subtasks()[0]["attempts"], 2)
            with store._connect() as conn:
                prompts = [
                    row["prompt"] for row in conn.execute("SELECT prompt FROM attempts ORDER BY attempt_num")
                ]
            self.assertEqual(prompts, ["prompt 1", "prompt 2"])

    def test_self_consistency_accepts_first_passing_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            write_text(os.path.join(tmp, "x.py"), "value = 1\n")
            diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
            runner = SelfConsistencyRunner(
                SimpleTester(), SimpleEngine(diff), n_samples=1,
                static_gate=StaticGate(require_mypy=False, require_lint=False),
            )
            self.assertTrue(runner.should_run({"blast_radius": 5}))
            result = runner.choose(tmp, {"files": [{"path": "x.py"}]}, ["prompt"])
            self.assertTrue(result.ok, result.error)

    def test_self_consistency_choose_does_not_block_on_missing_static_tools(self):
        """Regression test: choose() (the greenfield-mode path) used to call
        StaticGate.run() - the absolute mode - which treats a missing
        ruff/flake8/mypy binary as a real, blocking issue. Unlike
        choose_edit() (already correct), this meant every candidate would be
        rejected in any environment lacking those tools, silently defeating
        self-consistency voting for exactly the high-blast-radius/core-
        touching changes it exists to protect. Fixed to use collect_issues +
        run_delta, matching choose_edit()'s pattern."""
        from reliability_engine import static_gate as sg_module

        original_tool_path = sg_module._tool_path
        sg_module._tool_path = lambda name: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
                write_text(os.path.join(tmp, "x.py"), "value = 1\n")
                diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
                runner = SelfConsistencyRunner(
                    SimpleTester(), SimpleEngine(diff), n_samples=1,
                    static_gate=StaticGate(require_mypy=True, require_lint=True),
                )
                result = runner.choose(tmp, {"files": [{"path": "x.py"}]}, ["prompt"])
                self.assertTrue(result.ok, result.error)
        finally:
            sg_module._tool_path = original_tool_path


class TestOrchestratorConstruction(unittest.TestCase):
    def test_orchestrator_constructs_with_existing_gate(self):
        from reliability_engine.orchestrator import ReliabilityOrchestrator
        orchestrator = ReliabilityOrchestrator(AllowAllGate(), repo_root=os.getcwd(), assume_yes=True)
        self.assertIsInstance(orchestrator.sandbox, WorktreeSandbox)

    def test_edit_plan_uses_current_interpreter_for_tests(self):
        from reliability_engine.orchestrator import ReliabilityOrchestrator

        orchestrator = ReliabilityOrchestrator(AllowAllGate(), repo_root=os.getcwd(), assume_yes=True)
        command = orchestrator._edit_plan(
            "Add count_subtasks to memory/store.py",
            ["memory/store.py"],
        )["test_command"]
        self.assertIn(shlex.quote(sys.executable), command)
        self.assertNotEqual(command.split()[0], "python")
        self.assertNotEqual(command.split()[0], "python3")

    def test_orchestrator_adds_existing_test_target_when_tests_requested(self):
        from reliability_engine.orchestrator import ReliabilityOrchestrator

        orchestrator = ReliabilityOrchestrator(AllowAllGate(), repo_root=os.getcwd(), assume_yes=True)
        targets = ["memory/store.py"]
        self.assertEqual(orchestrator._test_targets_for(targets), ["tests/test_memory_store.py"])


if __name__ == "__main__":
    unittest.main()
