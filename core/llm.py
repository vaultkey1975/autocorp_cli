#!/usr/bin/env python3
"""
LLM client  (AutoCorp CLI - core)
=================================

A thin, local-only client for Ollama. Every brain talks to the model through
this module, so the call pattern (JSON mode, reasoning/code-fence stripping,
timeouts, error handling) lives in exactly one place.

Pattern mirrors the proven Agent Watchdog engine: POST /api/generate with
stream=False and "format":"json" when structured output is wanted, then pull the
outermost {...} object out of the response.
"""

import json
import re

import requests

from config import MODEL, OLLAMA_URL, REQUEST_TIMEOUT


class OllamaError(RuntimeError):
    """Raised when the Ollama server is unreachable or returns an error."""


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def check_ollama(model: str = MODEL):
    """Return (ok: bool, message: str). Verifies the server is up and the model
    is installed."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        return False, f"Ollama not reachable at {OLLAMA_URL} ({e}). Run `ollama serve`."

    names = [m.get("name", "") for m in resp.json().get("models", [])]
    base = model.split(":")[0]
    if not any(n == model or n.split(":")[0] == base for n in names):
        return False, (f"Model '{model}' is not installed. Pull it with "
                       f"`ollama pull {base}`.")
    return True, f"Ollama up; model '{model}' ready."


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def generate(prompt: str, system: str = "", json_mode: bool = False,
             model: str = MODEL, temperature: float = 0.2) -> str:
    """Send a prompt to Ollama and return the raw text response.

    Raises OllamaError on any transport/HTTP failure so callers can degrade
    gracefully instead of crashing.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system
    if json_mode:
        # Constrain output to a single complete, valid JSON object. Llama 3.2
        # will otherwise occasionally drop a brace.
        payload["format"] = "json"

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate", json=payload, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OllamaError(f"Ollama request failed: {e}") from e

    return resp.json().get("response", "")


# --------------------------------------------------------------------------- #
# JSON extraction
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response.

    Strips <think>...</think> reasoning blocks and markdown code fences, then
    parses the outermost {...}. Raises ValueError if no object is found.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = text.replace("```json", "").replace("```", "")

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in model response.")
    return json.loads(text[start : end + 1])


def generate_json(prompt: str, system: str = "", model: str = MODEL) -> dict:
    """Generate a structured response and parse it into a dict.

    Raises OllamaError (transport) or ValueError/JSONDecodeError (bad output);
    callers decide how to handle each.
    """
    raw = generate(prompt, system=system, json_mode=True, model=model)
    return extract_json(raw)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def strip_code_fences(text: str) -> str:
    """Return code with surrounding ``` fences removed, if present.

    Builders ask the model for raw file contents, but models often wrap them in
    a fenced block anyway. This unwraps a single leading/trailing fence.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text.rstrip() + "\n" if text else text

    lines = stripped.splitlines()
    # Drop the opening fence line (``` or ```python).
    lines = lines[1:]
    # Drop the closing fence line if present.
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).rstrip() + "\n"
