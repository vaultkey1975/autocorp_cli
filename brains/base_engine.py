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


class EngineError(RuntimeError):
    """Raised by an engine when generation fails (model down, CLI missing, ...)."""


class BaseEngine:
    name = "base"

    def generate(self, prompt: str, system: str = "") -> str:
        """Return generated text for `prompt`. Subclasses must implement this."""
        raise NotImplementedError

    def available(self) -> bool:
        """Whether this engine is usable right now. The default is True (e.g. the
        local Ollama engine is always considered reachable; transport errors
        surface at generate() time). Engines with an external dependency - like
        the Claude CLI - override this to report a missing binary up front."""
        return True

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{self.__class__.__name__} name={self.name!r}>"
