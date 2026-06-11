#!/usr/bin/env python3
"""
DeepSeek Engine  (AutoCorp CLI - brains)  [DeepSeek - dual transport, MANUAL]
===========================================================================

A DUAL-TRANSPORT code-generation engine behind the same BaseEngine seam as
LocalEngine and ClaudeEngine. The transport is chosen ONCE, at construction:

  * API mode  - if a DeepSeek API key is present (constructor arg or the
    DEEPSEEK_API_KEY environment variable), generation POSTs to DeepSeek's
    OpenAI-compatible chat-completions endpoint over HTTPS via `requests`
    (no SDK, no new dependency).
  * Local mode - if NO key is present, behaviour is IDENTICAL to before: a
    DeepSeek model served locally by Ollama via `core.llm.generate`. This path
    is byte-for-byte unchanged so the shadow behaviour is fully preserved.

Key resolution is ENV-ONLY (no .env loading): constructor `api_key` wins,
otherwise `os.environ["DEEPSEEK_API_KEY"]`. `use_api` reflects the resolved mode.

Discovery-only routing is unchanged: the Model Router has NO rule naming
"deepseek", so no build reaches this engine unless a human selects `--engine
deepseek`. API failures are wrapped as EngineError (no silent fallback when a key
is set) and NEVER echo the key. `available()` stays True in both modes (the local
path is always a viable transport).
"""

import os

import requests

from core import llm
from brains.base_engine import BaseEngine, EngineError

# Default local DeepSeek tag served by Ollama. Overridable via env so the shadow
# box can point at whichever deepseek-coder build is pulled, without code edits.
# Self-contained here (not in config) to keep the change to the engine only.
DEEPSEEK_MODEL = os.environ.get("AUTOCORP_DEEPSEEK_MODEL", "deepseek-coder-v2:16b")

# DeepSeek HTTP API (OpenAI-compatible). Base URL + model are env-overridable;
# the key itself is read at construction (env-only, never from a file).
DEEPSEEK_API_URL = os.environ.get(
    "AUTOCORP_DEEPSEEK_API_URL", "https://api.deepseek.com"
)
DEEPSEEK_API_MODEL = os.environ.get("AUTOCORP_DEEPSEEK_API_MODEL", "deepseek-chat")
# Seconds to wait for an API response. Reuses the same default the local path
# honours so the two transports time out comparably.
DEEPSEEK_API_TIMEOUT = int(os.environ.get("AUTOCORP_DEEPSEEK_API_TIMEOUT", "180"))


class DeepSeekEngine(BaseEngine):
    name = "deepseek"

    def __init__(self, model: str = None, temperature: float = 0.2,
                 api_key: str = None, api_model: str = None):
        self.model = model or DEEPSEEK_MODEL
        self.temperature = temperature
        # Env-only key resolution: explicit arg wins, else DEEPSEEK_API_KEY.
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or ""
        self.api_model = api_model or DEEPSEEK_API_MODEL
        self.use_api = bool(self.api_key)

    def generate(self, prompt: str, system: str = "") -> str:
        """Generate via the DeepSeek API when a key is present, otherwise via the
        local Ollama model. Both transports return plain text and wrap failures
        as EngineError so the Builder handles all engines uniformly."""
        if self.use_api:
            return self._generate_api(prompt, system)
        return self._generate_local(prompt, system)

    # ----------------------------------------------------------------------- #
    # Local transport (UNCHANGED - identical to the prior shadow behaviour)
    # ----------------------------------------------------------------------- #
    def _generate_local(self, prompt: str, system: str = "") -> str:
        """Generate via a local DeepSeek model on Ollama. Same parameters
        LocalEngine uses (system + model + temperature)."""
        try:
            return llm.generate(
                prompt,
                system=system,
                model=self.model,
                temperature=self.temperature,
            )
        except llm.OllamaError as e:
            raise EngineError(f"DeepSeek model unavailable: {e}") from e

    # ----------------------------------------------------------------------- #
    # API transport (DeepSeek chat completions, OpenAI-compatible)
    # ----------------------------------------------------------------------- #
    def _generate_api(self, prompt: str, system: str = "") -> str:
        """POST to the DeepSeek chat-completions endpoint and return the message
        content. Every failure mode is wrapped as EngineError WITHOUT including
        the API key (only the key is secret; the URL/model/status are safe)."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            resp = requests.post(
                f"{DEEPSEEK_API_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.api_model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "stream": False,
                },
                timeout=DEEPSEEK_API_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            # str(e) can include the request URL but not headers, so the Bearer
            # key is not exposed. Report the class, not the raw exception, to be
            # certain no secret is interpolated.
            raise EngineError(
                f"DeepSeek API request failed ({type(e).__name__})."
            ) from e

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise EngineError(
                f"DeepSeek API returned an unexpected response ({type(e).__name__})."
            ) from e

        return (content or "").strip()
