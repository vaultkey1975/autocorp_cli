"""Translates the guided operator's ``output()`` lines into progress events.

Every event here corresponds 1:1 to an ``output(...)`` call already present in
the repaired ``brains.guided_clonecast_episode.run_guided_episode_build``
(commit 08eee7a), so a progress bubble is only ever shown after the matching
backend action has actually completed. Lines that are not in this map (the
listening-gate detail dump, raw studio listings) are recorded for diagnostics
but are not rendered as chat bubbles, to keep raw JSON out of the default view.
"""

from __future__ import annotations

# Exact output() text -> (event_name, friendly chat text)
KNOWN_EVENTS: dict[str, tuple[str, str]] = {
    "Research imported": ("research_imported", "Research imported."),
    "Research validated": ("research_validated", "Research validated."),
    "Research accepted": ("research_accepted", "Research accepted."),
    "Approved script imported": ("approved_script_imported", "Approved script imported and byte-checksum verified."),
    "Voice assigned": ("voice_assignment_verified", "Voice assignment verified."),
    "Audio generation started": ("audio_generation_started", "Audio generation started."),
}

LISTENING_GATE_MARKER = "EPISODE READY FOR OWNER REVIEW"
SPEECH_PREVIEW_MARKER = "SPEECH-ONLY PREVIEW READY"


def classify_output_line(line: str) -> tuple[str | None, str | None]:
    """Return (event_name, friendly_text) for a known line, else (None, None)."""
    text = line.strip()
    if text.startswith("Audio generation still running") or text.startswith("Audio generation is active"):
        return "audio_generation_heartbeat", text + "."
    hit = KNOWN_EVENTS.get(text)
    if hit:
        return hit
    return None, None
