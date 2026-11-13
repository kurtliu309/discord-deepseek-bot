#!/usr/bin/env python3
"""One-off v2 -> v3 migration (docs/notes/design-v3.md §6).

Turns the old flat, env-configured layout into one folder per guild:

    DATA/guilds/<guild id>/config.yaml     built from the env vars below
    DATA/guilds/<guild id>/profile/*.md    split out of group-profile.md
    DATA/guilds/<guild id>/recaps/*.md     moved from DATA/recaps/
    DATA/guilds/dm-owner/config.yaml       DMs: owner trust, chat only

``group-profile.md`` and ``recaps/`` are renamed to ``*.v2.bak``, never deleted.
Running it twice is safe: an existing config.yaml is left alone (``--force`` rewrites
it), existing profile blocks are kept, and recaps already copied are not copied again.

Settings read from the environment (non-secret only; the .env file is never opened):
BLOCKED_CHANNEL_IDS, POST_CHANNEL_ID, SUMMARY_TIME, TOPICS_TIME, TOPICS_COUNT,
REPLY_BUDGET, BUDGET_RESET_TIME, RANDOM_CHANCE, FX_TOPIC_CHANCE, BURST_COUNT,
BURST_WINDOW_SECONDS, COOLDOWN_SECONDS.

Usage:
  migrate_v3.py --data data-staging --guild-id 1317847113559965736 --guild-name 頭腦警察
  migrate_v3.py ... --only-channel 1570775686862471301   # staging: talk in one channel
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

import guildcfg
from guildcfg import (
    BudgetSettings,
    Features,
    GuildConfig,
    InterjectSettings,
    RecapSettings,
    TopicsSettings,
    Trust,
)
from guildmem import GuildMemory, READONLY_BLOCKS, block_limit, fits

GUILD_ID_RE = re.compile(r"^\d+$")

# v2 group-profile.md headings -> new profile block file names
PROFILE_HEADINGS = (
    ("kurt (hand-written", "kurt"),
    ("main threads", "threads"),
    ("people", "people"),
    ("running jokes", "jokes"),
    ("unfinished threads", "unfinished"),
    ("recent drift", "drift"),
)

LOOKUPS_PER_DAY = 60  # reserved for v3.1 tools; stored now so the config is complete


def log(message: str) -> None:
    print(f"[migrate] {message}", flush=True)


# --------------------------------------------------------------------------- #
# env (fake values in tests; the real .env is passed as --env-file by the operator)
# --------------------------------------------------------------------------- #


def _env_int(env, name, default):
    raw = (env.get(name) or "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log(f"warning: {name}={raw!r} is not an integer, using {default}")
        return default


def _env_float(env, name, default):
    raw = (env.get(name) or "").strip()
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        log(f"warning: {name}={raw!r} is not a number, using {default}")
        return default


def _env_time(env, name, default):
    raw = (env.get(name) or "").strip() or default
    if not guildcfg.TIME_RE.match(raw):
        log(f"warning: {name}={raw!r} is not HH:MM, using {default}")
        return default
    return raw


def _env_ids(env, name) -> list[int]:
    ids = []
    for part in (env.get(name) or "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            log(f"warning: {name} has a non-numeric id {part!r}, ignoring it")
            continue
        ids.append(int(part))
    return ids


def _env_channel(env, name):
    ids = _env_ids(env, name)
    return ids[0] if ids else None


# --------------------------------------------------------------------------- #
# profile splitting
# --------------------------------------------------------------------------- #


def split_profile(text: str) -> dict[str, str]:
    """Split a v2 group-profile.md into {block name: markdown body}."""
    blocks: dict[str, list[str]] = {}
    current = None
    for line in (text or "").splitlines():
        if line.lstrip().startswith("#"):
            heading = line.lstrip("#").strip().lower()
            current = None
            for prefix, name in PROFILE_HEADINGS:
                if heading.startswith(prefix):
                    current = name
                    blocks.setdefault(name, [])
                    break
        elif current is not None:
            blocks[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in blocks.items()}


def _backup(path: Path) -> Path:
    """Rename ``path`` to ``path.v2.bak`` once; keep an existing backup untouched."""
    bak = path.with_name(path.name + ".v2.bak")
    if not bak.exists():
        path.rename(bak)
    return bak


def migrate_profile(data_dir: Path, guild_dir: Path) -> dict[str, int]:
    """Write profile/ blocks from group-profile.md and back the original up."""
    source = data_dir / "group-profile.md"
    memory = GuildMemory(guild_dir)
    memory.ensure()
    sizes: dict[str, int] = {}
    if not source.exists():
        for name in READONLY_BLOCKS:
            text = memory.read_block(name)
            if text:
                sizes[name] = len(text)
        return sizes

    for name, body in split_profile(source.read_text(encoding="utf-8")).items():
        if name in READONLY_BLOCKS:
            memory.seed_kurt(body)
        elif body and not memory.block_path(name).exists():
            memory.write_block(name, body, truncate=False)
        text = memory.read_block(name)
        sizes[name] = len(text)
        if not fits(name, text):
            log(f"warning: profile/{name}.md is {len(text)} chars, over the "
                f"{block_limit(name)}-char block limit; the next nightly rewrite will compress it")
    _backup(source)
    log(f"split group-profile.md -> profile/ ({', '.join(f'{n}={len(memory.read_block(n))}' for n in sizes)})")
    return sizes


def migrate_recaps(data_dir: Path, guild_dir: Path) -> list[str]:
    """Copy recaps into the guild folder, then back the old recaps/ folder up."""
    source = data_dir / "recaps"
    memory = GuildMemory(guild_dir)
    memory.ensure()
    copied = []
    if source.is_dir():
        for path in sorted(source.glob("*.md")):
            dest = memory.recaps_dir / path.name
            if not dest.exists():
                shutil.copy2(path, dest)
                copied.append(path.stem)
        if copied:
            log(f"moved {len(copied)} recap(s) into guilds/{guild_dir.name}/recaps/")
        _backup(source)
    else:
        existing = [p.stem for p in memory.recaps_dir.glob("*.md")]
        if existing:
            log(f"recaps already in place ({len(existing)} file(s)); old recaps/ not found")
    memory.prune_recaps()
    return copied


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


def build_config(env, guild_name: str, only_channel=None) -> GuildConfig:
    only = int(only_channel) if only_channel else None
    post = _env_channel(env, "POST_CHANNEL_ID")
    channel = only if only is not None else post
    if channel is None:
        raise ValueError("POST_CHANNEL_ID is not set; pass --only-channel or set POST_CHANNEL_ID")

    return GuildConfig(
        name=guild_name,
        trust=Trust.OWNER,
        consent=True,
        persona="tiredfx",
        style="normal",
        language="en",
        budget=BudgetSettings(
            replies_per_day=_env_int(env, "REPLY_BUDGET", 100),
            lookups_per_day=LOOKUPS_PER_DAY,
            reset_time=_env_time(env, "BUDGET_RESET_TIME", "09:00"),
        ),
        allowed_channels=[only] if only is not None else None,
        quiet_channels=_env_ids(env, "BLOCKED_CHANNEL_IDS"),
        recap_skip_channels=[],
        features=Features(
            interject=InterjectSettings(
                chance=_env_float(env, "RANDOM_CHANCE", 0.03),
                fx_chance=_env_float(env, "FX_TOPIC_CHANCE", 0.05),
                burst_count=_env_int(env, "BURST_COUNT", 12),
                burst_window=_env_int(env, "BURST_WINDOW_SECONDS", 180),
                cooldown=_env_int(env, "COOLDOWN_SECONDS", 600),
            ),
            recap=RecapSettings(
                on=True,
                channel=channel,
                time=_env_time(env, "SUMMARY_TIME", "22:00"),
            ),
            topics=TopicsSettings(
                on=True,
                channel=channel,
                time=_env_time(env, "TOPICS_TIME", "09:00"),
                count=_env_int(env, "TOPICS_COUNT", 3),
            ),
        ),
    )


def write_guild_config(guild_dir: Path, cfg: GuildConfig, force: bool = False) -> bool:
    """Write config.yaml unless one exists (``force`` rewrites). True when written."""
    path = guild_dir / "config.yaml"
    if path.exists() and not force:
        log(f"config.yaml already exists, leaving it alone: {path}")
        return False
    guildcfg.write_config(path, cfg)
    log(f"wrote {path}")
    return True


def ensure_dm_config(guilds_dir: Path) -> None:
    path = guilds_dir / guildcfg.DM_GUILD_ID / "config.yaml"
    if path.exists():
        return
    guildcfg.write_config(path, guildcfg.dm_config())
    log(f"wrote {path}")


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #


def run(
    data_dir,
    guild_id,
    guild_name: str,
    only_channel=None,
    force: bool = False,
    environ=None,
) -> dict:
    """Do the migration; returns a small summary (also used by tests)."""
    env = os.environ if environ is None else environ
    guild_id = str(guild_id)
    if not GUILD_ID_RE.match(guild_id):
        raise ValueError(f"--guild-id must be numeric, got {guild_id!r}")

    data_dir = Path(data_dir)
    guilds_dir = data_dir / "guilds"
    guild_dir = guilds_dir / guild_id
    guild_dir.mkdir(parents=True, exist_ok=True)

    cfg = build_config(env, guild_name, only_channel)
    written = write_guild_config(guild_dir, cfg, force=force)
    sizes = migrate_profile(data_dir, guild_dir)
    recaps = migrate_recaps(data_dir, guild_dir)
    ensure_dm_config(guilds_dir)

    return {
        "guild_dir": guild_dir,
        "config_written": written,
        "profile_chars": sizes,
        "recaps": recaps,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, help="data directory, e.g. data-staging")
    parser.add_argument("--guild-id", required=True)
    parser.add_argument("--guild-name", required=True)
    parser.add_argument(
        "--only-channel",
        type=int,
        default=None,
        help="test stack: only talk in this channel; recap/topics post there too",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing config.yaml")
    args = parser.parse_args(argv)

    try:
        result = run(args.data, args.guild_id, args.guild_name, args.only_channel, args.force)
    except ValueError as e:
        log(f"error: {e}")
        return 2

    log("done")
    for name, size in result["profile_chars"].items():
        log(f"  profile/{name}.md: {size} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
