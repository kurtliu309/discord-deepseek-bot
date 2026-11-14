#!/usr/bin/env python3
"""Persona library: build the system prompt from files under ``data/shared/personas/``.

    <personas_dir>/
      _base.md               the floor everyone shares (Kurt edits, bot never rewrites)
      <persona>/character.md the personality itself
      <persona>/persona.yaml optional numbers (jump-in chance/cooldown, typing speed)
      _styles/<style>.md     typing style: normal (one short message) or chatty (split sends)

Assembly order (see docs/notes/design-v3.md §8):
  _base -> character -> style -> language line -> extra_prompt -> date/time + <@ID> lines
The date/time and <@ID> lines stay in code so every caller gets them right; the
MENTION/DOC/UNTRUSTED addenda stay with the feature that needs them (bot.py).

Persona files are read through an mtime cache: edits are picked up without a restart.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

BASE_FILE = "_base.md"
CHARACTER_FILE = "character.md"
TRAITS_FILE = "persona.yaml"
STYLES_DIR = "_styles"

# persona.yaml is data written outside the code, so every number is clamped.
TRAIT_MIN, TRAIT_MAX = 0.2, 3.0  # chance_scale, fx_multiplier, cooldown_scale
BURST_MIN, BURST_MAX = 0.5, 3.0  # interject.burst_scale
SPEED_MIN, SPEED_MAX = 0.5, 2.0  # typing.speed

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


@dataclass(frozen=True)
class PersonaTraits:
    """The optional numbers a persona may set for itself.

    Every field is independent: ``chance_scale`` always multiplies the base chance
    (1.0 = no change), and ``fx_multiplier`` is only used on FX talk. ``fx_multiplier``
    stays ``None`` when the persona file doesn't provide one, which means FX talk gets
    no bonus (just the scaled base chance).
    """

    chance_scale: float = 1.0
    fx_multiplier: float | None = None
    cooldown_scale: float = 1.0
    burst_scale: float = 1.0
    typing_speed: float = 1.0

    @property
    def has_fx_multiplier(self) -> bool:
        """Whether the persona file actually set ``fx_multiplier``."""
        return self.fx_multiplier is not None


# path -> (mtime_ns, text)
_cache: dict[str, tuple[int, str]] = {}
# path -> (mtime_ns, traits)
_traits_cache: dict[str, tuple[int, PersonaTraits]] = {}


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
    _traits_cache.clear()


def _trait_number(value, default, low: float, high: float):
    """A finite number clamped into ``[low, high]``; anything else returns ``default``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


def _parse_traits(path: Path) -> PersonaTraits:
    """Read ``persona.yaml``; a missing, broken or wrong-typed file means defaults."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return PersonaTraits()
    if not isinstance(data, dict):
        return PersonaTraits()
    interject = data.get("interject")
    interject = interject if isinstance(interject, dict) else {}
    typing = data.get("typing")
    typing = typing if isinstance(typing, dict) else {}
    return PersonaTraits(
        chance_scale=_trait_number(interject.get("chance_scale"), 1.0, TRAIT_MIN, TRAIT_MAX),
        # Absent or invalid = no persona opinion, so FX talk gets no bonus.
        fx_multiplier=_trait_number(interject.get("fx_multiplier"), None, TRAIT_MIN, TRAIT_MAX),
        cooldown_scale=_trait_number(interject.get("cooldown_scale"), 1.0, TRAIT_MIN, TRAIT_MAX),
        burst_scale=_trait_number(interject.get("burst_scale"), 1.0, BURST_MIN, BURST_MAX),
        typing_speed=_trait_number(typing.get("speed"), 1.0, SPEED_MIN, SPEED_MAX),
    )


def load_traits(personas_dir, persona: str = "tiredfx") -> PersonaTraits:
    """The persona's optional numbers, read through the same mtime cache as its text.

    No ``persona.yaml`` is normal: it returns the defaults (no scaling) and never raises.
    """
    path = Path(personas_dir) / persona / TRAITS_FILE
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _traits_cache.pop(key, None)
        return PersonaTraits()
    hit = _traits_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    traits = _parse_traits(path)
    _traits_cache[key] = (mtime, traits)
    return traits


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


def typing_delay(chunk: str, speed: float = 1.0) -> float:
    """Fake typing time per outgoing message: ~1s per 20 chars, clamped to 1.2-4s.

    ``speed`` is the persona's typing multiplier (bigger = faster): the delay is
    divided by it and the result stays inside 0.6-8s. The default is unchanged.
    """
    base = max(1.2, min(4.0, len(chunk or "") / 20))
    if not isinstance(speed, (int, float)) or isinstance(speed, bool):
        speed = 1.0
    elif not math.isfinite(speed) or speed <= 0:
        speed = 1.0
    return max(0.6, min(8.0, base / speed))
