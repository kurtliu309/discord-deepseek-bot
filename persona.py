#!/usr/bin/env python3
"""Persona library: build the system prompt from files under ``data/shared/personas/``.

    <personas_dir>/
      _base.md               the floor everyone shares (Kurt edits, bot never rewrites)
      <persona>/character.md the personality itself
      _styles/<style>.md     typing style: normal (one short message) or chatty (split sends)

Assembly order (see docs/notes/design-v3.md §8):
  _base -> character -> style -> language line -> extra_prompt -> date/time + <@ID> lines
The date/time and <@ID> lines stay in code so every caller gets them right; the
MENTION/DOC/UNTRUSTED addenda stay with the feature that needs them (bot.py).

Persona files are read through an mtime cache: edits are picked up without a restart.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

BASE_FILE = "_base.md"
CHARACTER_FILE = "character.md"
STYLES_DIR = "_styles"

TURN_MARKER = "[[next]]"
MAX_TURNS = 5  # chatty style: at most 5 messages in a row

LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
}

TIME_LINES = (
    "Today's date is {date}. The current time is {time}.\n"
    "User messages are prefixed with their Discord ID as <@ID>. "
    "Use that format to mention or call out users."
)

# path -> (mtime_ns, text)
_cache: dict[str, tuple[int, str]] = {}


def _read(path: Path) -> str:
    """Read a UTF-8 file, reusing the cached text while its mtime is unchanged."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _cache.pop(key, None)
        return ""
    hit = _cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    _cache[key] = (mtime, text)
    return text


def clear_cache() -> None:
    _cache.clear()


def language_line(language: str) -> str:
    code = (language or "en").strip().lower()
    name = LANGUAGE_NAMES.get(code, language or "en")
    return (
        f"Default to {name}. If someone writes in another language, "
        "answer in that language with the same vibe."
    )


def time_lines(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now().astimezone()
    return TIME_LINES.format(
        date=now.strftime("%B %d %Y"), time=now.strftime("%H:%M %Z").strip()
    )


def build_system_prompt(
    personas_dir,
    persona: str = "tiredfx",
    style: str = "normal",
    language: str = "en",
    extra_prompt: str = "",
    now: dt.datetime | None = None,
) -> str:
    """Full persona part of the system prompt.

    Raises FileNotFoundError when the chosen persona's ``character.md`` is missing
    (a config mistake worth seeing); a missing style file is simply skipped.
    """
    root = Path(personas_dir)
    character_path = root / persona / CHARACTER_FILE
    if not character_path.is_file():
        raise FileNotFoundError(f"persona {persona!r}: {character_path} not found")

    parts = []
    base = _read(root / BASE_FILE)
    if base:
        parts.append(base)
    parts.append(_read(character_path))
    style_text = _read(root / STYLES_DIR / f"{style}.md")
    if style_text:
        parts.append(style_text)
    parts.append(language_line(language))
    if (extra_prompt or "").strip():
        parts.append(extra_prompt.strip())
    parts.append(time_lines(now))
    return "\n\n".join(parts)


def split_turns(text: str, max_turns: int = MAX_TURNS) -> list[str]:
    """Split model output on lines containing ``[[next]]``; extras merge into the last."""
    parts = [p.strip() for p in (text or "").split(TURN_MARKER)]
    parts = [p for p in parts if p]
    if max_turns > 0 and len(parts) > max_turns:
        parts = parts[: max_turns - 1] + ["\n\n".join(parts[max_turns - 1:])]
    return parts


def typing_delay(chunk: str) -> float:
    """Fake typing time per outgoing message: ~1s per 20 chars, clamped to 1.2-4s."""
    return max(1.2, min(4.0, len(chunk or "") / 20))
