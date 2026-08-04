"""Read-only repository discovery for the Fast Pytest Engine.

Inspects a target repository for pytest configuration, a strict "full
verification" command, a compile command, test roots, dependency/migration
files, and GPU/DB/network signal keywords. Never writes to the target
repository. An optional `.autocorp/test-profile.json` can override any
discovered value.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from brains import repo_policy

_PROFILE_REL = os.path.join(".autocorp", "test-profile.json")

_GPU_KEYWORDS = (
    "cuda", "torch.cuda", "chatterbox", "gpu", "nvidia", "ollama",
    "video_lab", "video model", "diffusers", "xformers",
)
_DB_KEYWORDS = (
    "sqlite3", ".db\"", ".db'", "postgres", "psycopg2", "mysql",
    "database", "migrations", "sqlalchemy",
)
_NETWORK_KEYWORDS = (
    "requests.get", "requests.post", "http://", "https://", "urllib",
    "socket.socket", "aiohttp", "websocket",
)

_IGNORE_DIRS = set(repo_policy.GENERATED_DIRS) | {"htmlcov", ".coverage"}


@dataclass
class RepoTestConfig:
    repo_path: str
    venv_python: str
    python_version: str
    pytest_version: str | None
    config_files: dict[str, str] = field(default_factory=dict)
    test_roots: list[str] = field(default_factory=list)
    conftest_paths: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)
    dependency_files: list[str] = field(default_factory=list)
    migration_dirs: list[str] = field(default_factory=list)
    strict_full_command: list[str] | None = None
    strict_full_command_confidence: str = "none"
    strict_full_command_evidence: list[str] = field(default_factory=list)
    compile_command: list[str] = field(default_factory=list)
    gpu_keyword_files: list[str] = field(default_factory=list)
    database_keyword_files: list[str] = field(default_factory=list)
    network_keyword_files: list[str] = field(default_factory=list)
    has_xdist: bool = False
    profile: dict[str, Any] = field(default_factory=dict)
    config_fingerprint_inputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "venv_python": self.venv_python,
            "python_version": self.python_version,
            "pytest_version": self.pytest_version,
            "config_files": self.config_files,
            "test_roots": self.test_roots,
            "conftest_paths": self.conftest_paths,
            "markers": self.markers,
            "dependency_files": self.dependency_files,
            "migration_dirs": self.migration_dirs,
            "strict_full_command": self.strict_full_command,
            "strict_full_command_confidence": self.strict_full_command_confidence,
            "strict_full_command_evidence": self.strict_full_command_evidence,
            "compile_command": self.compile_command,
            "gpu_keyword_files": self.gpu_keyword_files,
            "database_keyword_files": self.database_keyword_files,
            "network_keyword_files": self.network_keyword_files,
            "has_xdist": self.has_xdist,
            "profile_loaded": bool(self.profile),
        }


def load_test_profile(repo_path: str) -> dict[str, Any]:
    path = os.path.join(repo_path, _PROFILE_REL)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


_ALLOWED_DOT_DIRS = {".github", ".autocorp"}


def _walk_files(repo_path: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d for d in dirs
            if d not in _IGNORE_DIRS
            and (d in _ALLOWED_DOT_DIRS or not d.startswith("."))
            and not repo_policy.is_excluded(repo_path, os.path.join(root, d))
        ]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), repo_path).replace(os.sep, "/")
            if repo_policy.is_excluded(repo_path, rel):
                continue
            out.append(rel)
    return sorted(out)


def _venv_python(repo_path: str) -> str:
    for candidate in (
        os.path.join(repo_path, ".venv", "bin", "python"),
        os.path.join(repo_path, "venv", "bin", "python"),
        os.path.join(repo_path, ".venv", "Scripts", "python.exe"),
        os.path.join(repo_path, "venv", "Scripts", "python.exe"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def _read(repo_path: str, rel: str) -> str:
    try:
        with open(os.path.join(repo_path, rel), encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def _pytest_version(python_bin: str) -> str | None:
    try:
        proc = subprocess.run(
            [python_bin, "-m", "pytest", "--version"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"pytest\s+([\d.]+)", text)
    return match.group(1) if match else None


def _find_config_files(files: list[str]) -> dict[str, str]:
    names = {
        "pyproject.toml": "pyproject.toml",
        "pytest.ini": "pytest.ini",
        "setup.cfg": "setup.cfg",
        "tox.ini": "tox.ini",
        "noxfile.py": "noxfile.py",
        "Makefile": "Makefile",
        "conftest.py": "conftest.py",
    }
    found = {}
    for rel in files:
        base = os.path.basename(rel)
        if base in names and "/" not in rel:
            found[names[base]] = rel
    return found


def _test_roots(files: list[str]) -> list[str]:
    roots = set()
    for rel in files:
        base = os.path.basename(rel)
        if base.startswith("test_") and base.endswith(".py"):
            roots.add(rel.split("/")[0] if "/" in rel else ".")
        elif rel.endswith("_test.py"):
            roots.add(rel.split("/")[0] if "/" in rel else ".")
    return sorted(roots)


def _markers(repo_path: str, config_files: dict[str, str]) -> list[str]:
    markers = set()
    for key in ("pytest.ini", "setup.cfg", "pyproject.toml"):
        rel = config_files.get(key)
        if not rel:
            continue
        text = _read(repo_path, rel)
        section = re.search(r"markers\s*=\s*(.+?)(?:\n\S|\Z)", text, re.DOTALL)
        if section:
            for line in section.group(1).splitlines():
                name = line.strip().split(":")[0].strip()
                if name and re.match(r"^[a-zA-Z_][\w-]*$", name):
                    markers.add(name)
    return sorted(markers)


def _dependency_files(files: list[str]) -> list[str]:
    wanted = {
        "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
        "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
        "setup.py", "setup.cfg", "uv.lock",
    }
    return sorted(rel for rel in files if os.path.basename(rel) in wanted and "/" not in rel)


def _migration_dirs(files: list[str]) -> list[str]:
    dirs = set()
    for rel in files:
        parts = rel.split("/")
        for idx, part in enumerate(parts[:-1]):
            if part.lower() in ("migrations", "alembic"):
                dirs.add("/".join(parts[: idx + 1]))
    return sorted(dirs)


def _addopts_and_filterwarnings(repo_path: str, config_files: dict[str, str]) -> tuple[str, bool]:
    """Returns (addopts, filterwarnings_is_error) from ini-style pytest config."""
    for key in ("pytest.ini", "setup.cfg", "tox.ini"):
        rel = config_files.get(key)
        if not rel:
            continue
        text = _read(repo_path, rel)
        addopts_match = re.search(r"^\s*addopts\s*=\s*(.+)$", text, re.MULTILINE)
        addopts = addopts_match.group(1).strip() if addopts_match else ""
        strict_warn = bool(re.search(r"filterwarnings\s*=\s*\n?\s*error", text)) or "-W error" in addopts or "-W=error" in addopts
        if addopts or key == "pytest.ini":
            return addopts, strict_warn
    rel = config_files.get("pyproject.toml")
    if rel:
        text = _read(repo_path, rel)
        if "[tool.pytest.ini_options]" in text:
            section = text.split("[tool.pytest.ini_options]", 1)[1]
            section = section.split("\n[", 1)[0]
            addopts_match = re.search(r"addopts\s*=\s*(.+)", section)
            addopts = addopts_match.group(1).strip() if addopts_match else ""
            strict_warn = bool(re.search(r"filterwarnings\s*=.*error", section)) or "-W error" in addopts
            return addopts, strict_warn
    return "", False


_INVOCATION_RE = re.compile(
    r"^(?:\.?[\w./-]*/)?python3?(\.\d+)?\s+-m\s+pytest\b"  # python -m pytest / .venv/bin/python -m pytest
    r"|^(?:\.?[\w./-]*/)?pytest\b"  # bare pytest / .venv/bin/pytest
)
_NON_INVOCATION_HINTS = ("pip install", "pip3 install", "apt-get install", "requirements", "poetry add", "uv add")


def _looks_like_pytest_invocation(line: str) -> bool:
    stripped = line.strip().strip("`$>").strip()
    if not stripped or stripped.startswith(("#", "//", '"')):
        return False
    if any(hint in stripped.lower() for hint in _NON_INVOCATION_HINTS):
        return False
    return bool(_INVOCATION_RE.match(stripped))


def _strict_command_from_ci(repo_path: str, files: list[str]) -> tuple[list[str] | None, str, list[str]]:
    ci_files = [rel for rel in files if rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml"))]
    ci_files += [rel for rel in files if os.path.basename(rel) in (".gitlab-ci.yml", "azure-pipelines.yml")]
    for rel in ci_files:
        text = _read(repo_path, rel)
        for line in text.splitlines():
            stripped = line.strip().lstrip("-").strip()
            if re.match(r"^(run|script)\s*:\s*", stripped):
                stripped = re.sub(r"^(run|script)\s*:\s*", "", stripped).strip("|>").strip()
            if _looks_like_pytest_invocation(stripped):
                return _tokenize_command(stripped), "high", [f"{rel}: {stripped}"]
    return None, "none", []


def _strict_command_from_make_tox_nox(repo_path: str, config_files: dict[str, str]) -> tuple[list[str] | None, str, list[str]]:
    make_rel = config_files.get("Makefile")
    if make_rel:
        text = _read(repo_path, make_rel)
        for target in ("test-strict", "test-full", "test"):
            match = re.search(rf"^{re.escape(target)}:.*?\n((?:\t.*\n?)+)", text, re.MULTILINE)
            if match:
                for line in match.group(1).splitlines():
                    cmd = line.strip("\t ").strip()
                    if _looks_like_pytest_invocation(cmd):
                        return _tokenize_command(cmd), "high", [f"Makefile [{target}]: {cmd}"]
    tox_rel = config_files.get("tox.ini")
    if tox_rel:
        text = _read(repo_path, tox_rel)
        match = re.search(r"commands\s*=\s*(.+?)(?:\n\S|\Z)", text, re.DOTALL)
        if match:
            for line in match.group(1).splitlines():
                cmd = line.strip()
                if _looks_like_pytest_invocation(cmd):
                    return _tokenize_command(cmd), "high", [f"tox.ini [testenv]: {cmd}"]
    nox_rel = config_files.get("noxfile.py")
    if nox_rel:
        text = _read(repo_path, nox_rel)
        match = re.search(r'session\.run\(\s*["\']pytest["\']([^)]*)\)', text)
        if match:
            args = re.findall(r'["\']([^"\']+)["\']', match.group(1))
            return ["pytest", *args], "high", [f"noxfile.py: session.run(\"pytest\", {match.group(1).strip()})"]
    return None, "none", []


def _strict_command_from_readme(repo_path: str, files: list[str]) -> tuple[list[str] | None, str, list[str]]:
    readmes = [rel for rel in files if os.path.basename(rel).lower().startswith("readme")]
    for rel in readmes:
        text = _read(repo_path, rel)
        for match in re.finditer(r"^[^\n`]*pytest[^\n`]*$", text, re.MULTILINE):
            line = match.group(0).strip().strip("`$ ").strip()
            if _looks_like_pytest_invocation(line):
                return _tokenize_command(line), "medium", [f"{rel}: {line}"]
    return None, "none", []


def _tokenize_command(cmd: str) -> list[str]:
    cmd = cmd.strip().lstrip("$").strip()
    return cmd.split()


def discover(repo_path: str) -> RepoTestConfig:
    repo_path = os.path.abspath(repo_path)
    profile = load_test_profile(repo_path)
    files = _walk_files(repo_path)
    config_files = _find_config_files(files)
    venv_python = _venv_python(repo_path)
    python_version = ".".join(map(str, sys.version_info[:3]))
    pytest_version = _pytest_version(venv_python)

    test_roots = _test_roots(files)
    conftest_paths = sorted(rel for rel in files if os.path.basename(rel) == "conftest.py")
    markers = _markers(repo_path, config_files)
    dependency_files = _dependency_files(files)
    migration_dirs = _migration_dirs(files)

    addopts, strict_warn = _addopts_and_filterwarnings(repo_path, config_files)

    strict_cmd = None
    strict_conf = "none"
    strict_evidence: list[str] = []
    profile_strict = profile.get("strict_full_command")
    if profile_strict:
        strict_cmd = profile_strict if isinstance(profile_strict, list) else _tokenize_command(str(profile_strict))
        strict_conf = "high"
        strict_evidence = [".autocorp/test-profile.json: strict_full_command"]
    else:
        strict_cmd, strict_conf, strict_evidence = _strict_command_from_ci(repo_path, files)
        if strict_cmd is None:
            strict_cmd, strict_conf, strict_evidence = _strict_command_from_make_tox_nox(repo_path, config_files)
        if strict_cmd is None:
            strict_cmd, strict_conf, strict_evidence = _strict_command_from_readme(repo_path, files)
        if strict_cmd is None and ("pytest.ini" in config_files or "pyproject.toml" in config_files or "setup.cfg" in config_files):
            strict_cmd = [venv_python, "-m", "pytest"]
            strict_conf = "high" if strict_warn else "medium"
            evidence = [f"{config_files.get('pytest.ini') or config_files.get('pyproject.toml') or config_files.get('setup.cfg')}: pytest configuration file present"]
            if addopts:
                evidence.append(f"addopts = {addopts}")
            if strict_warn:
                evidence.append("filterwarnings/addopts treats warnings as errors")
            strict_evidence = evidence

    compile_rel = "scripts/verify_compileall.py"
    if os.path.isfile(os.path.join(repo_path, compile_rel)):
        compile_command = [venv_python, compile_rel, "--repo", repo_path]
    else:
        compile_command = [venv_python, "-m", "compileall", "-q", repo_path]

    gpu_files, db_files, net_files = _keyword_scan(repo_path, files)

    has_xdist = _has_xdist(venv_python)

    config_fingerprint_inputs = sorted(
        list(config_files.values()) + conftest_paths + dependency_files + migration_dirs
    )

    return RepoTestConfig(
        repo_path=repo_path,
        venv_python=venv_python,
        python_version=python_version,
        pytest_version=pytest_version,
        config_files=config_files,
        test_roots=test_roots or ["."],
        conftest_paths=conftest_paths,
        markers=markers,
        dependency_files=dependency_files,
        migration_dirs=migration_dirs,
        strict_full_command=strict_cmd,
        strict_full_command_confidence=strict_conf,
        strict_full_command_evidence=strict_evidence,
        compile_command=compile_command,
        gpu_keyword_files=gpu_files,
        database_keyword_files=db_files,
        network_keyword_files=net_files,
        has_xdist=has_xdist,
        profile=profile,
        config_fingerprint_inputs=config_fingerprint_inputs,
    )


def _keyword_scan(repo_path: str, files: list[str]) -> tuple[list[str], list[str], list[str]]:
    gpu, db, net = [], [], []
    for rel in files:
        base = os.path.basename(rel)
        if not (base.startswith("test_") or base.endswith("_test.py")) or not rel.endswith(".py"):
            continue
        text = _read(repo_path, rel).lower()
        if any(k in text for k in _GPU_KEYWORDS):
            gpu.append(rel)
        if any(k in text for k in _DB_KEYWORDS):
            db.append(rel)
        if any(k in text for k in _NETWORK_KEYWORDS):
            net.append(rel)
    return sorted(gpu), sorted(db), sorted(net)


def _has_xdist(venv_python: str) -> bool:
    try:
        proc = subprocess.run(
            [venv_python, "-c", "import xdist"],
            capture_output=True, text=True, timeout=15,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
