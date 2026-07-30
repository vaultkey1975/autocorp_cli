#!/usr/bin/env python3
"""Environment cleanup for subprocesses that validate generated code."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Mapping


ORCHESTRATION_ONLY_ENV_VARS = frozenset({
    "AUTOCORP_MODEL",
})


def clean_subprocess_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment suitable for running the code under validation."""
    env = dict(os.environ if base is None else base)
    for key in ORCHESTRATION_ONLY_ENV_VARS:
        env.pop(key, None)
    return env


@contextlib.contextmanager
def isolated_test_environment() -> Iterator[None]:
    """Temporarily remove orchestration-only env vars for inherited subprocesses."""
    removed: dict[str, str] = {}
    for key in ORCHESTRATION_ONLY_ENV_VARS:
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    try:
        yield
    finally:
        for key, value in removed.items():
            os.environ[key] = value
