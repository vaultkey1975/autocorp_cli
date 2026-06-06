"""Tests for the pure parsing helpers in core/llm (no Ollama / network needed).

strip_code_fences and extract_json are what make the Builder/Tester robust to the
model wrapping output in markdown or reasoning blocks.
"""

import pytest

from core.llm import strip_code_fences, extract_json


def test_strip_code_fences_plain_text_gets_trailing_newline():
    assert strip_code_fences("hello") == "hello\n"


def test_strip_code_fences_unwraps_language_fence():
    assert strip_code_fences("```python\nx = 1\n```") == "x = 1\n"


def test_strip_code_fences_unwraps_bare_fence():
    assert strip_code_fences("```\nfoo\nbar\n```") == "foo\nbar\n"


def test_strip_code_fences_empty():
    assert strip_code_fences("") == ""


def test_extract_json_from_surrounding_prose():
    assert extract_json('here is the result {"a": 1, "b": 2} thanks') == {"a": 1, "b": 2}


def test_extract_json_strips_think_block():
    text = '<think>let me reason {ignore}</think> {"answer": 42}'
    assert extract_json(text) == {"answer": 42}


def test_extract_json_strips_markdown_fence():
    assert extract_json('```json\n{"c": 3}\n```') == {"c": 3}


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError):
        extract_json("there is no object here")
