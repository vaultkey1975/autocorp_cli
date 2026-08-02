"""Read-only CloneCast adapter used by the web app to build friendly UI.

This module never drives the guided episode workflow itself — that remains
the exclusive job of ``brains.guided_clonecast_episode.run_guided_episode_build``,
reused as-is. It only exposes the small set of read-only lookups (studios,
voice profiles, config-check) the chat UI needs to turn internal IDs into
friendly labels, plus a resolver used to normalize a saved/typed studio value
without ever running it through a shell.

Every CloneCast command this module can invoke is named explicitly below;
nothing here accepts an arbitrary command name from a caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brains.guided_clonecast_episode import CloneCastCLI, EpisodeBuildError

READ_ONLY_ALLOWLIST = frozenset({"config-check", "radio-studio-list", "voice-list"})


class AmbiguousSelectionError(ValueError):
    """Raised when a friendly-name lookup matches more than one item."""


@dataclass(frozen=True)
class Studio:
    studio_id: str
    display_name: str
    lifecycle_status: str


@dataclass(frozen=True)
class VoiceProfile:
    voice_profile_id: str
    display_name: str
    lifecycle_status: str
    stable_name: str
    version_label: str | None
    version_number: int | None

    @property
    def label(self) -> str:
        base = f"{self.display_name} — {self.lifecycle_status.title()}"
        return base


class CloneCastClient:
    def __init__(self, repo_path: str, *, cli: CloneCastCLI | None = None):
        self.repo_path = str(Path(repo_path).expanduser())
        self._cli = cli or CloneCastCLI(Path(self.repo_path))

    def _run(self, args: list[str]) -> Any:
        if args[0] not in READ_ONLY_ALLOWLIST:
            raise EpisodeBuildError(f"CloneCast operation is not on the read-only allowlist: {args[0]}")
        result = self._cli.checked(args)
        return result.json_data

    def config_check(self) -> dict[str, Any]:
        return self._run(["config-check"]) or {}

    def list_studios(self) -> list[Studio]:
        data = self._run(["radio-studio-list"]) or []
        studios = []
        for item in data:
            studios.append(
                Studio(
                    studio_id=str(item.get("studio_id")),
                    display_name=str(item.get("display_name") or item.get("stable_name") or "Untitled"),
                    lifecycle_status=str(item.get("lifecycle_status") or "unknown"),
                )
            )
        return studios

    def list_voices(self, *, status: str | None = None) -> list[VoiceProfile]:
        args = ["voice-list"]
        if status:
            args += ["--status", status]
        data = self._run(args) or []
        voices = []
        for item in data:
            voices.append(
                VoiceProfile(
                    voice_profile_id=str(item.get("voice_profile_id")),
                    display_name=str(item.get("display_name") or item.get("stable_name") or "Untitled"),
                    lifecycle_status=str(item.get("lifecycle_status") or "unknown"),
                    stable_name=str(item.get("stable_name") or ""),
                    version_label=item.get("version_label"),
                    version_number=item.get("version_number"),
                )
            )
        return voices


def disambiguate_voice_labels(voices: list[VoiceProfile]) -> dict[str, str]:
    """Map voice_profile_id -> a UI label, adding version info only for
    display names that collide so approved duplicates stay distinguishable."""
    counts: dict[str, int] = {}
    for v in voices:
        counts[v.display_name] = counts.get(v.display_name, 0) + 1
    labels = {}
    for v in voices:
        if counts[v.display_name] > 1:
            suffix = v.version_label or v.stable_name or v.voice_profile_id[:12]
            labels[v.voice_profile_id] = f"{v.display_name} — {v.lifecycle_status.title()} ({suffix})"
        else:
            labels[v.voice_profile_id] = v.label
    return labels


def resolve_studio(value: str, studios: list[Studio]) -> Studio:
    """Normalize a raw saved/typed studio value that may be an ID, a
    'ID: Display Name' pair (the historical malformed input), or a bare
    display name, into a single unambiguous Studio."""
    text = (value or "").strip()
    if not text:
        raise ValueError("studio selection is required")
    if ":" in text:
        text = text.split(":", 1)[0].strip()
    by_id = {s.studio_id: s for s in studios}
    if text in by_id:
        return by_id[text]
    matches = [s for s in studios if s.display_name.casefold() == text.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousSelectionError(f"multiple studios are named {text!r}; select one by ID")
    raise ValueError(f"no studio matches {value!r}")


def resolve_voice(value: str, voices: list[VoiceProfile]) -> VoiceProfile:
    text = (value or "").strip()
    if not text:
        raise ValueError("voice selection is required")
    by_id = {v.voice_profile_id: v for v in voices}
    if text in by_id:
        return by_id[text]
    matches = [v for v in voices if v.display_name.casefold() == text.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousSelectionError(f"multiple voices are named {text!r}; select one by profile ID")
    raise ValueError(f"no voice matches {value!r}")
