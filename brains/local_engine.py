#!/usr/bin/env python3
"""
Local Engine  (AutoCorp CLI - brains)  [Claude CLI Integration Phase 1]
======================================================================

The default engine: local generation via Ollama. This wraps the exact call the
Builder used to make directly (`core.llm.generate` with the same system prompt,
model, and temperature), so behaviour is IDENTICAL to the pre-engine
implementation - this is purely a relocation behind the BaseEngine interface.
"""

from core import llm
from brains.base_engine import BaseEngine, EngineError


class LocalEngine(BaseEngine):
    name = "local"

    def __init__(self, model: str = None, temperature: float = 0.2):
        self.model = model or llm.MODEL
        self.temperature = temperature
        self.last_usage = None

    def generate(self, prompt: str, system: str = "") -> str:
        """Generate via the local Ollama model. Same parameters the Builder used
        before (system + model + temperature). Wraps transport errors as
        EngineError so the Builder handles all engines uniformly.

        Calls `core.llm.generate` (not `generate_with_usage`) - this exact
        call target is pinned by existing tests
        (`tests/test_engines.py::test_local_engine_delegates_prompt_system_and_params`
        et al. patch `core.llm.generate` directly). Ollama does report real
        per-call token counts, but there is no way to fetch them without
        either a second, duplicate model call or changing this call target,
        so `last_usage` intentionally stays unset here - honestly reported
        as unavailable rather than estimated-and-mislabeled or fetched via a
        path a mocked `generate` could silently bypass."""
        try:
            return llm.generate(
                prompt,
                system=system,
                model=self.model,
                temperature=self.temperature,
            )
        except llm.OllamaError as e:
            raise EngineError(f"Local model unavailable: {e}") from e
