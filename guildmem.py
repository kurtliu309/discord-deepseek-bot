#!/usr/bin/env python3
"""Per-guild memory: profile blocks + nightly recaps (see docs/notes/design-v3.md §3).

    DATA/guilds/<guild id>/
      profile/  kurt.md        hand-written by Kurt, the bot only reads it
                threads.md   <= 600 chars  (bot rewrites nightly)
                people.md    <= 600
                jokes.md     <= 400
                unfinished.md<= 400
                drift.md     <= 300
      recaps/   YYYY-MM-DD.md  newest KEEP_RECAPS files only

Blocks are separate so the nightly rewrite can compress one section at a time and
the chat path can read only the blocks its feature is registered for. The token
estimate is deliberately rough (``len(text) / 3.5``); when a feature's blocks don't
fit its budget they are truncated proportionally, block by block.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

CHARS_PER_TOKEN = 3.5

PROFILE_LIMITS = {
    "threads": 600,
    "people": 600,
    "jokes": 400,
    "unfinished": 400,
    "drift": 300,
}
READONLY_BLOCKS = ("kurt",)
ALL_BLOCKS = ("kurt", "threads", "people", "jokes", "unfinished", "drift")
BOT_BLOCKS = tuple(b for b in ALL_BLOCKS if b not in READONLY_BLOCKS)
KEEP_RECAPS = 10

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def estimate_tokens(text: str) -> float:
    return len(text or "") / CHARS_PER_TOKEN


def truncate_to_tokens(text: str, max_tokens: float) -> str:
    """Cut text down to ~``max_tokens``, preferring a line boundary."""
    text = text or ""
    if max_tokens <= 0:
        return ""
    limit = int(max_tokens * CHARS_PER_TOKEN)
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip()


def block_limit(name: str) -> int | None:
    """Character limit for a block; ``kurt`` has none (Kurt writes it)."""
    if name not in ALL_BLOCKS:
        raise KeyError(f"unknown profile block {name!r}")
    return PROFILE_LIMITS.get(name)


def fits(name: str, text: str) -> bool:
    limit = block_limit(name)
    return limit is None or len(text or "") <= limit


def enforce_block(name: str, text: str) -> str:
    """Truncate a bot-written block to its limit (the last resort after re-compressing)."""
    limit = block_limit(name)
    text = (text or "").strip()
    if limit is None or len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _fit_to_budget(items, token_budget: float):
    """Scale each (label, text) block down so the total fits; long blocks lose more."""
    total = sum(estimate_tokens(text) for _, text in items)
    if token_budget <= 0:
        return []
    if total <= token_budget or not items:
        return items
    share = token_budget / total
    return [
        (label, truncate_to_tokens(text, max(1.0, estimate_tokens(text) * share)))
        for label, text in items
    ]


class GuildMemory:
    """Everything under one guild's folder: config lives beside it, not in here."""

    def __init__(self, guild_dir):
        self.root = Path(guild_dir)
        self.profile_dir = self.root / "profile"
        self.recaps_dir = self.root / "recaps"

    # -- setup -------------------------------------------------------------- #

    def ensure(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.recaps_dir.mkdir(parents=True, exist_ok=True)

    # -- profile blocks ----------------------------------------------------- #

    def block_path(self, name: str) -> Path:
        block_limit(name)  # validates the name
        return self.profile_dir / f"{name}.md"

    def read_block(self, name: str) -> str:
        path = self.block_path(name)
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def read_blocks(self, names) -> dict[str, str]:
        return {name: self.read_block(name) for name in names}

    def write_block(self, name: str, text: str, truncate: bool = True) -> str:
        """Write one block; returns the text actually written.

        ``kurt`` is read-only for the bot. Bot blocks are truncated to their
        character limit unless ``truncate=False`` (used by the migration script).
        """
        if name in READONLY_BLOCKS:
            raise PermissionError(f"{name}.md is hand-written; the bot must not change it")
        text = (text or "").strip()
        if truncate:
            text = enforce_block(name, text)
        _atomic_write(self.block_path(name), text + "\n")
        return text

    def seed_kurt(self, text: str) -> bool:
        """Create kurt.md from the v2 profile once; never touches an existing one."""
        path = self.profile_dir / "kurt.md"
        if path.exists():
            return False
        _atomic_write(path, (text or "").strip() + "\n")
        return True

    def oversized(self, text_by_block: dict[str, str]) -> list[str]:
        """Names of bot blocks over their limit (the nightly job re-compresses them)."""
        return [n for n, t in text_by_block.items() if n not in READONLY_BLOCKS and not fits(n, t)]

    def profile_text(self, parts, token_budget: float, recent_recaps: int = 0) -> str:
        """Read the registered blocks (plus N recent recaps) inside one token budget."""
        items = []
        for name in parts:
            text = self.read_block(name)
            if text:
                items.append((name, text))
        for date, text in self.recent_recaps(recent_recaps):
            if text:
                items.append((f"recap {date}", text))
        fitted = _fit_to_budget(items, token_budget)
        return "\n\n".join(f"=== {label} ===\n{text}" for label, text in fitted if text)

    # -- recaps ------------------------------------------------------------- #

    def recent_recaps(self, n: int = 3) -> list[tuple[str, str]]:
        """Newest ``n`` recaps as (date, text), oldest first."""
        if n <= 0:
            return []
        out = []
        for path in sorted(self.recaps_dir.glob("*.md"), key=lambda p: p.stem)[-n:]:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            out.append((path.stem, text))
        return out

    def save_recap(self, date: str, text: str) -> Path:
        if not DATE_RE.match(date or ""):
            raise ValueError(f"recap date must be YYYY-MM-DD, got {date!r}")
        path = self.recaps_dir / f"{date}.md"
        _atomic_write(path, (text or "").strip() + "\n")
        self.prune_recaps()
        return path

    def prune_recaps(self, keep: int = KEEP_RECAPS) -> list[str]:
        """Delete all but the newest ``keep`` recaps; returns the deleted dates."""
        paths = sorted(self.recaps_dir.glob("*.md"), key=lambda p: p.stem)
        removed = []
        for path in paths[:-keep] if keep > 0 else paths:
            try:
                path.unlink()
                removed.append(path.stem)
            except OSError:
                pass
        return removed
