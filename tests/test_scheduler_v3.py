"""v3 scheduler: per-guild jobs, consent, channel skips, block rewrites, state and feeds.

Everything is a fake: no Discord, no model, no fetcher, no network.
"""

import asyncio
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import guildmem
import scheduler
from guildcfg import GuildConfig, GuildConfigStore

UTC = dt.timezone.utc


def run(coro):
    return asyncio.run(coro)


GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"


def at(day, hour, minute=1):
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def recap_cfg(name="A", hour="22:00", channel=100, consent=True, skip=()):
    return GuildConfig(
        name=name,
        trust="owner",
        consent=consent,
        recap_skip_channels=list(skip),
        features={"recap": {"on": True, "channel": channel, "time": hour}},
    )


class FakeDiscord:
    """guild_id -> {channel_id: (name, [human lines])}; records posts, DMs and skip lists.

    ``channel_guild`` answers from that map (plus ``foreign``: channel id -> another guild),
    and a channel it doesn't know is reported as unreadable, like a 404.
    """

    def __init__(self, channels=None, foreign=None):
        self.channels = channels or {}
        self.guild_of = {str(cid): str(gid) for gid, chans in self.channels.items() for cid in chans}
        self.guild_of.update({str(cid): str(gid) for cid, gid in (foreign or {}).items()})
        self.posts = []
        self.dms = []
        self.reads = []
        self.skips = []

    async def channel_guild(self, channel_id):
        return self.guild_of.get(str(channel_id))

    async def dm_user(self, user_id, text):
        self.dms.append((str(user_id), text))

    async def human_messages(self, guild_id, since, skip_channels=()):
        self.reads.append(str(guild_id))
        self.skips.append((str(guild_id), tuple(skip_channels)))
        skip = {str(c) for c in skip_channels}
        lines = []
        for channel_id, (name, messages) in self.channels.get(str(guild_id), {}).items():
            if str(channel_id) in skip:
                continue
            lines += [f"[#{name}] {line}" for line in messages]
        return lines

    async def post_message(self, channel_id, text, allow_users=False):
        self.posts.append((str(channel_id), text))


class FakeAsk:
    """Records prompts; replies via ``reply(user)`` (default: plausible short output)."""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or self._default

    @staticmethod
    def _default(user):
        if "Rewrite ONLY the" in user:
            name = user.split("Rewrite ONLY the '", 1)[1].split("'", 1)[0]
            return f"{name} notes"
        if "This is your morning job" in user:
            return "1. weird thing <https://example.com/a>\n2. another <https://example.com/b>\n4. life thought: meh"
        return "- someone said something\n- someone else said something"

    async def __call__(self, system, user, max_tokens=1200):
        self.calls.append(user)
        return self.reply(user)


class FakeFetcher:
    def __init__(self):
        self.calls = []

    async def __call__(self, path, payload):
        self.calls.append(payload)
        return [
            {"source": f["name"], "title": f"{f['name']} headline", "link": "https://example.com/x"}
            for f in payload["feeds"]
        ]


class SchedulerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.guilds = self.root / "guilds"
        personas = self.root / "shared" / "personas"
        (personas / "tiredfx").mkdir(parents=True)
        (personas / "_styles").mkdir(parents=True)
        (personas / "_base.md").write_text("BASE-FLOOR", encoding="utf-8")
        (personas / "tiredfx" / "character.md").write_text("CHARACTER", encoding="utf-8")
        (personas / "_styles" / "normal.md").write_text("STYLE", encoding="utf-8")
        self.now = [at(14, 22)]
        self.clock = lambda: self.now[0]

    def tearDown(self):
        self._tmp.cleanup()

    def make_jobs(self, configs, discord=None, ask=None, fetcher=None):
        store = GuildConfigStore(self.guilds)
        for guild_id, cfg in configs.items():
            store.save(guild_id, cfg)
        return scheduler.Jobs(
            store,
            discord=discord or FakeDiscord(),
            ask_model=ask or FakeAsk(),
            fetcher=fetcher or FakeFetcher(),
            clock=self.clock,
            tz=UTC,
            data_dir=self.root,
            log_fn=lambda msg: None,
        )

    def seed_profile(self, guild_id, kurt="HAND-WRITTEN-DIRECTION"):
        memory = guildmem.GuildMemory(self.guilds / str(guild_id))
        memory.ensure()
        memory.seed_kurt(kurt)
        memory.write_block("threads", "old threads")
        return memory


class TestPerGuildSchedule(SchedulerCase):
    def test_two_guilds_fire_at_their_own_times(self):
        discord = FakeDiscord({GUILD_A: {100: ("a-chat", ["alice <@1>: hi A"])},
                               GUILD_B: {200: ("b-chat", ["bob <@2>: hi B"])}})
        jobs = self.make_jobs({
            GUILD_A: recap_cfg("A", "22:00", channel=100),
            GUILD_B: recap_cfg("B", "22:05", channel=200),
        }, discord=discord)

        self.assertEqual(scheduler.schedule_for(jobs.store.get(GUILD_A)), [("recap", (22, 0))])
        self.assertEqual(scheduler.schedule_for(jobs.store.get(GUILD_B)), [("recap", (22, 5))])

        self.now[0] = at(14, 22, 1)
        self.assertEqual(run(jobs.tick(self.now[0])), [(GUILD_A, "recap")])
        self.assertEqual([p[0] for p in discord.posts], ["100"])
        self.assertNotIn(GUILD_B, discord.reads)

        self.now[0] = at(14, 22, 6)
        self.assertEqual(run(jobs.tick(self.now[0])),
                         [(GUILD_B, "recap")])
        self.assertEqual([p[0] for p in discord.posts], ["100", "200"])

    def test_missed_more_than_ten_minutes_is_skipped(self):
        discord = FakeDiscord({GUILD_A: {100: ("chat", ["alice <@1>: hi"])}})
        jobs = self.make_jobs({GUILD_A: recap_cfg("A", "22:00")}, discord=discord)
        self.now[0] = at(14, 22, 11)
        self.assertEqual(run(jobs.tick(self.now[0])), [])
        self.assertEqual(discord.posts, [])

    def test_consent_false_never_recaps(self):
        discord = FakeDiscord({GUILD_A: {100: ("chat", ["alice <@1>: hi"])}})
        cfg = recap_cfg("A", "22:00", consent=False)
        jobs = self.make_jobs({GUILD_A: cfg}, discord=discord)

        self.assertEqual(scheduler.schedule_for(cfg), [])  # not even scheduled
        self.now[0] = at(14, 22, 1)
        self.assertEqual(run(jobs.tick(self.now[0])), [])
        self.assertEqual(discord.reads, [])

        # Even a manual --once recap refuses to read the chat.
        run(jobs.recap(GUILD_A, cfg))
        self.assertEqual(discord.reads, [])
        self.assertEqual(discord.posts, [])

    def test_recap_skips_configured_channels(self):
        discord = FakeDiscord({GUILD_A: {
            100: ("general-talk", ["alice <@1>: READ-ME-PLEASE"]),
            101: ("private", ["bob <@2>: SECRET-SHOULD-STAY-UNREAD"]),
        }})
        ask = FakeAsk()
        jobs = self.make_jobs({GUILD_A: recap_cfg("A", skip=[101])}, discord=discord, ask=ask)
        self.seed_profile(GUILD_A)
        self.now[0] = at(14, 22, 1)
        run(jobs.recap(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.skips, [(GUILD_A, (101,))])
        recap_prompt = next(c for c in ask.calls if "This is your nightly job" in c)
        self.assertIn("READ-ME-PLEASE", recap_prompt)
        self.assertNotIn("SECRET-SHOULD-STAY-UNREAD", recap_prompt)
        self.assertEqual([p[0] for p in discord.posts], ["100"])


class TestOneRunPerDay(SchedulerCase):
    def test_second_tick_and_restart_do_not_repost(self):
        discord = FakeDiscord({GUILD_A: {100: ("chat", ["alice <@1>: hi"])}})
        jobs = self.make_jobs({GUILD_A: recap_cfg("A", "22:00")}, discord=discord)
        self.seed_profile(GUILD_A)

        self.now[0] = at(14, 22, 1)
        self.assertEqual(run(jobs.tick(self.now[0])),
                         [(GUILD_A, "recap")])
        self.now[0] = at(14, 22, 2)
        self.assertEqual(run(jobs.tick(self.now[0])), [])
        self.assertEqual(len(discord.posts), 1)

        state = json.loads((self.guilds / GUILD_A / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state, {"date": "2026-11-14", "done": ["recap"]})

        # "Restart": a fresh Jobs/state cache over the same folders must not re-post.
        fresh = self.make_jobs({GUILD_A: recap_cfg("A", "22:00")}, discord=discord)
        self.now[0] = at(14, 22, 3)
        self.assertEqual(run(fresh.tick(self.now[0])), [])
        self.assertEqual(len(discord.posts), 1)

    def test_next_day_runs_again(self):
        discord = FakeDiscord({GUILD_A: {100: ("chat", ["alice <@1>: hi"])}})
        jobs = self.make_jobs({GUILD_A: recap_cfg("A", "22:00")}, discord=discord)
        self.seed_profile(GUILD_A)
        for day in (14, 15):
            self.now[0] = at(day, 22, 1)
            run(jobs.tick(self.now[0]))
        self.assertEqual(len(discord.posts), 2)


class TestProfileBlocks(SchedulerCase):
    def test_each_block_rewritten_and_kurt_untouched(self):
        ask = FakeAsk()
        jobs = self.make_jobs({GUILD_A: recap_cfg("A")}, ask=ask)
        memory = self.seed_profile(GUILD_A, kurt="KURT-DO-NOT-TOUCH")
        cfg = jobs.store.get(GUILD_A)

        run(
            jobs.update_profile(GUILD_A, cfg, "[daily recap] body"))

        self.assertEqual(memory.read_block("kurt"), "KURT-DO-NOT-TOUCH")
        for name in guildmem.BOT_BLOCKS:
            self.assertEqual(memory.read_block(name), f"{name} notes")
            self.assertTrue((memory.profile_dir / f"{name}.md").exists())
        rewritten = [c for c in ask.calls if "Rewrite ONLY the" in c]
        self.assertEqual(len(rewritten), len(guildmem.BOT_BLOCKS))
        self.assertFalse(any("'kurt'" in c and "Rewrite ONLY" in c for c in ask.calls))

    def test_oversized_block_recompressed_then_truncated(self):
        long_text = "x" * 1000
        ask = FakeAsk(reply=lambda user: long_text if ("Rewrite ONLY the" in user or "must be at most" in user)
                      else "- recap")
        jobs = self.make_jobs({GUILD_A: recap_cfg("A")}, ask=ask)
        memory = self.seed_profile(GUILD_A, kurt="KURT")
        cfg = jobs.store.get(GUILD_A)

        run(
            jobs.update_profile(GUILD_A, cfg, "[daily recap] body"))

        for name in guildmem.BOT_BLOCKS:
            limit = guildmem.block_limit(name)
            self.assertLessEqual(len(memory.read_block(name)), limit)
            self.assertEqual(len(memory.read_block(name)), limit)
        self.assertEqual(len([c for c in ask.calls if "Rewrite ONLY the" in c]), len(guildmem.BOT_BLOCKS))
        self.assertEqual(len([c for c in ask.calls if "must be at most" in c]), len(guildmem.BOT_BLOCKS))
        self.assertEqual(memory.read_block("kurt"), "KURT")


class TestRecapRetention(SchedulerCase):
    def test_keeps_last_ten_days(self):
        discord = FakeDiscord({GUILD_A: {100: ("chat", ["alice <@1>: day"])}})
        jobs = self.make_jobs({GUILD_A: recap_cfg("A")}, discord=discord)
        self.seed_profile(GUILD_A)
        cfg = jobs.store.get(GUILD_A)
        for day in range(1, 13):
            self.now[0] = at(day, 22, 1)
            run(jobs.recap(GUILD_A, cfg))
        recaps = sorted(p.stem for p in (self.guilds / GUILD_A / "recaps").glob("*.md"))
        self.assertEqual(recaps, [f"2026-11-{d:02d}" for d in range(3, 13)])


class TestTopicsFeeds(SchedulerCase):
    def topics_cfg(self, feeds):
        return GuildConfig(
            name="T",
            trust="owner",
            consent=True,
            features={"topics": {"on": True, "channel": 300, "time": "09:00", "count": 3, "feeds": feeds}},
        )

    def test_feeds_come_from_the_guild_config(self):
        feeds = [
            {"name": "Blog", "url": "https://blog.example/feed"},
            {"name": "reddit-weird", "url": "https://reddit.example/weird"},
            {"name": "reddit", "url": "https://reddit.example/hot"},
        ]
        fetcher = FakeFetcher()
        jobs = self.make_jobs({GUILD_A: self.topics_cfg(feeds)}, fetcher=fetcher)
        cfg = jobs.store.get(GUILD_A)

        self.assertEqual([f["name"] for f in jobs.feeds_for(cfg)], ["Blog", "reddit-weird", "reddit"])
        material = run(jobs.fetch_material(cfg))
        self.assertEqual(sorted(material), ["Blog", "reddit", "reddit-weird"])
        self.assertEqual(fetcher.calls[0], {"feeds": [feeds[0]], "per_feed": 6})
        self.assertEqual(fetcher.calls[1]["feeds"], feeds[1:])
        self.assertEqual(fetcher.calls[1]["per_feed"], 30)

    def test_default_feeds_match_the_v2_list(self):
        cfg = GuildConfig(
            name="T", trust="owner", consent=True,
            features={"topics": {"on": True, "channel": 300}},
        )
        jobs = self.make_jobs({GUILD_A: cfg})
        names = [f["name"] for f in jobs.feeds_for(jobs.store.get(GUILD_A))]
        self.assertIn("Hacker News", names)
        self.assertIn("reddit-weird", names)
        self.assertIn("reddit", names)

    def test_material_is_stored_per_guild(self):
        feeds = [{"name": "Blog", "url": "https://blog.example/feed"}]
        jobs = self.make_jobs({GUILD_A: self.topics_cfg(feeds), GUILD_B: self.topics_cfg(feeds)})
        self.now[0] = at(14, 8, 1)
        run(
            jobs.prefetch(GUILD_A, jobs.store.get(GUILD_A)))
        run(
            jobs.prefetch(GUILD_B, jobs.store.get(GUILD_B)))
        for guild_id in (GUILD_A, GUILD_B):
            data = json.loads((self.guilds / guild_id / "material.json").read_text(encoding="utf-8"))
            self.assertEqual(data["date"], "2026-11-14")
            self.assertEqual(sorted(data["items"]), ["Blog"])


class TestTopicSlots(SchedulerCase):
    def test_prefetch_refetch_and_topics_follow_the_configured_time(self):
        cfg = GuildConfig(
            name="T", trust="owner", consent=True,
            features={"topics": {"on": True, "channel": 300, "time": "09:00"}})
        self.assertEqual(
            scheduler.schedule_for(cfg),
            [("prefetch", (8, 0)), ("refetch", (8, 30)), ("topics", (9, 0))],
        )

    def test_public_guild_cannot_enable_a_feature_above_its_trust(self):
        # topics is public in the registry; tools is owner-only. A public guild asking for
        # tools must still get nothing scheduled (registry ceiling, not a config value).
        cfg = GuildConfig(
            name="P", trust="public", consent=True,
            features={"recap": {"on": True, "channel": 100, "time": "22:00"}})
        self.assertEqual(scheduler.schedule_for(cfg), [("recap", (22, 0))])
        self.assertFalse(any(name == "tools" for name, _ in scheduler.schedule_for(cfg)))


# ---------------------------------------------------------------- channel guard
#
# A configured channel id can point at another server's channel (copied during a
# migration). Nothing may be posted there, the owner gets one DM per channel, and the
# day is not recorded as sent.


def news_item(n):
    return {
        "id": f"id{n}",
        "url": f"https://example.com/{n}",
        "title_en": f"English title {n}",
        "title_zh": f"中文标题{n}",
        "summary_en": f"English summary {n}. Second sentence.",
        "summary_zh": f"中文摘要{n}。",
        "score": 5.0,
        "source": "hackernews",
        "category": "科技新闻",
    }


class PostingGuardCase(SchedulerCase):
    OWNER = "424242424242424242"

    def news_cfg(self, channel=400):
        return GuildConfig(
            name="N",
            trust="public",
            language="en",
            features={"news": {"on": True, "channel": channel, "time": "09:00", "items": 2}},
        )

    def write_news_material(self, date="2026-11-14"):
        path = self.root / "shared" / "news" / f"material-{date}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"date": date, "items": [news_item(1), news_item(2)]}), encoding="utf-8"
        )
        return path

    def run_with_owner(self, coro):
        with mock.patch.dict(os.environ, {"OWNER_USER_ID": self.OWNER}):
            return run(coro)

    def logs(self, jobs):
        seen = []
        jobs.log = seen.append
        return seen


class TestPostingGuard(PostingGuardCase):
    def test_channel_in_this_guild_is_posted(self):
        discord = FakeDiscord({GUILD_A: {400: ("news", [])}})
        jobs = self.make_jobs({GUILD_A: self.news_cfg()}, discord=discord)
        self.write_news_material()

        self.run_with_owner(jobs.tick(at(14, 9, 1)))

        self.assertEqual(len(discord.posts), 3)  # intro + 2 stories
        self.assertEqual({cid for cid, _ in discord.posts}, {"400"})
        self.assertEqual(discord.dms, [])

    def test_channel_of_another_guild_is_not_posted_and_owner_is_dmed(self):
        discord = FakeDiscord(foreign={400: GUILD_B})
        jobs = self.make_jobs({GUILD_A: self.news_cfg()}, discord=discord)
        logs = self.logs(jobs)
        self.write_news_material()

        self.run_with_owner(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.posts, [])
        self.assertEqual(len(discord.dms), 1)
        user_id, text = discord.dms[0]
        self.assertEqual(user_id, self.OWNER)
        self.assertIn(GUILD_A, text)      # which server is misconfigured
        self.assertIn(GUILD_B, text)      # where the channel really is
        self.assertIn("400", text)        # the channel
        self.assertIn("news", text)       # the feature
        self.assertIn("/setup", text)     # what to do about it
        self.assertTrue(any(
            line == (f"guild {GUILD_A}: news channel 400 is not in this guild "
                     f"(belongs to {GUILD_B}), not posting")
            for line in logs
        ), logs)

    def test_same_channel_again_warns_only_once(self):
        discord = FakeDiscord(foreign={400: GUILD_B})
        jobs = self.make_jobs({GUILD_A: self.news_cfg()}, discord=discord)
        self.write_news_material()
        cfg = jobs.store.get(GUILD_A)

        self.run_with_owner(jobs.news(GUILD_A, cfg))
        self.run_with_owner(jobs.news(GUILD_A, cfg))  # e.g. a retry after --once

        self.assertEqual(discord.posts, [])
        self.assertEqual(len(discord.dms), 1)

        # A retry through tick() is not attempted twice either (same process).
        self.assertEqual(run(jobs.tick(at(14, 9, 1))), [(GUILD_A, "news")])
        self.assertEqual(run(jobs.tick(at(14, 9, 2))), [])
        self.assertEqual(len(discord.dms), 1)
        self.assertEqual(discord.posts, [])

    def test_unreadable_channel_is_not_posted(self):
        discord = FakeDiscord()  # channel_guild() knows no channels -> None
        jobs = self.make_jobs({GUILD_A: self.news_cfg()}, discord=discord)
        self.write_news_material()

        self.run_with_owner(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.posts, [])
        self.assertEqual(len(discord.dms), 1)
        self.assertIn("could not look", discord.dms[0][1])

    def test_without_owner_id_nothing_is_posted_and_nothing_raises(self):
        for value in ("", "not-a-number", "12 34"):
            with self.subTest(owner=value):
                discord = FakeDiscord(foreign={400: GUILD_B})
                jobs = self.make_jobs({GUILD_A: self.news_cfg()}, discord=discord)
                self.write_news_material()
                with mock.patch.dict(os.environ, {"OWNER_USER_ID": value}):
                    run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))  # must not raise
                self.assertEqual(discord.posts, [])
                self.assertEqual(discord.dms, [])

    def test_withheld_news_records_nothing(self):
        discord = FakeDiscord(foreign={400: GUILD_B})
        jobs = self.make_jobs({GUILD_A: self.news_cfg()}, discord=discord)
        self.write_news_material()
        self.now[0] = at(14, 9, 1)

        self.run_with_owner(jobs.tick(self.now[0]))

        guild_dir = self.guilds / GUILD_A
        self.assertFalse((guild_dir / "news-seen.json").exists())
        self.assertFalse((self.root / "shared" / "news" / "digests").exists())
        state_path = guild_dir / "state.json"
        done = json.loads(state_path.read_text(encoding="utf-8"))["done"] if state_path.exists() else []
        self.assertNotIn("news", done)

    def test_withheld_recap_saves_no_recap_and_touches_no_profile(self):
        discord = FakeDiscord(foreign={100: GUILD_B})
        jobs = self.make_jobs({GUILD_A: recap_cfg("A")}, discord=discord)
        self.seed_profile(GUILD_A)

        self.run_with_owner(jobs.recap(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.posts, [])
        self.assertEqual(len(discord.dms), 1)
        self.assertEqual(list((self.guilds / GUILD_A / "recaps").glob("*.md")), [])
        self.assertEqual(jobs.memory(GUILD_A).read_block("threads"), "old threads")

    def test_withheld_topics_keeps_the_material(self):
        cfg = GuildConfig(
            name="T", trust="owner", consent=True,
            features={"topics": {"on": True, "channel": 300, "time": "09:00", "count": 2}},
        )
        discord = FakeDiscord(foreign={300: GUILD_B})
        jobs = self.make_jobs({GUILD_A: cfg}, discord=discord)
        self.now[0] = at(14, 9, 1)
        material = self.guilds / GUILD_A / "material.json"

        self.run_with_owner(jobs.prefetch(GUILD_A, jobs.store.get(GUILD_A)))
        self.assertTrue(material.exists())

        self.run_with_owner(jobs.topics(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.posts, [])
        self.assertEqual(len(discord.dms), 1)
        self.assertTrue(material.exists())  # kept for a rerun

    def test_next_day_tries_again(self):
        discord = FakeDiscord(foreign={400: GUILD_B})
        jobs = self.make_jobs({GUILD_A: self.news_cfg()}, discord=discord)
        cfg = jobs.store.get(GUILD_A)
        for day in (14, 15):
            self.now[0] = at(day, 9, 1)
            self.write_news_material(self.now[0].strftime("%Y-%m-%d"))
            self.run_with_owner(jobs.news(GUILD_A, cfg))
        self.assertEqual(discord.posts, [])


# ---------------------------------------------------------------- REST endpoints


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("no json body")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    """Queued responses; records every call as (method, path, kwargs)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self):
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)

    async def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self._next()

    async def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        return self._next()


def discord_with(*http):
    """A Discord client wired to a fake http layer (no token, no network)."""
    d = object.__new__(scheduler.Discord)
    d.http = FakeHttp(*http)
    return d


class TestDiscordEndpoints(unittest.TestCase):
    def test_channel_guild_returns_the_owning_guild(self):
        d = discord_with(FakeResponse(200, {"id": "400", "guild_id": 111111111111111111}))
        self.assertEqual(run(d.channel_guild("400")), GUILD_A)  # always a string
        self.assertEqual(d.http.calls, [("GET", "/channels/400", {})])

    def test_channel_guild_is_none_for_a_dm_channel(self):
        d = discord_with(FakeResponse(200, {"id": "500", "type": 1}))
        self.assertIsNone(run(d.channel_guild("500")))

    def test_channel_guild_is_none_on_errors(self):
        for status in (403, 404, 500):
            with self.subTest(status=status):
                d = discord_with(FakeResponse(status))
                self.assertIsNone(run(d.channel_guild("400")))

    def test_channel_guild_is_none_when_the_body_is_not_json(self):
        d = discord_with(FakeResponse(200))
        self.assertIsNone(run(d.channel_guild("400")))

    def test_dm_user_opens_the_dm_channel_then_posts(self):
        d = discord_with(FakeResponse(200, {"id": "777"}), FakeResponse(200, {"id": "1"}))
        run(d.dm_user("42", "hello there"))

        self.assertEqual(d.http.calls, [
            ("POST", "/users/@me/channels", {"recipient_id": "42"}),
            ("POST", "/channels/777/messages",
             {"content": "hello there", "allowed_mentions": {"parse": []}}),
        ])

    def test_dm_user_swallows_failures(self):
        d = discord_with(FakeResponse(403))  # cannot open a DM with this user
        run(d.dm_user("42", "hello"))  # must not raise

        d = discord_with(FakeResponse(200, {}))  # no id in the reply
        run(d.dm_user("42", "hello"))


if __name__ == "__main__":
    unittest.main()
