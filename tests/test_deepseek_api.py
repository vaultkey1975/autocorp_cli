#!/usr/bin/env python3
"""
DeepSeek API engine tests  (AutoCorp CLI - DeepSeek API RED, MANUAL)
===================================================================

Drives the design of DUAL-TRANSPORT DeepSeekEngine: the same engine, selected
at construction, either calls the DeepSeek HTTP API (when an API key is present)
or falls back to the EXISTING local Ollama path (when it is not). The engine is
still only reachable via a manual `--engine deepseek`; the Model Router is
unchanged and routes nothing here.

Design intent these tests pin:
  * `DeepSeekEngine(api_key=...)` (or env DEEPSEEK_API_KEY) flips the engine into
    API mode; `use_api` reflects the mode.
  * API mode POSTs to DeepSeek's OpenAI-compatible chat-completions endpoint via
    `requests` (no SDK, no new dependency), with a Bearer auth header, sending
    system+user messages, and returns choices[0].message.content.
  * No key -> the local path is UNCHANGED (delegates to core.llm.generate).
  * API failures raise EngineError (no silent fallback when a key is set), and
    the error never echoes the secret.

These tests are RED on purpose: today's DeepSeekEngine.__init__ accepts only
(model, temperature), so `api_key=`/`use_api`/`api_model` do not exist yet, and
there is no HTTP path. Fully offline: `requests.post` and `core.llm.generate`
are patched, so no network call and no real key are ever used.
"""

from unittest.mock import patch

import pytest
import requests

from core import llm
from brains.base_engine import EngineError
from brains.deepseek_engine import DeepSeekEngine


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _Resp:
    """Stand-in for a requests.Response from the chat-completions endpoint."""

    def __init__(self, content="API-OUT", status_ok=True):
        self._content = content
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise requests.HTTPError("500 Server Error")

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _no_env_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Mode selection
# --------------------------------------------------------------------------- #
def test_api_key_param_enables_api_mode(monkeypatch):
    _no_env_key(monkeypatch)
    assert DeepSeekEngine(api_key="sk-test").use_api is True


def test_blank_api_key_means_local_mode(monkeypatch):
    _no_env_key(monkeypatch)
    assert DeepSeekEngine(api_key="").use_api is False


def test_env_var_enables_api_mode(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert DeepSeekEngine().use_api is True


def test_api_model_defaults_to_deepseek_chat(monkeypatch):
    _no_env_key(monkeypatch)
    assert DeepSeekEngine(api_key="sk-test").api_model == "deepseek-chat"


# --------------------------------------------------------------------------- #
# API transport (patches requests.post - no network)
# --------------------------------------------------------------------------- #
def test_api_mode_posts_to_deepseek_endpoint(monkeypatch):
    _no_env_key(monkeypatch)
    with patch("requests.post", return_value=_Resp()) as post:
        DeepSeekEngine(api_key="sk-test").generate("p")
    url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs.get("url")
    headers = post.call_args.kwargs.get("headers", {})
    assert "deepseek" in url.lower()
    assert any("Bearer" in str(v) for v in headers.values())


def test_api_mode_does_not_call_local_generate(monkeypatch):
    _no_env_key(monkeypatch)
    with patch("requests.post", return_value=_Resp()), \
         patch("core.llm.generate", return_value="LOCAL") as local:
        out = DeepSeekEngine(api_key="sk-test").generate("p")
    assert out != "LOCAL"
    local.assert_not_called()


def test_api_sends_system_and_user_messages(monkeypatch):
    _no_env_key(monkeypatch)
    with patch("requests.post", return_value=_Resp()) as post:
        DeepSeekEngine(api_key="sk-test").generate("THE PROMPT", system="THE SYSTEM")
    payload = post.call_args.kwargs.get("json", {})
    roles = [m.get("role") for m in payload.get("messages", [])]
    assert "system" in roles
    assert "user" in roles


def test_api_returns_message_content(monkeypatch):
    _no_env_key(monkeypatch)
    with patch("requests.post", return_value=_Resp(content="  hello \n")):
        assert DeepSeekEngine(api_key="sk-test").generate("p") == "hello"


# --------------------------------------------------------------------------- #
# Local fallback lock (key absent -> unchanged Ollama path)
# --------------------------------------------------------------------------- #
def test_local_mode_still_uses_llm_generate(monkeypatch):
    _no_env_key(monkeypatch)
    with patch("core.llm.generate", return_value="LOCAL") as local, \
         patch("requests.post") as post:
        out = DeepSeekEngine(api_key="").generate("p")
    assert out == "LOCAL"
    local.assert_called_once()
    post.assert_not_called()


# --------------------------------------------------------------------------- #
# Failure handling + secret hygiene
# --------------------------------------------------------------------------- #
def test_api_failure_raises_engine_error(monkeypatch):
    _no_env_key(monkeypatch)
    with patch("requests.post", side_effect=requests.RequestException("boom")):
        with pytest.raises(EngineError):
            DeepSeekEngine(api_key="sk-test").generate("p")


def test_api_error_message_excludes_api_key(monkeypatch):
    _no_env_key(monkeypatch)
    secret = "sk-super-secret-123"
    with patch("requests.post", side_effect=requests.RequestException("boom")):
        with pytest.raises(EngineError) as ei:
            DeepSeekEngine(api_key=secret).generate("p")
    assert secret not in str(ei.value)
