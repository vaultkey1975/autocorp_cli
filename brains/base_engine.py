#!/usr/bin/env python3
"""
Base Engine  (AutoCorp CLI - brains)  [Claude CLI Integration Phase 1]
=====================================================================

The engine abstraction. A code-generation engine takes a prompt (and an optional
system instruction) and returns generated text. The Builder talks ONLY to this
interface, so the underlying model - a local Ollama model or the Claude CLI - can
be swapped without changing the Builder, Planner, Tester, Fix Loop, or pipeline.

Contract:
    engine.generate(prompt) -> str          # minimal form (per spec)
    engine.generate(prompt, system) -> str   # optional system instruction

`system` is optional so the LOCAL engine can preserve its exact current behaviour
(Ollama treats the system prompt separately from the user prompt). Engines that
have no separate system channel may fold it into the prompt.

Engines raise EngineError on failure; the Builder catches it, logs a clean
message, and continues (it never crashes the build).
"""

from abc import ABC, abstractmethod


class EngineError(RuntimeError):
    """Raised by an engine when generation fails (model down, CLI missing, ...)."""


class BaseEngine(ABC):
    name = "base"

    @abstractmethod
    def generate(self, prompt: str, system: str = "") -> str:
        """Return generated text for `prompt`. Subclasses must implement this."""
        raise NotImplementedError

    def available(self) -> bool:
        """Whether this engine is usable right now. The default is True (e.g. the
        local Ollama engine is always considered reachable; transport errors
        surface at generate() time). Engines with an external dependency - like
        the Claude CLI - override this to report a missing binary up front."""
        return True

    # Phase 2B: real provider-reported usage ({"input_tokens", "output_tokens",
    # "source"}) from the MOST RECENT generate() call, captured as a side
    # effect by engines whose transport reports it (LocalEngine, DeepSeekEngine
    # in API mode - both via a real tokenizer/API-reported count, never a
    # byte-based estimate). Stays None for engines with no usage channel (e.g.
    # the Claude CLI) and for any generate() call a test has monkeypatched
    # away - callers must treat a stale/None value as "usage unavailable",
    # never guess. Deliberately NOT a method: generate() stays the single
    # call every existing caller and test mocks, so usage capture can never
    # silently bypass a mocked generate() and reach a real transport.
    last_usage: dict | None = None

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{self.__class__.__name__} name={self.name!r}>"
