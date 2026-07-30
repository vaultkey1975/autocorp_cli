#!/usr/bin/env python3
"""Universal Repository Discovery Engine.

Discovery is the first-stage repository profiler for projects with no
AutoCorp-specific engineering documents. It is read-only against the target
repository and stores only AutoCorp metadata in memory/store.py.
"""

from __future__ import annotations

import json
import os
import re
import ast
from dataclasses import dataclass, field

from brains import analyzer, scanner
from memory import store


_LANG_EXTS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".cs": "C#",
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C++",
}

_TEXT_EXTS = set(_LANG_EXTS) | {".txt", ".md", ".toml", ".json", ".yaml", ".yml", ".xml", ".gradle", ".ini", ".cfg", ".csproj"}
_MAX_READ = 2 * 1024 * 1024
_DISCOVERY_IGNORE_DIRS = set(scanner.IGNORE_DIRS) | {
    "workspace", "data", "runtime", "output", "outputs", "artifacts",
    ".coverage", "htmlcov",
}


@dataclass(frozen=True)
class DiscoveryFinding:
    value: str
    evidence: tuple[str, ...] = ()
    confidence: int = 0

    def to_dict(self) -> dict:
        return {"value": self.value, "evidence": list(self.evidence), "confidence": self.confidence}


@dataclass(frozen=True)
class RepositoryProfile:
    repo_path: str
    repo_name: str
    repository_type: DiscoveryFinding
    languages: tuple[DiscoveryFinding, ...] = ()
    frameworks: tuple[DiscoveryFinding, ...] = ()
    package_managers: tuple[DiscoveryFinding, ...] = ()
    build_system: tuple[DiscoveryFinding, ...] = ()
    test_frameworks: tuple[DiscoveryFinding, ...] = ()
    lint_tools: tuple[DiscoveryFinding, ...] = ()
    formatters: tuple[DiscoveryFinding, ...] = ()
    type_checkers: tuple[DiscoveryFinding, ...] = ()
    database_technology: tuple[DiscoveryFinding, ...] = ()
    containerization: tuple[DiscoveryFinding, ...] = ()
    cicd_platforms: tuple[DiscoveryFinding, ...] = ()
    operating_systems: tuple[DiscoveryFinding, ...] = ()
    repository_size: dict = field(default_factory=dict)
    project_structure: tuple[DiscoveryFinding, ...] = ()
    documentation: tuple[DiscoveryFinding, ...] = ()
    license: DiscoveryFinding = field(default_factory=lambda: DiscoveryFinding("Unknown", ("No license file found",), 25))
    architecture: DiscoveryFinding = field(default_factory=lambda: DiscoveryFinding("Unknown", ("Not enough evidence",), 20))
    application_type: DiscoveryFinding = field(default_factory=lambda: DiscoveryFinding("Unknown", ("Not enough evidence",), 20))
    dependencies: tuple[DiscoveryFinding, ...] = ()
    build_process: tuple[DiscoveryFinding, ...] = ()
    deployment: tuple[DiscoveryFinding, ...] = ()
    production_readiness: DiscoveryFinding = field(default_factory=lambda: DiscoveryFinding("Unknown", ("Not enough evidence",), 20))
    engineering_maturity: DiscoveryFinding = field(default_factory=lambda: DiscoveryFinding("Unknown", ("Not enough evidence",), 20))
    known_risks: tuple[str, ...] = ()
    unknown_areas: tuple[str, ...] = ()
    confidence: int = 0
    preferred_test_command: str = "Unknown"
    preferred_build_command: str = "Unknown"
    preferred_lint_command: str = "Unknown"
    preferred_package_manager: str = "Unknown"
    preferred_entry_point: str = "Unknown"
    preferred_documentation: str = "Unknown"

    def to_dict(self) -> dict:
        return {
            "repo_path": self.repo_path,
            "repo_name": self.repo_name,
            "repository_type": self.repository_type.to_dict(),
            "languages": [f.to_dict() for f in self.languages],
            "frameworks": [f.to_dict() for f in self.frameworks],
            "package_managers": [f.to_dict() for f in self.package_managers],
            "build_system": [f.to_dict() for f in self.build_system],
            "test_frameworks": [f.to_dict() for f in self.test_frameworks],
            "lint_tools": [f.to_dict() for f in self.lint_tools],
            "formatters": [f.to_dict() for f in self.formatters],
            "type_checkers": [f.to_dict() for f in self.type_checkers],
            "database_technology": [f.to_dict() for f in self.database_technology],
            "containerization": [f.to_dict() for f in self.containerization],
            "cicd_platforms": [f.to_dict() for f in self.cicd_platforms],
            "operating_systems": [f.to_dict() for f in self.operating_systems],
            "repository_size": dict(self.repository_size),
            "project_structure": [f.to_dict() for f in self.project_structure],
            "documentation": [f.to_dict() for f in self.documentation],
            "license": self.license.to_dict(),
            "architecture": self.architecture.to_dict(),
            "application_type": self.application_type.to_dict(),
            "dependencies": [f.to_dict() for f in self.dependencies],
            "build_process": [f.to_dict() for f in self.build_process],
            "deployment": [f.to_dict() for f in self.deployment],
            "production_readiness": self.production_readiness.to_dict(),
            "engineering_maturity": self.engineering_maturity.to_dict(),
            "known_risks": list(self.known_risks),
            "unknown_areas": list(self.unknown_areas),
            "confidence": self.confidence,
            "preferred_test_command": self.preferred_test_command,
            "preferred_build_command": self.preferred_build_command,
            "preferred_lint_command": self.preferred_lint_command,
            "preferred_package_manager": self.preferred_package_manager,
            "preferred_entry_point": self.preferred_entry_point,
            "preferred_documentation": self.preferred_documentation,
        }


def discover_repository(repo_path: str, store_profile: bool = True) -> RepositoryProfile:
    repo_path = os.path.abspath(repo_path)
    scan = scanner.run_scan(repo_path)
    analysis = analyzer.run_analysis(repo_path)
    files = _inventory(repo_path)
    manifests = _manifest_text(repo_path)

    languages = _languages(files)
    package_managers = _package_managers(files)
    frameworks = _frameworks(files, manifests)
    build_system = _build_system(files, manifests)
    test_frameworks = _test_frameworks(files, manifests, analysis)
    lint_tools = _tool_findings("lint", files, manifests)
    formatters = _tool_findings("format", files, manifests)
    type_checkers = _tool_findings("type", files, manifests)
    databases = _databases(files, manifests)
    containers = _containers(files)
    cicd = _cicd(files)
    oses = _operating_systems(files, manifests)
    docs = _documentation(files)
    license_finding = _license(repo_path, files)
    structure = _structure(repo_path, files)
    app_type = _application_type(analysis, frameworks, files, manifests)
    architecture = _architecture(app_type, structure, containers, cicd)
    dependencies = tuple(package_managers) + tuple(frameworks)
    build_process = _build_process(build_system, manifests)
    deployment = _deployment(containers, cicd, manifests)
    prod = _production_readiness(scan, test_frameworks, docs, containers, cicd)
    maturity = _engineering_maturity(scan, test_frameworks, docs, cicd, package_managers)
    risks = _known_risks(scan, test_frameworks, docs, frameworks)
    unknowns = _unknown_areas(languages, package_managers, test_frameworks, build_system, docs, license_finding, app_type)
    confidence = _confidence(languages, package_managers, test_frameworks, docs, unknowns)

    profile = RepositoryProfile(
        repo_path=repo_path,
        repo_name=os.path.basename(repo_path.rstrip(os.sep)) or repo_path,
        repository_type=app_type,
        languages=tuple(languages),
        frameworks=tuple(frameworks),
        package_managers=tuple(package_managers),
        build_system=tuple(build_system),
        test_frameworks=tuple(test_frameworks),
        lint_tools=tuple(lint_tools),
        formatters=tuple(formatters),
        type_checkers=tuple(type_checkers),
        database_technology=tuple(databases),
        containerization=tuple(containers),
        cicd_platforms=tuple(cicd),
        operating_systems=tuple(oses),
        repository_size={
            "python_files": sum(1 for rel in files if rel.endswith(".py")),
            "test_files": sum(
                1
                for rel in files
                if os.path.basename(rel).startswith("test_")
                or os.path.basename(rel).endswith("_test.py")
            ),
            "architecture_python_files": analysis.python_file_count,
            "total_python_lines": analysis.total_python_lines,
        },
        project_structure=tuple(structure),
        documentation=tuple(docs),
        license=license_finding,
        architecture=architecture,
        application_type=app_type,
        dependencies=dependencies,
        build_process=tuple(build_process),
        deployment=tuple(deployment),
        production_readiness=prod,
        engineering_maturity=maturity,
        known_risks=tuple(risks),
        unknown_areas=tuple(unknowns),
        confidence=confidence,
        preferred_test_command=_preferred_test(test_frameworks, manifests),
        preferred_build_command=_preferred_build(build_process, manifests),
        preferred_lint_command=_preferred_lint(lint_tools, type_checkers, manifests),
        preferred_package_manager=package_managers[0].value if package_managers else "Unknown",
        preferred_entry_point=_preferred_entry_point(repo_path, analysis, files, manifests),
        preferred_documentation=_preferred_docs(docs),
    )
    if store_profile:
        store.save_repository_profile(profile.to_dict())
    return profile


def render_profile(profile: RepositoryProfile, full: bool = False) -> str:
    lines = [
        "Repository Profile",
        "==================",
        "",
        f"Repository Name: {profile.repo_name}",
        f"Repository Path: {profile.repo_path}",
        f"Repository Type: {profile.repository_type.value}",
        f"Confidence Level: {profile.confidence}%",
        "",
    ]
    sections = [
        ("Languages", profile.languages),
        ("Frameworks", profile.frameworks),
        ("Package Managers", profile.package_managers),
        ("Build System", profile.build_system),
        ("Testing", profile.test_frameworks),
        ("Lint Tools", profile.lint_tools),
        ("Formatter", profile.formatters),
        ("Type Checker", profile.type_checkers),
        ("Database Technology", profile.database_technology),
        ("Containerization", profile.containerization),
        ("CI/CD Platform", profile.cicd_platforms),
        ("Operating Systems Supported", profile.operating_systems),
        ("Project Structure", profile.project_structure),
        ("Documentation", profile.documentation),
        ("Dependencies", profile.dependencies),
        ("Build Process", profile.build_process),
        ("Deployment", profile.deployment),
    ]
    for title, findings in sections:
        lines.extend([title, "-" * len(title)])
        lines.extend(_finding_lines(findings, full=full))
        lines.append("")
    lines.extend([
        "License",
        "-------",
        f"- {profile.license.value}",
        "",
        "Architecture",
        "------------",
        f"- {profile.architecture.value}",
        "",
        "Application Type",
        "----------------",
        f"- {profile.application_type.value}",
        "",
        "Production Readiness",
        "--------------------",
        f"- {profile.production_readiness.value}",
        "",
        "Engineering Maturity",
        "--------------------",
        f"- {profile.engineering_maturity.value}",
        "",
        "Self-Learned Preferences",
        "------------------------",
        f"- Preferred test command: {profile.preferred_test_command}",
        f"- Preferred build command: {profile.preferred_build_command}",
        f"- Preferred lint command: {profile.preferred_lint_command}",
        f"- Preferred package manager: {profile.preferred_package_manager}",
        f"- Preferred entry point: {profile.preferred_entry_point}",
        f"- Preferred documentation: {profile.preferred_documentation}",
        "",
        "Known Risks",
        "-----------",
    ])
    lines.extend(_bullet(profile.known_risks))
    lines.extend(["", "Unknown Areas", "-------------"])
    lines.extend(_bullet(profile.unknown_areas))
    return "\n".join(lines)


def profile_to_json(profile: RepositoryProfile) -> str:
    return json.dumps(profile.to_dict(), indent=2, sort_keys=True)


def _inventory(repo_path: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not scanner._should_skip_dir(d, _DISCOVERY_IGNORE_DIRS)]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), repo_path).replace(os.sep, "/")
            out.append(rel)
    return sorted(out)


def _manifest_text(repo_path: str) -> dict[str, str]:
    names = {
        "requirements.txt", "requirements-dev.txt", "pyproject.toml",
        "setup.cfg", "setup.py", "pytest.ini", "ruff.toml", "mypy.ini",
        "package.json",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
        "Dockerfile", "docker-compose.yml", "compose.yml", "Makefile", "Taskfile.yml",
        ".gitlab-ci.yml", "azure-pipelines.yml", "README.md", "README.rst",
        "CMakeLists.txt",
    }
    texts = {}
    for rel in _inventory(repo_path):
        name = os.path.basename(rel)
        if (
            rel in names
            or name in names
            or rel.startswith(".github/workflows/")
            or rel.endswith((".csproj", ".sln", ".vcxproj"))
        ):
            text = _read_file(os.path.join(repo_path, rel))
            if text:
                texts[rel] = text
    return texts


def _read_file(path: str) -> str:
    try:
        if os.path.getsize(path) > _MAX_READ:
            return ""
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _languages(files: list[str]) -> list[DiscoveryFinding]:
    counts = {}
    evidence = {}
    for rel in files:
        lang = _LANG_EXTS.get(os.path.splitext(rel)[1])
        if not lang:
            continue
        counts[lang] = counts.get(lang, 0) + 1
        evidence.setdefault(lang, []).append(rel)
    total = sum(counts.values())
    return [
        DiscoveryFinding(lang, (f"{counts[lang]} file(s)", *evidence[lang][:3]), min(95, 55 + round(40 * counts[lang] / max(total, 1))))
        for lang in sorted(counts, key=lambda k: (-counts[k], k))
    ]


def _has(files: list[str], *names: str) -> list[str]:
    wanted = set(names)
    return [rel for rel in files if rel in wanted or os.path.basename(rel) in wanted]


def _package_managers(files: list[str]) -> list[DiscoveryFinding]:
    checks = [
        ("pip", ("requirements.txt", "requirements-dev.txt")),
        ("Poetry/Python packaging", ("pyproject.toml",)),
        ("npm", ("package.json", "package-lock.json")),
        ("Yarn", ("yarn.lock",)),
        ("pnpm", ("pnpm-lock.yaml",)),
        ("Cargo", ("Cargo.toml", "Cargo.lock")),
        ("Go modules", ("go.mod", "go.sum")),
        ("Maven", ("pom.xml",)),
        ("Gradle", ("build.gradle", "build.gradle.kts")),
    ]
    findings = [DiscoveryFinding(label, tuple(matches[:4]), 90) for label, names in checks if (matches := _has(files, *names))]
    dotnet = [rel for rel in files if rel.endswith((".csproj", ".sln"))]
    if dotnet:
        findings.append(DiscoveryFinding(".NET/NuGet", tuple(dotnet[:4]), 85))
    return findings


def _frameworks(files: list[str], texts: dict[str, str]) -> list[DiscoveryFinding]:
    signal_texts = _signal_texts(texts)
    blob = "\n".join(signal_texts.values()).lower()
    checks = [
        ("FastAPI", "fastapi"),
        ("Flask", "flask"),
        ("Django", "django"),
        ("PySide/PyQt Desktop", "pyside"),
        ("React", "react"),
        ("Next.js", "next"),
        ("Vue", "vue"),
        ("Express", "express"),
        ("Spring", "spring-boot"),
        ("ASP.NET", "microsoft.aspnetcore"),
        ("Bevy", "bevy"),
    ]
    findings = []
    for label, needle in checks:
        ev = [rel for rel, text in signal_texts.items() if needle in text.lower()]
        if ev or needle in blob:
            findings.append(DiscoveryFinding(label, tuple(ev[:4] or (f"manifest mentions {needle}",)), 80))
    if any(rel.endswith(".html") for rel in files) and not findings:
        findings.append(DiscoveryFinding("Static web assets", tuple(rel for rel in files if rel.endswith(".html"))[:3], 65))
    return findings


def _build_system(files: list[str], texts: dict[str, str]) -> list[DiscoveryFinding]:
    checks = [
        ("Make", ("Makefile",)),
        ("Taskfile", ("Taskfile.yml", "Taskfile.yaml")),
        ("Python packaging", ("pyproject.toml", "setup.py")),
        ("npm scripts", ("package.json",)),
        ("Cargo", ("Cargo.toml",)),
        ("Go toolchain", ("go.mod",)),
        ("Maven", ("pom.xml",)),
        ("Gradle", ("build.gradle", "build.gradle.kts")),
    ]
    findings = [DiscoveryFinding(label, tuple(matches[:4]), 85) for label, names in checks if (matches := _has(files, *names))]
    dotnet = [rel for rel in files if rel.endswith((".csproj", ".sln"))]
    if dotnet:
        findings.append(DiscoveryFinding(".NET SDK", tuple(dotnet[:4]), 85))
    cmake = _has(files, "CMakeLists.txt")
    if cmake:
        findings.append(DiscoveryFinding("CMake", tuple(cmake[:4]), 85))
    if "package.json" in texts and '"build"' in texts["package.json"]:
        findings.append(DiscoveryFinding("npm build script", ("package.json scripts.build",), 90))
    return findings


def _test_frameworks(files: list[str], texts: dict[str, str], analysis) -> list[DiscoveryFinding]:
    findings = []
    if analysis.test_framework != "unknown":
        findings.append(DiscoveryFinding(analysis.test_framework, (f"analyzer test framework: {analysis.test_framework}",), 85))
    test_files = [rel for rel in files if "/test" in f"/{rel}".lower() or rel.lower().startswith("test")]
    if test_files and not findings:
        findings.append(DiscoveryFinding("Unknown test framework", tuple(test_files[:4]), 50))
    checks = [("pytest", "pytest"), ("Jest", "jest"), ("Vitest", "vitest"), ("JUnit", "junit"), ("xUnit", "xunit"), ("NUnit", "nunit"), ("Go test", "_test.go")]
    for label, needle in checks:
        ev = [rel for rel, text in texts.items() if needle.lower() in text.lower()]
        if ev or any(needle in rel for rel in files):
            findings.append(DiscoveryFinding(label, tuple(ev[:4] or (needle,)), 80))
    return _dedupe(findings)


def _tool_findings(kind: str, files: list[str], texts: dict[str, str]) -> list[DiscoveryFinding]:
    checks = {
        "lint": [("ruff", "ruff"), ("flake8", "flake8"), ("ESLint", "eslint"), ("golangci-lint", "golangci"), ("Clippy", "clippy")],
        "format": [("Black", "black"), ("Prettier", "prettier"), ("rustfmt", "rustfmt"), ("gofmt", "gofmt")],
        "type": [("mypy", "mypy"), ("pyright", "pyright"), ("TypeScript compiler", "typescript"), ("tsc", "tsc")],
    }[kind]
    blob = "\n".join(_signal_texts(texts).values()).lower()
    return [DiscoveryFinding(label, (f"manifest/config mentions {needle}",), 75) for label, needle in checks if needle.lower() in blob or any(needle.lower() in rel.lower() for rel in files)]


def _databases(files: list[str], texts: dict[str, str]) -> list[DiscoveryFinding]:
    blob = "\n".join(_signal_texts(texts).values()).lower()
    checks = [("SQLite", "sqlite"), ("PostgreSQL", "postgres"), ("MySQL", "mysql"), ("MongoDB", "mongodb"), ("Redis", "redis"), ("SQLAlchemy", "sqlalchemy"), ("Prisma", "prisma")]
    return [DiscoveryFinding(label, (f"repository evidence mentions {needle}",), 70) for label, needle in checks if needle in blob or any(needle in rel.lower() for rel in files)]


def _containers(files: list[str]) -> list[DiscoveryFinding]:
    checks = [("Docker", ("Dockerfile",)), ("Docker Compose", ("docker-compose.yml", "compose.yml", "compose.yaml"))]
    return [DiscoveryFinding(label, tuple(matches), 90) for label, names in checks if (matches := _has(files, *names))]


def _cicd(files: list[str]) -> list[DiscoveryFinding]:
    findings = []
    if any(rel.startswith(".github/workflows/") for rel in files):
        findings.append(DiscoveryFinding("GitHub Actions", tuple(rel for rel in files if rel.startswith(".github/workflows/"))[:4], 95))
    for label, names in (("GitLab CI", (".gitlab-ci.yml",)), ("Azure Pipelines", ("azure-pipelines.yml",))):
        if matches := _has(files, *names):
            findings.append(DiscoveryFinding(label, tuple(matches), 95))
    return findings


def _operating_systems(files: list[str], texts: dict[str, str]) -> list[DiscoveryFinding]:
    blob = "\n".join(_signal_texts(texts).values()).lower()
    checks = [("Linux", "ubuntu"), ("Windows", "windows"), ("macOS", "macos")]
    return [DiscoveryFinding(label, (f"CI/config mentions {needle}",), 70) for label, needle in checks if needle in blob]


def _documentation(files: list[str]) -> list[DiscoveryFinding]:
    findings = []
    readmes = [rel for rel in files if os.path.basename(rel).lower().startswith("readme")]
    if readmes:
        findings.append(DiscoveryFinding("README present", tuple(readmes[:3]), 90))
    docs = [rel for rel in files if rel == "docs" or rel.startswith("docs/")]
    if docs:
        findings.append(DiscoveryFinding("docs/ present", tuple(docs[:4]), 80))
    if any(rel.startswith("AI_ENGINEERING/") for rel in files):
        findings.append(DiscoveryFinding("AI_ENGINEERING present", tuple(rel for rel in files if rel.startswith("AI_ENGINEERING/"))[:4], 95))
    if not findings:
        findings.append(DiscoveryFinding("Not enough evidence", ("No README, docs/, or AI_ENGINEERING/ found",), 35))
    return findings


def _license(repo_path: str, files: list[str]) -> DiscoveryFinding:
    for rel in files:
        if os.path.basename(rel).lower() in {"license", "license.md", "license.txt", "copying"}:
            first = _read_file(os.path.join(repo_path, rel)).splitlines()
            value = first[0].strip() if first else "License file present"
            return DiscoveryFinding(value, (rel,), 90)
    return DiscoveryFinding("Unknown", ("No license file found",), 25)


def _structure(repo_path: str, files: list[str]) -> list[DiscoveryFinding]:
    dirs = {rel.split("/")[0] for rel in files if "/" in rel}
    labels = []
    for dirname, label in (("src", "src/ layout"), ("tests", "tests/ present"), ("docs", "docs/ present"), ("examples", "examples/ present"), ("AI_ENGINEERING", "AI engineering docs present")):
        if dirname in dirs:
            labels.append(DiscoveryFinding(label, (f"{dirname}/",), 85))
    if not labels:
        root_files = [rel for rel in files if "/" not in rel]
        labels.append(DiscoveryFinding("flat/root-file layout", tuple(root_files[:5]) or ("No files found",), 55))
    return labels


def _application_type(analysis, frameworks, files: list[str], texts: dict[str, str]) -> DiscoveryFinding:
    fw = {f.value for f in frameworks}
    if analysis.project_type and analysis.project_type != "Unknown":
        return DiscoveryFinding(analysis.project_type, (f"analyzer project type: {analysis.project_type}",), analysis.confidence)
    if {"FastAPI", "Flask", "Django", "Express", "Spring", "ASP.NET"} & fw:
        return DiscoveryFinding("REST API / Web App", tuple(f.value for f in frameworks), 80)
    if {"React", "Next.js", "Vue", "Static web assets"} & fw:
        return DiscoveryFinding("Web App", tuple(f.value for f in frameworks), 75)
    if any(rel.endswith(".rs") for rel in files):
        return DiscoveryFinding("Rust application/library", ("Rust source files",), 60)
    if any(rel.endswith(".go") for rel in files):
        return DiscoveryFinding("Go application/library", ("Go source files",), 60)
    if any(rel.endswith(".cs") for rel in files):
        return DiscoveryFinding(".NET application/library", ("C# source files",), 60)
    if any(rel.endswith((".cpp", ".cc", ".cxx", ".c")) for rel in files):
        return DiscoveryFinding("Native application/library", ("C/C++ source files",), 60)
    return DiscoveryFinding("Unknown", ("Not enough evidence",), 20)


def _architecture(app_type, structure, containers, cicd) -> DiscoveryFinding:
    if "REST API" in app_type.value:
        return DiscoveryFinding("Service/API", app_type.evidence, 75)
    if "CLI" in app_type.value:
        return DiscoveryFinding("CLI application", app_type.evidence, 80)
    if containers and cicd:
        return DiscoveryFinding("Deployable service", tuple(f.value for f in containers + cicd), 70)
    if any("src/" in f.value for f in structure):
        return DiscoveryFinding("src-layout project", ("src/ directory present",), 65)
    return DiscoveryFinding("Unknown", ("Not enough evidence",), 25)


def _build_process(build_system, texts):
    out = list(build_system)
    pkg = texts.get("package.json", "")
    for name in ("build", "test", "lint", "format"):
        if f'"{name}"' in pkg:
            out.append(DiscoveryFinding(f"npm script: {name}", ("package.json",), 85))
    return _dedupe(out)


def _deployment(containers, cicd, texts):
    out = list(containers) + list(cicd)
    if not out:
        out.append(DiscoveryFinding("Unknown", ("No Docker, compose, or CI deployment config found",), 30))
    return _dedupe(out)


def _production_readiness(scan_result, tests, docs, containers, cicd) -> DiscoveryFinding:
    missing = []
    if scan_result.working_tree != "clean":
        missing.append("dirty working tree")
    if not tests:
        missing.append("no tests detected")
    if not docs or docs[0].value == "Not enough evidence":
        missing.append("no README/docs detected")
    if missing:
        return DiscoveryFinding("Needs inspection", tuple(missing), 55)
    if containers or cicd:
        return DiscoveryFinding("Moderate evidence", ("tests and documentation present",), 75)
    return DiscoveryFinding("Basic evidence only", ("tests and documentation present; deployment evidence missing",), 65)


def _engineering_maturity(scan_result, tests, docs, cicd, package_managers) -> DiscoveryFinding:
    signals = []
    if tests:
        signals.append("tests detected")
    if docs and docs[0].value != "Not enough evidence":
        signals.append("documentation detected")
    if cicd:
        signals.append("CI/CD detected")
    if package_managers:
        signals.append("dependency management detected")
    if scan_result.working_tree == "clean":
        signals.append("clean working tree")
    if len(signals) >= 4:
        return DiscoveryFinding("High", tuple(signals), 80)
    if len(signals) >= 2:
        return DiscoveryFinding("Medium", tuple(signals), 65)
    return DiscoveryFinding("Low", tuple(signals or ("Not enough evidence",)), 40)


def _known_risks(scan_result, tests, docs, frameworks) -> list[str]:
    risks = []
    if scan_result.working_tree == "dirty":
        risks.append("Working tree is dirty; review existing changes before automation.")
    if not tests:
        risks.append("No test framework detected.")
    if docs and docs[0].value == "Not enough evidence":
        risks.append("No README/docs/AI_ENGINEERING evidence found.")
    if not frameworks:
        risks.append("Framework/application stack is unknown.")
    return risks or ["No major discovery risks found from available evidence."]


def _unknown_areas(languages, package_managers, tests, build_system, docs, license_finding, app_type) -> list[str]:
    unknown = []
    if not languages:
        unknown.append("Primary language: Unknown - not enough source-file evidence.")
    if not package_managers:
        unknown.append("Package manager: Unknown - no supported manifest found.")
    if not tests:
        unknown.append("Testing: Unknown - no supported test evidence found.")
    if not build_system:
        unknown.append("Build process: Unknown - no supported build config found.")
    if docs and docs[0].value == "Not enough evidence":
        unknown.append("Documentation quality: Not enough evidence.")
    if license_finding.value == "Unknown":
        unknown.append("License: Unknown - no license file found.")
    if app_type.value == "Unknown":
        unknown.append("Application type: Unknown - additional inspection required.")
    return unknown or ["No major unknown areas found from supported discovery evidence."]


def _confidence(languages, package_managers, tests, docs, unknowns) -> int:
    score = 35
    if languages:
        score += 20
    if package_managers:
        score += 15
    if tests:
        score += 10
    if docs and docs[0].value != "Not enough evidence":
        score += 10
    real_unknowns = [u for u in unknowns if "No major unknown" not in u]
    score -= min(25, len(real_unknowns) * 5)
    return max(10, min(95, score))


def _preferred_test(tests, texts) -> str:
    values = {t.value.lower() for t in tests}
    if "pytest" in values:
        return ".venv/bin/python -m pytest"
    if "jest" in values:
        return "npm test"
    if "vitest" in values:
        return "npm test"
    if "go test" in values:
        return "go test ./..."
    if "junit" in values:
        return "mvn test" if "pom.xml" in texts else "gradle test"
    if "xunit" in values or "nunit" in values:
        return "dotnet test"
    return "Unknown"


def _preferred_build(build_process, texts) -> str:
    vals = {b.value for b in build_process}
    if "npm build script" in vals or "npm script: build" in vals:
        return "npm run build"
    if "Cargo" in vals:
        return "cargo build"
    if "Go toolchain" in vals:
        return "go build ./..."
    if "Maven" in vals:
        return "mvn package"
    if "Gradle" in vals:
        return "gradle build"
    if ".NET SDK" in vals:
        return "dotnet build"
    if "CMake" in vals:
        return "cmake --build build"
    if "Make" in vals:
        return "make"
    return "Unknown"


def _preferred_lint(lints, type_checkers, texts) -> str:
    vals = {f.value for f in lints + type_checkers}
    if "ruff" in vals:
        return "ruff check ."
    if "ESLint" in vals:
        return "npm run lint"
    if "Clippy" in vals:
        return "cargo clippy"
    if "golangci-lint" in vals:
        return "golangci-lint run"
    if "mypy" in vals:
        return "mypy ."
    return "Unknown"


def _preferred_docs(docs) -> str:
    for item in docs:
        if item.value == "AI_ENGINEERING present":
            return "AI_ENGINEERING/"
        if item.value == "README present":
            return item.evidence[0]
        if item.value == "docs/ present":
            return "docs/"
    return "Unknown"


def _preferred_entry_point(repo_path: str, analysis, files: list[str], texts: dict[str, str]) -> str:
    if analysis.entry_points:
        return analysis.entry_points[0]
    for rel, text in texts.items():
        name = os.path.basename(rel)
        if name == "pyproject.toml" and "[project.scripts]" in text:
            return "pyproject.toml [project.scripts]"
        if name in {"setup.cfg", "setup.py"} and "console_scripts" in text:
            return f"{rel} console_scripts"
    for rel in files:
        if not rel.endswith(".py"):
            continue
        content = _read_file(os.path.join(repo_path, rel))
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id == "FastAPI":
                    return f"{rel}:FastAPI"
    return "Unknown"


def _finding_lines(findings, full: bool) -> list[str]:
    if not findings:
        return ["- Unknown - not enough evidence"]
    lines = []
    for finding in findings:
        lines.append(f"- {finding.value} ({finding.confidence}%)")
        if full:
            for ev in finding.evidence:
                lines.append(f"  Evidence: {ev}")
    return lines


def _bullet(items) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- (none)"]


def _dedupe(findings):
    seen = set()
    out = []
    for finding in findings:
        if finding.value in seen:
            continue
        seen.add(finding.value)
        out.append(finding)
    return out


def _signal_texts(texts: dict[str, str]) -> dict[str, str]:
    """Manifest/config/source-like text used for technology inference.
    Prose documentation can describe examples or future work, so it is not
    used for framework/tool/database conclusions."""
    out = {}
    for rel, text in texts.items():
        lower = os.path.basename(rel).lower()
        if lower.startswith("readme") or lower.endswith((".md", ".rst")):
            continue
        out[rel] = text
    return out
