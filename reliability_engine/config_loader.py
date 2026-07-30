#!/usr/bin/env python3
"""Configuration loading for the Reliability Engine."""

import copy
import os


DEFAULT_CONFIG = {
    "model_router": {
        "builder_model": "qwen2.5-coder:14b",
        "fallback_model": "qwen2.5:14b",
    },
    "patch_apply": {"full_file_line_threshold": 150},
    "test_loop": {"max_attempts": 5},
    "self_consistency": {"n_samples": 3, "trigger_blast_radius": 5},
    "worktree_sandbox": {
        "base_branch": "main",
        "cleanup_on_success": True,
        "keep_failure_hours": 24,
    },
    "regression_runner": {"test_framework": "pytest"},
}


def _parse_scalar(value: str):
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def _minimal_yaml(text: str) -> dict:
    data: dict[str, dict[str, object]] = {}
    current = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if not raw_line.startswith(" ") and raw_line.rstrip().endswith(":"):
            current = raw_line.strip()[:-1]
            data[current] = {}
            continue
        if current and raw_line.startswith("  ") and ":" in raw_line:
            key, value = raw_line.strip().split(":", 1)
            data[current][key] = _parse_scalar(value)
            continue
        raise ValueError("unsupported YAML shape without PyYAML installed")
    return data


def _load_yaml(text: str) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        return _minimal_yaml(text)
    return yaml.safe_load(text) or {}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str = "reliability_config.yaml") -> dict:
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_CONFIG)
    with open(path, encoding="utf-8") as fh:
        loaded = _load_yaml(fh.read())
    if not isinstance(loaded, dict):
        raise ValueError("reliability config must be a YAML mapping")
    return _merge(DEFAULT_CONFIG, loaded)
