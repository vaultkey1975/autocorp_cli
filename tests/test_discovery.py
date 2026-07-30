import json
import subprocess

import autocorp
from brains import chat, discovery, manager
from memory import store


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit_all(path):
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _values(findings):
    return {finding.value for finding in findings}


def test_discovery_profiles_python_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "data" / "test.db"))
    repo = tmp_path / "python_app"
    repo.mkdir()
    _init_repo(repo)
    (repo / "requirements.txt").write_text("fastapi\npytest\nruff\nmypy\nsqlite-utils\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (repo / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (repo / "README.md").write_text("# Python App\n", encoding="utf-8")
    (repo / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _commit_all(repo)

    profile = discovery.discover_repository(str(repo), store_profile=True)

    assert "Python" in _values(profile.languages)
    assert "FastAPI" in _values(profile.frameworks)
    assert "pip" in _values(profile.package_managers)
    assert "pytest" in _values(profile.test_frameworks)
    assert "ruff" in _values(profile.lint_tools)
    assert "mypy" in _values(profile.type_checkers)
    assert "SQLite" in _values(profile.database_technology)
    assert profile.preferred_test_command == ".venv/bin/python -m pytest"
    assert store.latest_repository_profile(str(repo))["repo_name"] == "python_app"


def test_discovery_profiles_node_repository(tmp_path):
    repo = tmp_path / "node_app"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps({
            "scripts": {"build": "vite build", "test": "jest", "lint": "eslint ."},
            "dependencies": {"react": "^18.0.0", "express": "^4.0.0"},
            "devDependencies": {"eslint": "^8.0.0", "prettier": "^3.0.0", "typescript": "^5.0.0", "jest": "^29.0.0"},
        }),
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    (repo / "src" / "index.tsx").write_text("import React from 'react'\n", encoding="utf-8")
    (repo / "README.md").write_text("# Node App\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert "TypeScript" in _values(profile.languages)
    assert {"React", "Express"} <= _values(profile.frameworks)
    assert "npm" in _values(profile.package_managers)
    assert "Jest" in _values(profile.test_frameworks)
    assert "ESLint" in _values(profile.lint_tools)
    assert "Prettier" in _values(profile.formatters)
    assert profile.preferred_build_command == "npm run build"


def test_discovery_profiles_rust_repository(tmp_path):
    repo = tmp_path / "rust_app"
    repo.mkdir()
    (repo / "Cargo.toml").write_text("[package]\nname = \"rust_app\"\n[dependencies]\nbevy = \"0.13\"\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert "Rust" in _values(profile.languages)
    assert "Cargo" in _values(profile.package_managers)
    assert "Bevy" in _values(profile.frameworks)
    assert profile.preferred_build_command == "cargo build"


def test_discovery_profiles_go_repository(tmp_path):
    repo = tmp_path / "go_app"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/go_app\n", encoding="utf-8")
    (repo / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (repo / "main_test.go").write_text("package main\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert "Go" in _values(profile.languages)
    assert "Go modules" in _values(profile.package_managers)
    assert "Go test" in _values(profile.test_frameworks)
    assert profile.preferred_test_command == "go test ./..."


def test_discovery_profiles_java_repository(tmp_path):
    repo = tmp_path / "java_app"
    repo.mkdir()
    (repo / "pom.xml").write_text(
        """
        <project>
          <dependencies>
            <dependency><artifactId>spring-boot-starter-web</artifactId></dependency>
            <dependency><artifactId>junit-jupiter</artifactId></dependency>
          </dependencies>
        </project>
        """,
        encoding="utf-8",
    )
    src = repo / "src" / "main" / "java"
    src.mkdir(parents=True)
    (src / "App.java").write_text("class App {}\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert "Java" in _values(profile.languages)
    assert "Maven" in _values(profile.package_managers)
    assert "Spring" in _values(profile.frameworks)
    assert "JUnit" in _values(profile.test_frameworks)
    assert profile.preferred_build_command == "mvn package"
    assert profile.preferred_test_command == "mvn test"


def test_discovery_profiles_dotnet_repository(tmp_path):
    repo = tmp_path / "dotnet_app"
    repo.mkdir()
    (repo / "dotnet_app.csproj").write_text(
        """
        <Project Sdk="Microsoft.NET.Sdk.Web">
          <ItemGroup>
            <PackageReference Include="Microsoft.AspNetCore.App" Version="8.0.0" />
            <PackageReference Include="xunit" Version="2.6.0" />
          </ItemGroup>
        </Project>
        """,
        encoding="utf-8",
    )
    (repo / "Program.cs").write_text("var builder = WebApplication.CreateBuilder(args);\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert "C#" in _values(profile.languages)
    assert ".NET/NuGet" in _values(profile.package_managers)
    assert ".NET SDK" in _values(profile.build_system)
    assert "ASP.NET" in _values(profile.frameworks)
    assert "xUnit" in _values(profile.test_frameworks)
    assert profile.preferred_build_command == "dotnet build"
    assert profile.preferred_test_command == "dotnet test"


def test_discovery_profiles_cpp_repository(tmp_path):
    repo = tmp_path / "cpp_app"
    repo.mkdir()
    (repo / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\nproject(cpp_app)\nadd_executable(cpp_app src/main.cpp)\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert "C++" in _values(profile.languages)
    assert "CMake" in _values(profile.build_system)
    assert profile.application_type.value == "Native application/library"
    assert profile.preferred_build_command == "cmake --build build"


def test_discovery_ignores_generated_workspace_artifacts(tmp_path):
    repo = tmp_path / "workspace_noise"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "app.py").write_text("print('real')\n", encoding="utf-8")
    workspace = repo / "workspace" / "generated_node"
    workspace.mkdir(parents=True)
    (workspace / "package.json").write_text('{"dependencies":{"react":"latest"}}', encoding="utf-8")
    (workspace / "app.tsx").write_text("export const App = () => null\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert "Python" in _values(profile.languages)
    assert "TypeScript" not in _values(profile.languages)
    assert "npm" not in _values(profile.package_managers)
    assert "React" not in _values(profile.frameworks)


def test_discovery_profiles_mixed_language_repository(tmp_path):
    repo = tmp_path / "mixed"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "package.json").write_text('{"scripts":{"test":"jest"}}', encoding="utf-8")
    (repo / "app.py").write_text("print('x')\n", encoding="utf-8")
    (repo / "web.ts").write_text("export const x = 1\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert {"Python", "TypeScript"} <= _values(profile.languages)
    assert {"pip", "npm"} <= _values(profile.package_managers)


def test_discovery_minimal_repository_reports_unknowns(tmp_path):
    repo = tmp_path / "minimal"
    repo.mkdir()
    (repo / "notes.txt").write_text("hello\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert profile.repository_type.value == "Unknown"
    assert any("Primary language: Unknown" in item for item in profile.unknown_areas)
    assert any("Package manager: Unknown" in item for item in profile.unknown_areas)
    assert profile.confidence < 50


def test_discovery_repository_with_no_readme_reports_documentation_gap(tmp_path):
    repo = tmp_path / "no_readme"
    repo.mkdir()
    (repo / "app.py").write_text("print('x')\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert profile.documentation[0].value == "Not enough evidence"
    assert any("Documentation quality" in item for item in profile.unknown_areas)


def test_discovery_repository_with_no_tests_reports_testing_gap(tmp_path):
    repo = tmp_path / "no_tests"
    repo.mkdir()
    (repo / "app.py").write_text("print('x')\n", encoding="utf-8")
    (repo / "README.md").write_text("# app\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert not profile.test_frameworks
    assert any("Testing: Unknown" in item for item in profile.unknown_areas)
    assert "No test framework detected." in profile.known_risks


def test_discovery_conflicting_evidence_reports_mixed_profile(tmp_path):
    repo = tmp_path / "conflict"
    repo.mkdir()
    (repo / "package.json").write_text('{"dependencies":{"react":"latest"}}', encoding="utf-8")
    (repo / "Cargo.toml").write_text("[package]\nname = \"conflict\"\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (repo / "src" / "app.tsx").write_text("export const App = () => null\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert {"Rust", "TypeScript"} <= _values(profile.languages)
    assert {"Cargo", "npm"} <= _values(profile.package_managers)
    assert profile.confidence >= 50


def test_discovery_detects_ai_engineering_docs(tmp_path):
    repo = tmp_path / "with_ai_docs"
    repo.mkdir()
    docs = repo / "AI_ENGINEERING"
    docs.mkdir()
    (docs / "CURRENT_PHASE.md").write_text("# Current Phase\nDiscovery\n", encoding="utf-8")

    profile = discovery.discover_repository(str(repo), store_profile=False)

    assert "AI_ENGINEERING present" in _values(profile.documentation)
    assert profile.preferred_documentation == "AI_ENGINEERING/"


def test_discover_cli_registers_json_and_full_flags():
    parser = autocorp.build_parser()
    args = parser.parse_args(["discover", "--repo", "/tmp/example", "--json"])

    assert args.func is autocorp.cmd_discover
    assert args.json is True

    args = parser.parse_args(["discover", "--repo", "/tmp/example", "--full"])
    assert args.full is True


def test_cmd_discover_json_outputs_machine_readable_json(monkeypatch, capsys, tmp_path):
    repo = tmp_path / "json_repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "app.py").write_text("print('x')\n", encoding="utf-8")
    _commit_all(repo)
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "data" / "test.db"))

    rc = autocorp.cmd_discover(type("Args", (), {"repo": str(repo), "json": True, "full": False})())

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert rc == 0
    assert parsed["repo_name"] == "json_repo"


def test_manager_auto_discovers_unseen_repository(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "data" / "test.db"))
    repo = tmp_path / "managed"
    repo.mkdir()
    _init_repo(repo)
    (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "app.py").write_text("print('x')\n", encoding="utf-8")
    _commit_all(repo)

    assert store.latest_repository_profile(str(repo)) is None

    report = manager.run_manager(str(repo))

    assert report.discovery_source == "Auto-discovered during manager run"
    assert report.discovery_profile is not None
    assert store.latest_repository_profile(str(repo))["repo_path"] == str(repo)


def test_chat_routes_discovery_profile_requests(monkeypatch, tmp_path):
    profile = discovery.RepositoryProfile(
        repo_path=str(tmp_path),
        repo_name="repo",
        repository_type=discovery.DiscoveryFinding("CLI", ("autocorp.py",), 80),
        languages=(discovery.DiscoveryFinding("Python", ("app.py",), 90),),
        frameworks=(discovery.DiscoveryFinding("FastAPI", ("requirements.txt",), 80),),
        build_system=(discovery.DiscoveryFinding("pip", ("requirements.txt",), 80),),
        test_frameworks=(discovery.DiscoveryFinding("pytest", ("pytest.ini",), 80),),
        deployment=(discovery.DiscoveryFinding("Docker", ("Dockerfile",), 80),),
        architecture=discovery.DiscoveryFinding("CLI application", ("autocorp.py",), 80),
        engineering_maturity=discovery.DiscoveryFinding("Medium", ("tests detected",), 65),
    )
    monkeypatch.setattr(chat.discovery, "discover_repository", lambda repo_root, store_profile=True: profile)

    session = chat.AutoCorpChatSession(str(tmp_path))

    assert session.handle("show repository profile").intent == "repository_profile"
    assert "Python" in session.handle("show languages").text
    assert "FastAPI" in session.handle("show frameworks").text
    assert "pip" in session.handle("show build system").text
    assert "pytest" in session.handle("show testing").text
    assert "Docker" in session.handle("show deployment").text
    assert "Medium" in session.handle("show engineering maturity").text
