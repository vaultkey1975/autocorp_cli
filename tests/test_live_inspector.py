import argparse
import json
import os
import sqlite3
import subprocess
import textwrap

import autocorp
from brains import chat, live_inspector, manager


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def _fake_fastapi(repo):
    _write(
        repo / "fastapi" / "__init__.py",
        r'''
        import json

        class FastAPI:
            def __init__(self):
                self.routes = {}

            def get(self, path):
                def decorator(func):
                    self.routes[path] = func
                    return func
                return decorator

            async def __call__(self, scope, receive, send):
                if scope.get("type") != "http":
                    return
                path = scope.get("path", "/")
                if path == "/openapi.json":
                    body = json.dumps({
                        "openapi": "3.0.0",
                        "info": {"title": "Fake API", "version": "1"},
                        "paths": {route: {"get": {"operationId": route.strip("/") or "root"}} for route in self.routes},
                    }).encode()
                    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                if path == "/docs":
                    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html")]})
                    await send({"type": "http.response.body", "body": b"<html>docs</html>"})
                    return
                func = self.routes.get(path)
                if func is None:
                    await send({"type": "http.response.start", "status": 404, "headers": []})
                    await send({"type": "http.response.body", "body": b"not found"})
                    return
                data = func()
                body = json.dumps(data).encode()
                await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
        ''',
    )


def _fastapi_repo(repo, app_source):
    _fake_fastapi(repo)
    _write(repo / "requirements.txt", "uvicorn\npytest\nruff\nmypy\nsqlite-utils\n")
    _write(repo / "app.py", app_source)
    _write(repo / "README.md", "# app\n")
    _init_repo(repo)


def _fake_flask(repo):
    _write(
        repo / "flask" / "__init__.py",
        r'''
        class Flask:
            def __init__(self, name):
                self.routes = {}

            def route(self, path):
                def decorator(func):
                    self.routes[path] = func
                    return func
                return decorator

            def run(self, host="127.0.0.1", port=5000):
                from http.server import BaseHTTPRequestHandler, HTTPServer
                routes = self.routes

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        func = routes.get(self.path)
                        if func is None:
                            self.send_response(404)
                            self.end_headers()
                            return
                        body = str(func()).encode()
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(body)

                    def log_message(self, fmt, *args):
                        pass

                HTTPServer((host, int(port)), Handler).serve_forever()
        ''',
    )
    _write(
        repo / "flask" / "__main__.py",
        r'''
        import argparse
        import importlib

        parser = argparse.ArgumentParser()
        parser.add_argument("--app", required=True)
        sub = parser.add_subparsers(dest="command")
        run = sub.add_parser("run")
        run.add_argument("--host", default="127.0.0.1")
        run.add_argument("--port", default="5000")
        ns = parser.parse_args()
        module_name, _, obj_name = ns.app.partition(":")
        module = importlib.import_module(module_name)
        getattr(module, obj_name).run(host=ns.host, port=ns.port)
        ''',
    )


def test_inspector_launches_fastapi_and_reads_openapi(tmp_path):
    _fastapi_repo(
        tmp_path,
        """
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/")
        def root():
            return {"ok": True}

        @app.get("/health")
        def health():
            return {"status": "ok"}
        """,
    )

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert report.application_launches is True
    assert report.launch_status == "APPLICATION_RESPONDING"
    assert "/" in report.routes_discovered
    assert "/health" in report.routes_discovered
    assert report.running_application == "PASS"
    assert report.cleanup_verified is True
    assert not os.path.exists(report.disposable_root)


def test_inspector_launches_fastapi_factory_not_local_app_variable(tmp_path):
    _fastapi_repo(
        tmp_path,
        """
        from fastapi import FastAPI

        def create_app():
            app = FastAPI()

            @app.get("/health")
            def health():
                return {"status": "ok"}

            return app
        """,
    )

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert report.application_launches is True
    assert report.selected_entry_point.target == "app:create_app"
    assert "--factory" in report.selected_entry_point.command
    assert "/health" in report.routes_discovered


def test_inspector_launches_flask_style_application(tmp_path):
    _fake_flask(tmp_path)
    _write(tmp_path / "requirements.txt", "flask\npytest\n")
    _write(
        tmp_path / "app.py",
        """
        from flask import Flask
        app = Flask(__name__)

        @app.route("/")
        def root():
            return "ok"
        """,
    )
    _init_repo(tmp_path)

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert report.application_launches is True
    assert report.selected_entry_point.kind == "flask"
    assert any(result.path == "/" and result.status == "PASS" for result in report.endpoint_results)


def test_inspector_runs_cli_help_from_disposable_copy(tmp_path):
    _write(
        tmp_path / "main.py",
        """
        import argparse

        def main():
            argparse.ArgumentParser().parse_args()
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        """,
    )
    _write(tmp_path / "README.md", "# cli\n")
    _init_repo(tmp_path)

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert report.application_launches is True
    assert report.launch_status == "CLI_STARTED"
    assert report.selected_entry_point.kind == "cli"


def test_inspector_reports_broken_startup(tmp_path):
    _fastapi_repo(
        tmp_path,
        """
        raise RuntimeError("boom during import")
        from fastapi import FastAPI
        app = FastAPI()
        """,
    )

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert report.application_launches is False
    assert report.launch_status == "PROCESS_EXITED_EARLY"
    assert "boom during import" in report.startup_exception
    assert report.highest_value_next_task.startswith("Fix application startup")


def test_inspector_reports_missing_dependency(tmp_path):
    _write(tmp_path / "requirements.txt", "definitely-missing-package\n")
    _write(
        tmp_path / "app.py",
        """
        import definitely_missing_package
        from fastapi import FastAPI
        app = FastAPI()
        """,
    )
    _init_repo(tmp_path)

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert report.application_launches is False
    assert "ModuleNotFoundError" in report.startup_exception


def test_inspector_reports_disposable_copy_failure(monkeypatch, tmp_path):
    _write(
        tmp_path / "main.py",
        """
        if __name__ == "__main__":
            raise SystemExit(0)
        """,
    )
    _init_repo(tmp_path)

    def fail_copy(src, dst):
        raise OSError("copy exploded")

    monkeypatch.setattr(live_inspector, "_copy_disposable", fail_copy)

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert report.launch_status == "DISPOSABLE_COPY_FAILED"
    assert "copy exploded" in report.startup_exception
    assert report.cleanup_verified is True


def test_inspector_reports_database_failure(tmp_path):
    _write(tmp_path / "app.py", "print('x')\n")
    (tmp_path / "broken.sqlite").write_bytes(b"not a sqlite database")
    _init_repo(tmp_path)

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert any(db.path == "broken.sqlite" and db.status == "FAIL" for db in report.database_status)
    assert any("Database broken.sqlite" in item for item in report.configuration_problems)


def test_inspector_reports_missing_migrations(tmp_path):
    _write(tmp_path / "app.py", "print('x')\n")
    conn = sqlite3.connect(tmp_path / "app.sqlite")
    conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY)")
    conn.close()
    _init_repo(tmp_path)

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert any(db.path == "app.sqlite" and db.migrations == "MISSING" for db in report.database_status)
    assert any("migrations not found" in item for item in report.configuration_problems)


def test_inspector_database_foreign_key_failure_is_high_risk(tmp_path):
    _write(tmp_path / "app.py", "print('x')\n")
    conn = sqlite3.connect(tmp_path / "app.sqlite")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))")
    conn.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")
    conn.commit()
    conn.close()
    (tmp_path / "migrations").mkdir()
    _init_repo(tmp_path)

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert any(db.path == "app.sqlite" and db.status == "FAIL" for db in report.database_status)
    assert any("foreign_keys=FAIL" in item for item in report.configuration_problems)
    assert any("Database app.sqlite" in item for item in report.highest_risk_failures)


def test_inspector_reports_404_and_500_endpoint_results(tmp_path):
    _fastapi_repo(
        tmp_path,
        """
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/fail")
        def fail():
            raise RuntimeError("route exploded")
        """,
    )

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert any(result.path == "/" and result.status_code == 404 for result in report.endpoint_results)
    assert any(result.path == "/fail" and result.status in {"FAIL", "ERROR"} for result in report.endpoint_results)
    assert report.running_application == "PARTIAL"


def test_inspector_reports_clonecast_unknown_and_not_configured_features(tmp_path):
    repo = tmp_path / "clonecast"
    repo.mkdir()
    _fastapi_repo(
        repo,
        """
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/api/episodes/{episode_id}")
        def episodes():
            return {"items": []}
        """,
    )

    report = live_inspector.inspect_application(str(repo), timeout=5)

    statuses = {feature.name: feature.status for feature in report.feature_status}
    assert statuses["Create Episode"] == "UNKNOWN"
    assert statuses["YouTube Publishing"] == "NOT CONFIGURED"


def test_inspector_detects_console_script_entry_point(tmp_path):
    _write(
        tmp_path / "pyproject.toml",
        """
        [project]
        name = "console-app"

        [project.scripts]
        console-app = "pkg.cli:main"
        """,
    )
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(
        tmp_path / "pkg" / "cli.py",
        """
        def main():
            return 0
        """,
    )
    _init_repo(tmp_path)

    report = live_inspector.inspect_application(str(tmp_path), timeout=5)

    assert any(entry.kind == "console_script" and entry.target == "pkg.cli:main" for entry in report.entry_points)
    assert report.application_launches is True


def test_inspect_cli_registers_json_and_full_flags():
    parser = autocorp.build_parser()
    args = parser.parse_args(["inspect", "--repo", "/tmp/example", "--json", "--full"])

    assert args.func is autocorp.cmd_inspect
    assert args.json is True
    assert args.full is True


def test_cmd_inspect_json_outputs_machine_readable_json(monkeypatch, capsys, tmp_path):
    _write(
        tmp_path / "main.py",
        """
        if __name__ == "__main__":
            raise SystemExit(0)
        """,
    )
    _init_repo(tmp_path)
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args, quiet=False: str(tmp_path))

    rc = autocorp.cmd_inspect(argparse.Namespace(repo=str(tmp_path), json=True, full=False, timeout=5, port=0))

    parsed = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert parsed["repo_path"] == str(tmp_path)


def test_manager_prioritizes_live_runtime_failure(tmp_path):
    _fastapi_repo(
        tmp_path,
        """
        raise RuntimeError("startup broke")
        from fastapi import FastAPI
        app = FastAPI()
        """,
    )

    report = manager.run_manager(str(tmp_path))

    assert report.next_task is not None
    assert report.next_task.category == "runtime"
    assert report.next_task.title == "Application startup fails"


def test_chat_routes_what_actually_works_to_live_inspector(monkeypatch, tmp_path):
    fake = live_inspector.LiveInspectionReport(
        repo_path=str(tmp_path),
        application_launches=True,
        launch_status="CLI_STARTED",
        running_application="STARTED",
    )
    monkeypatch.setattr(chat.live_inspector, "inspect_application", lambda repo_root, timeout=5: fake)

    session = chat.AutoCorpChatSession(str(tmp_path))
    response = session.handle("what actually works?")

    assert response.intent == "live_inspection"
    assert "Live Application Inspector" in response.text
