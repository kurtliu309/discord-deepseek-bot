#!/usr/bin/env python3
"""Daily jobs for the Discord DeepSeek bot (stage 1: chat only, no tools).

- summary (SUMMARY_TIME, default 22:00): read the last 24h of messages in every text
  channel of the server the bot can see, plus the last HISTORY_RECAPS recaps the bot itself
  posted (found by RECAP_PREFIX), ask DeepSeek for a recap framed against the group's
  direction, and post it to POST_CHANNEL_ID.
- topics  (TOPICS_TIME, default 09:00): ask DeepSeek for TOPICS_COUNT conversation starters
  that build on those earlier recaps, and post them to POST_CHANNEL_ID.

Uses only the Discord REST API (no gateway) and the DeepSeek chat API, both through the
egress proxy in $HTTPS_PROXY (httpx / openai read it from the environment). Stateless: the
root fs is read-only and nothing is written to disk. A job missed while the container was
down is skipped, not replayed.

Usage:
  scheduler.py                         run forever
  scheduler.py --once summary|topics   run one job now and post it
  scheduler.py --once summary --dry-run   run one job, print the result instead of posting
"""

import argparse
import asyncio
import datetime as dt
import os
import re
import sys
from zoneinfo import ZoneInfo

import httpx
from openai import AsyncOpenAI

API = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000
TEXT_CHANNEL_TYPES = (0, 5)  # GUILD_TEXT, GUILD_ANNOUNCEMENT
MAX_TRANSCRIPT_CHARS = 60000
DISCORD_MSG_LIMIT = 2000
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
RECAP_PREFIX = "[daily recap "  # fixed marker so later runs can find earlier recaps
HISTORY_RECAPS = int(os.environ.get("HISTORY_RECAPS", "7").strip() or 7)
PERSONA_FILE =os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "default.txt")


def env(name, default=""):
    return os.environ.get(name, default).strip()


def parse_hhmm(name, default):
    raw = env(name, default)
    m = TIME_RE.match(raw)
    if not m:
        sys.exit(f"[scheduler] config error: {name} must be HH:MM, got {raw!r}")
    return int(m.group(1)), int(m.group(2))


TZ = ZoneInfo(env("SCHED_TZ", "Australia/Adelaide"))
SUMMARY_AT = parse_hhmm("SUMMARY_TIME", "22:00")
TOPICS_AT = parse_hhmm("TOPICS_TIME", "09:00")
TOPICS_COUNT = int(env("TOPICS_COUNT", "3"))
MODEL = env("DEEPSEEK_MODEL", "deepseek-flash")


def post_channel_id():
    raw = env("POST_CHANNEL_ID") or env("ALLOWED_CHANNEL_IDS").split(",")[0].strip()
    if not raw.isdigit():
        sys.exit("[scheduler] config error: set POST_CHANNEL_ID (or ALLOWED_CHANNEL_IDS)")
    return raw


def persona():
    custom = env("SYSTEM_PROMPT")
    text = custom.replace("\\n", "\n") if custom else open(PERSONA_FILE, encoding="utf-8").read()
    now = dt.datetime.now(TZ)
    return text.replace("{date}", now.strftime("%B %d %Y")).replace("{time}", now.strftime("%H:%M %Z"))


def snowflake_at(when):
    return str((int(when.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22)


class Discord:
    def __init__(self):
        self.http = httpx.AsyncClient(
            base_url=API,
            headers={"Authorization": "Bot " + os.environ["DISCORD_BOT_TOKEN"]},
            timeout=30,
        )

    async def get(self, path, **params):
        for _ in range(5):
            r = await self.http.get(path, params=params)
            if r.status_code == 429:
                await asyncio.sleep(float(r.json().get("retry_after", 1)) + 0.5)
                continue
            return r
        return r

    async def post_message(self, channel_id, text):
        for chunk in split_message(text):
            r = await self.http.post(
                f"/channels/{channel_id}/messages",
                json={"content": chunk, "allowed_mentions": {"parse": []}},
            )
            r.raise_for_status()

    async def past_recaps(self, channel_id, limit):
        """The bot's own earlier recaps in the post channel (Discord is the memory; no disk)."""
        me = (await self.get("/users/@me")).json()["id"]
        found, before = [], None
        for _ in range(5):  # look back through at most 500 messages
            params = {"limit": 100} | ({"before": before} if before else {})
            r = await self.get(f"/channels/{channel_id}/messages", **params)
            if r.status_code != 200 or not r.json():
                break
            page = r.json()
            for m in page:
                if m["author"]["id"] == me and m.get("content", "").startswith(RECAP_PREFIX):
                    found.append(m["content"])
            before = page[-1]["id"]
            if len(found) >= limit or len(page) < 100:
                break
        return list(reversed(found[:limit]))  # oldest first

    async def guild_of(self, channel_id):
        r = await self.get(f"/channels/{channel_id}")
        r.raise_for_status()
        return r.json()["guild_id"]

    async def transcript(self, guild_id, since):
        """Messages since `since` in every text channel the bot can read, oldest first."""
        r = await self.get(f"/guilds/{guild_id}/channels")
        r.raise_for_status()
        channels = sorted(
            (c for c in r.json() if c["type"] in TEXT_CHANNEL_TYPES),
            key=lambda c: c.get("position", 0),
        )
        lines, read, skipped = [], 0, 0
        for ch in channels:
            after, msgs = snowflake_at(since), []
            while True:
                r = await self.get(f"/channels/{ch['id']}/messages", after=after, limit=100)
                if r.status_code in (403, 404):
                    skipped += 1
                    break
                r.raise_for_status()
                page = r.json()
                if not page:
                    break
                msgs.extend(page)
                after = max(page, key=lambda m: int(m["id"]))["id"]
                if len(page) < 100:
                    break
            if r.status_code not in (403, 404):
                read += 1
            msgs.sort(key=lambda m: int(m["id"]))
            for m in msgs:
                content = (m.get("content") or "").strip()
                if not content:
                    continue
                name = m["author"].get("global_name") or m["author"]["username"]
                tag = " (bot)" if m["author"].get("bot") else ""
                lines.append(f"[#{ch['name']}] {name}{tag}: {content}")
        print(f"[scheduler] read {read} channels, skipped {skipped} (no access), {len(lines)} messages", flush=True)
        text = "\n".join(lines)
        if len(text) > MAX_TRANSCRIPT_CHARS:
            text = "...(older messages cut)...\n" + text[-MAX_TRANSCRIPT_CHARS:]
        return text, len(lines)


def split_message(text):
    text = text.strip()
    while text:
        if len(text) <= DISCORD_MSG_LIMIT:
            yield text
            return
        cut = text.rfind("\n", 0, DISCORD_MSG_LIMIT)
        if cut < DISCORD_MSG_LIMIT // 2:
            cut = DISCORD_MSG_LIMIT
        yield text[:cut]
        text = text[cut:].lstrip()


async def ask(system, user):
    client = AsyncOpenAI(base_url="https://api.deepseek.com", api_key=os.environ["DEEPSEEK_API_KEY"])
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content.strip()


class Jobs:
    def __init__(self):
        self.discord = Discord()
        self.channel = post_channel_id()

    async def history(self):
        recaps = await self.discord.past_recaps(self.channel, HISTORY_RECAPS)
        print(f"[scheduler] loaded {len(recaps)} earlier recaps", flush=True)
        if not recaps:
            return "(no earlier recaps yet: this is the first one)"
        return "\n\n".join(recaps)

    async def summary(self):
        guild = await self.discord.guild_of(self.channel)
        history = await self.history()
        text, count = await self.discord.transcript(guild, dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24))
        date = dt.datetime.now(TZ).strftime("%Y-%m-%d")
        if count == 0:
            return f"{RECAP_PREFIX}{date}]\nnobody said anything today. dead server. fitting."
        task = (
            "This is your nightly job: recap today's chat in this Discord server.\n\n"
            "First, the earlier daily recaps (oldest first). Use them to understand what this group is usually "
            "about, its running jokes and ongoing threads, and where the conversation has been heading. Frame "
            "today against that: call out what continued from before, what's new, and how the group's direction "
            "is shifting. Don't re-summarize old days.\n\n"
            "=== earlier recaps ===\n" + history + "\n\n"
            "Then today's chat: the last 24h of messages from all channels, formatted as [#channel] name: message.\n\n"
            "Write the recap in your usual voice, but make it actually useful: what people talked about, who said "
            "what notable thing, any plans or decisions, the funniest moments. Group by topic, not by channel. "
            "Keep it tight: at most 8 short lines, each starting with '- ', and end with one line starting with "
            "'direction:' saying where the group's conversation is heading. Use the language most of the chat used. "
            "Use people's names as written; don't use @mentions. Never repeat private info (addresses, phone "
            "numbers, passwords) even if it appears. Don't write a header; it is added for you.\n\n"
            "=== today's chat ===\n" + text
        )
        return f"{RECAP_PREFIX}{date}]\n" + await ask(persona(), task)

    async def topics(self):
        history = await self.history()
        task = (
            "This is your morning job: drop conversation starters for the boys in this Discord server.\n\n"
            "Here are the recent daily recaps (oldest first). Read them to understand what this group is into, "
            "its ongoing threads and running jokes, and which direction the conversation has been heading:\n\n"
            "=== recent recaps ===\n" + history + "\n\n"
            f"Give exactly {TOPICS_COUNT} topics to talk about today, in your usual voice. Build on the group's "
            "direction: pick up an unfinished thread or push an ongoing topic one step further, plus at most one "
            "fresh thing that fits what they like (a news-ish / internet-culture thing or a dumb would-you-rather). "
            "Use the language most of the recaps use. One short header line, then the topics numbered 1., 2., ... "
            "each one line. Don't use @mentions."
        )
        return await ask(persona(), task)

    async def run(self, name, dry_run=False):
        try:
            text = await getattr(self, name)()
            if dry_run:
                print(f"[scheduler] --- {name} (dry run, not posted) ---\n{text}", flush=True)
            else:
                await self.discord.post_message(self.channel, text)
                print(f"[scheduler] posted {name} ({len(text)} chars)", flush=True)
        except Exception as e:  # keep the loop alive; never print secrets
            print(f"[scheduler] {name} failed: {type(e).__name__}: {e}", flush=True)


def next_run(now, hhmm):
    target = now.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
    return target if target > now else target + dt.timedelta(days=1)


async def forever(jobs):
    print(
        f"[scheduler] started tz={TZ.key} summary={SUMMARY_AT[0]:02d}:{SUMMARY_AT[1]:02d} "
        f"topics={TOPICS_AT[0]:02d}:{TOPICS_AT[1]:02d} count={TOPICS_COUNT} model={MODEL} "
        f"post_channel={jobs.channel} proxy={env('HTTPS_PROXY')}",
        flush=True,
    )
    while True:
        now = dt.datetime.now(TZ)
        name, when = min(
            (("summary", next_run(now, SUMMARY_AT)), ("topics", next_run(now, TOPICS_AT))),
            key=lambda x: x[1],
        )
        print(f"[scheduler] next: {name} at {when.isoformat(timespec='minutes')}", flush=True)
        # Sleep in short steps so clock changes (DST, host sleep) don't push a job far off.
        while (left := (when - dt.datetime.now(TZ)).total_seconds()) > 0:
            await asyncio.sleep(min(left, 300))
        await jobs.run(name)
        await asyncio.sleep(61)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", choices=("summary", "topics"))
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    for k in ("DISCORD_BOT_TOKEN", "DEEPSEEK_API_KEY"):
        if not env(k):
            sys.exit(f"[scheduler] config error: {k} is not set")
    jobs = Jobs()
    asyncio.run(jobs.run(a.once, a.dry_run) if a.once else forever(jobs))


if __name__ == "__main__":
    main()
