#!/usr/bin/env python3
"""Ollama model liveness/fallback checking for reliability builds.

Renamed from model_router.py (2026-07-29): this module's job - checking
whether a named Ollama model is actually pulled and reachable, falling back
to a second named model if not - is unrelated to brains/model_router.py's
job (deterministic, rule-based selection between registered *engines*, no
network calls to decide). The two modules previously shared a filename and
a "*Router" class-naming convention despite having nothing in common, which
is a real hazard for a future maintainer even though there was never a live
import collision (each is a fully-qualified subpackage import)."""

from dataclasses import dataclass

from core import llm


@dataclass
class ModelDecision:
    model: str
    fallback_used: bool
    reason: str


class ReliabilityModelRouter:
    def __init__(self, builder_model: str, fallback_model: str):
        self.builder_model = builder_model
        self.fallback_model = fallback_model

    def route(self) -> ModelDecision:
        ok, message = llm.check_ollama(self.builder_model)
        if ok:
            return ModelDecision(self.builder_model, False, message)
        fallback_ok, fallback_message = llm.check_ollama(self.fallback_model)
        if fallback_ok:
            return ModelDecision(
                self.fallback_model,
                True,
                f"{message} Falling back: {fallback_message}",
            )
        raise RuntimeError(f"No configured model is available. {message} {fallback_message}")
