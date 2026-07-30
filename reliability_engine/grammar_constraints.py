#!/usr/bin/env python3
"""Patch-output grammar constraints.

Ollama does not currently expose a first-party GBNF parameter through the local
client used by AutoCorp, so this module keeps the GBNF grammar explicit and
enforces the same output shape before a patch can enter the apply/test loop.
"""

import re


UNIFIED_DIFF_GBNF = r'''
root ::= file-diff+
file-diff ::= "--- " path "\n" "+++ " path "\n" hunk+
hunk ::= "@@ " line-range " " line-range " @@" text-line* "\n" diff-line+
line-range ::= "-" number ("," number)? | "+" number ("," number)?
diff-line ::= (" " | "+" | "-") text-line "\n"
path ::= [^\n]+
text-line ::= [^\n]*
number ::= [0-9]+
'''


def looks_like_unified_diff(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(re.search(r"^--- .+\n\+\+\+ .+\n@@ .+ @@", text, re.MULTILINE))


def strip_markdown_fences(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if not lines or not lines[0].strip().startswith("```"):
        return text
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip() + "\n"


def validate_builder_patch(text: str) -> str:
    candidate = strip_markdown_fences(text)
    if not looks_like_unified_diff(candidate):
        raise ValueError("builder output is not a unified diff")
    for line in candidate.splitlines():
        if line.startswith("```"):
            raise ValueError("builder output must not contain markdown fences")
    return candidate.rstrip() + "\n"


def prompt_suffix() -> str:
    return (
        "Return one valid unified diff only. Do not use markdown fences. "
        "The output is constrained by this GBNF grammar:\n" + UNIFIED_DIFF_GBNF
    )
