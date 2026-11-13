"""v3.2 news job: scheduling, material lookup, per-guild dedupe, selection, posting, digest.

Everything is a fake: no Discord, no model, no network.
"""

import asyncio
import datetime as dt
import json
import re
import tempfile
import unittest
from pathlib import Path

import scheduler
from guildcfg import GuildConfig, GuildConfigStore

UTC = dt.timezone.utc
GUILD_A = "111111111111111111"


def run(coro):
    return asyncio.run(coro)


def at(day, hour=9, minute=1):
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def make_item(n, score=5.0, title_en=None, title_zh=None, summary_en=None, summary_zh=None):
    return {
        "id": f"id{n}",
        "url": f"https://example.com/{n}",
        "title_en": title_en or f"English title {n}",
        "title_zh": title_zh or f"中文标题{n}",
        "summary_en": summary_en or f"English summary {n}. Second sentence. Third sentence.",
        "summary_zh": summary_zh or f"中文摘要{n}。第二句。第三句。",
        "score": score,
        "source": "hackernews",
        "category": "科技新闻",
    }


class FakeDiscord:
    """Records posts; strips mass mentions exactly like the real post_message does."""

    def __init__(self):
        self.posts = []

    async def post_message(self, channel_id, text, allow_users=False):
        self.posts.append((str(channel_id), scheduler.strip_mass_mentions(text)))


class NewsAsk:
    """Two calls per run: the id pick, then the intro/quips. Both overridable."""

    DEFAULT_COMMENTARY = '{"intro": "morning, here is the stuff.", "quips": {"id1": "classic."}}'

    def __init__(self, pick=None, commentary=None):
        self.calls = []
        self.systems = []
        self.pick = pick if pick is not None else self.all_ids
        self.commentary = commentary if commentary is not None else self.DEFAULT_COMMENTARY

    @staticmethod
    def all_ids(user):
        return json.dumps(re.findall(r"id=(\S+)", user))

    async def __call__(self, system, user, max_tokens=1200):
        self.calls.append(user)
        self.systems.append(system)
        if "JSON array of ids" in user:
            return self.pick(user) if callable(self.pick) else self.pick
        return self.commentary(user) if callable(self.commentary) else self.commentary


class NewsCase(unittest.TestCase):
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
        self.now = [at(14, 9)]
        self.clock = lambda: self.now[0]

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ------------------------------------------------------------ #

    def news_cfg(self, name="N", language="en", items=3, channel=400, on=True, time="09:00"):
        return GuildConfig(
            name=name,
            trust="public",
            language=language,
            features={"news": {"on": on, "channel": channel, "time": time, "items": items}},
        )

    def make_jobs(self, cfg, discord=None, ask=None):
        store = GuildConfigStore(self.guilds)
        store.save(GUILD_A, cfg)
        return scheduler.Jobs(
            store,
            discord=discord or FakeDiscord(),
            ask_model=ask or NewsAsk(),
            clock=self.clock,
            tz=UTC,
            data_dir=self.root,
            log_fn=lambda msg: None,
        )

    def write_material(self, date, items):
        path = self.root / "shared" / "news" / f"material-{date}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": date, "generated_at": "", "items": items}), encoding="utf-8")
        return path

    def seed_seen(self, seen):
        path = self.guilds / GUILD_A / "news-seen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seen": seen}), encoding="utf-8")

    def read_seen(self):
        return json.loads((self.guilds / GUILD_A / "news-seen.json").read_text(encoding="utf-8"))["seen"]

    def seed_interests(self, text):
        path = self.guilds / GUILD_A / "interests.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def posted_news(self, discord):
        return [text for _, text in discord.posts[1:]]  # [0] is the intro


# ---------------------------------------------------------------- scheduling


class TestNewsSchedule(NewsCase):
    def test_on_with_channel_gets_a_slot(self):
        cfg = self.news_cfg(time="07:30")
        self.assertEqual(scheduler.schedule_for(cfg), [("news", (7, 30))])

    def test_off_is_not_scheduled(self):
        self.assertEqual(scheduler.schedule_for(self.news_cfg(on=False)), [])

    def test_no_channel_is_not_scheduled(self):
        self.assertEqual(scheduler.schedule_for(self.news_cfg(channel=None)), [])

    def test_public_guild_may_have_news(self):
        cfg = self.news_cfg()
        self.assertEqual(cfg.trust, "public")
        self.assertEqual(scheduler.schedule_for(cfg), [("news", (9, 0))])

    def test_tick_runs_news_at_its_own_time(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(time="09:00"), discord=discord)
        self.write_material("2026-11-14", [make_item(1), make_item(2)])

        self.now[0] = at(14, 9, 11)
        self.assertEqual(run(jobs.tick(self.now[0])), [])
        self.now[0] = at(14, 9, 1)
        self.assertEqual(run(jobs.tick(self.now[0])), [(GUILD_A, "news")])
        self.assertEqual(len(discord.posts), 1 + 2)


# ---------------------------------------------------------------- material


class TestMaterialLookup(NewsCase):
    def posts_for_material(self, dates):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=5), discord=discord)
        for days_ago in dates:
            day = at(14) - dt.timedelta(days=days_ago)
            n = days_ago + 1
            self.write_material(day.strftime("%Y-%m-%d"), [make_item(n)])
        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))
        return discord

    def test_todays_material_is_used(self):
        discord = self.posts_for_material([0])
        self.assertEqual(len(discord.posts), 2)
        self.assertIn("English title 1", discord.posts[1][1])

    def test_yesterdays_material_is_used(self):
        discord = self.posts_for_material([1])
        self.assertEqual(len(discord.posts), 2)
        self.assertIn("English title 2", discord.posts[1][1])

    def test_two_days_old_material_is_used(self):
        discord = self.posts_for_material([2])
        self.assertEqual(len(discord.posts), 2)
        self.assertIn("English title 3", discord.posts[1][1])

    def test_three_days_old_material_is_ignored(self):
        logs = []
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(), discord=discord)
        jobs.log = logs.append
        self.write_material("2026-11-11", [make_item(1)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.posts, [])
        self.assertTrue(any("no material" in line for line in logs))
        self.assertFalse((self.guilds / GUILD_A / "news-seen.json").exists())
        self.assertFalse((self.guilds / GUILD_A / "state.json").exists())

    def test_today_wins_even_when_older_files_exist(self):
        discord = self.posts_for_material([0, 1, 2])
        self.assertEqual(len(discord.posts), 2)
        self.assertIn("English title 1", discord.posts[1][1])

    def test_unreadable_today_falls_back_to_yesterday(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(), discord=discord)
        bad = self.write_material("2026-11-14", [])
        self.write_material("2026-11-13", [make_item(2)])
        self.assertTrue(bad.exists())

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))
        self.assertEqual(len(discord.posts), 2)
        self.assertIn("English title 2", discord.posts[1][1])


# ---------------------------------------------------------------- dedupe


class TestDedupe(NewsCase):
    def test_already_sent_ids_are_not_sent_again(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=5), discord=discord)
        self.write_material("2026-11-14", [make_item(1), make_item(2), make_item(3)])
        self.seed_seen({"id1": "2026-11-13"})

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        posted = " ".join(t for _, t in discord.posts)
        self.assertNotIn("English title 1", posted)
        self.assertIn("English title 2", posted)
        self.assertIn("English title 3", posted)
        self.assertEqual(sorted(self.read_seen()), ["id1", "id2", "id3"])

    def test_nothing_new_posts_nothing(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=5), discord=discord)
        self.write_material("2026-11-14", [make_item(1), make_item(2)])
        self.seed_seen({"id1": "2026-11-13", "id2": "2026-11-13"})

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.posts, [])

    def test_seen_entries_older_than_thirty_days_are_dropped(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=5), discord=discord)
        self.write_material("2026-11-14", [make_item(1)])
        # 2026-11-14 minus 30 days = 2026-10-15: anything before that is forgotten.
        self.seed_seen({"old": "2026-10-01", "edge": "2026-10-15", "recent": "2026-11-10"})

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        seen = self.read_seen()
        self.assertNotIn("old", seen)
        self.assertIn("edge", seen)
        self.assertIn("recent", seen)
        self.assertEqual(seen["id1"], "2026-11-14")

    def test_a_forgotten_id_can_be_sent_again(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=5), discord=discord)
        self.write_material("2026-11-14", [make_item(1)])
        self.seed_seen({"id1": "2026-09-01"})  # way older than 30 days

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertIn("English title 1", discord.posts[1][1])


# ---------------------------------------------------------------- selection


class TestSelection(NewsCase):
    def test_bad_json_falls_back_to_score_order(self):
        ask = NewsAsk(pick="sorry, I cannot do JSON today")
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=2), discord=discord, ask=ask)
        self.write_material("2026-11-14", [
            make_item(1, score=1.0), make_item(2, score=9.0), make_item(3, score=5.0),
        ])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        posted = " ".join(self.posted_news(discord))
        self.assertIn("English title 2", posted)
        self.assertIn("English title 3", posted)
        self.assertNotIn("English title 1", posted)
        self.assertEqual(self.posted_news(discord)[0].count("**"), 2)  # one item per message

    def test_model_exception_falls_back_to_score_order(self):
        def boom(user):
            raise RuntimeError("model down")

        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=1), discord=discord, ask=NewsAsk(pick=boom))
        self.write_material("2026-11-14", [make_item(1, score=2.0), make_item(2, score=7.0)])

        run(
            jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertIn("English title 2", discord.posts[1][1])

    def test_ids_not_in_the_candidates_are_dropped(self):
        ask = NewsAsk(pick='["id1", "made-up-id", "id3"]')
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=2), discord=discord, ask=ask)
        self.write_material("2026-11-14", [make_item(1), make_item(2), make_item(3)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        posted = " ".join(self.posted_news(discord))
        self.assertIn("English title 1", posted)
        self.assertIn("English title 3", posted)
        self.assertNotIn("English title 2", posted)

    def test_only_invented_ids_falls_back_to_score(self):
        ask = NewsAsk(pick='["nope", "also-nope"]')
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=1), discord=discord, ask=ask)
        self.write_material("2026-11-14", [make_item(1, score=1.0), make_item(2, score=8.0)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertIn("English title 2", discord.posts[1][1])

    def test_item_count_is_capped(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=2), discord=discord)
        self.write_material("2026-11-14", [make_item(n) for n in range(1, 6)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(len(discord.posts), 1 + 2)

    def test_interests_go_into_the_pick_prompt(self):
        ask = NewsAsk()
        jobs = self.make_jobs(self.news_cfg(items=1), ask=ask)
        self.write_material("2026-11-14", [make_item(1)])
        self.seed_interests("INTERESTS-FROM-FILE: Houdini and local models")

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        pick_prompt = next(c for c in ask.calls if "JSON array of ids" in c)
        self.assertIn("INTERESTS-FROM-FILE", pick_prompt)
        self.assertIn("id=id1", pick_prompt)

    def test_profile_threads_used_without_interests_file(self):
        ask = NewsAsk()
        jobs = self.make_jobs(self.news_cfg(items=1), ask=ask)
        memory = jobs.memory(GUILD_A)
        memory.ensure()
        memory.write_block("threads", "THREADS-FROM-PROFILE")
        self.write_material("2026-11-14", [make_item(1)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        pick_prompt = next(c for c in ask.calls if "JSON array of ids" in c)
        self.assertIn("THREADS-FROM-PROFILE", pick_prompt)


# ---------------------------------------------------------------- output


class TestNewsOutput(NewsCase):
    def test_message_count_is_intro_plus_items(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=3), discord=discord)
        self.write_material("2026-11-14", [make_item(n) for n in range(1, 6)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(len(discord.posts), 4)
        self.assertEqual({channel for channel, _ in discord.posts}, {"400"})

    def test_intro_comes_first_in_the_persona_call(self):
        ask = NewsAsk(commentary='{"intro": "INTRO-LINE", "quips": {}}')
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=1), discord=discord, ask=ask)
        self.write_material("2026-11-14", [make_item(1)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.posts[0][1], "INTRO-LINE")
        commentary_prompt = next(c for c in ask.calls if "morning news post" in c)
        self.assertIn("id=id1", commentary_prompt)
        self.assertIn("CHARACTER", ask.systems[-1])  # intro + quips use the guild persona

    def test_links_are_wrapped_in_angle_brackets(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=2), discord=discord)
        self.write_material("2026-11-14", [make_item(1), make_item(2)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        for text in self.posted_news(discord):
            self.assertRegex(text, r"<https://example\.com/\d+>")
            self.assertNotRegex(text, r"(?<!<)https://example\.com/\d+(?!>)")

    def run_one_item(self, language):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=1, language=language), discord=discord)
        self.write_material("2026-11-14", [make_item(1)])
        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))
        return discord

    def test_zh_guild_gets_zh_fields(self):
        discord = self.run_one_item("zh")
        self.assertIn("中文标题1", discord.posts[1][1])
        self.assertIn("中文摘要1", discord.posts[1][1])
        self.assertNotIn("English title 1", discord.posts[1][1])

    def test_en_guild_gets_en_fields(self):
        discord = self.run_one_item("en")
        self.assertIn("English title 1", discord.posts[1][1])
        self.assertIn("English summary 1", discord.posts[1][1])
        self.assertNotIn("中文标题1", discord.posts[1][1])

    def test_digest_is_written_with_full_summary_intro_and_quip(self):
        long_summary = "Full summary first sentence. " + ("Detail sentence. " * 40) + "Last sentence."
        ask = NewsAsk(commentary='{"intro": "DIGEST-INTRO", "quips": {"id1": "DIGEST-QUIP"}}')
        jobs = self.make_jobs(self.news_cfg(items=1), ask=ask)
        self.write_material("2026-11-14", [make_item(1, summary_en=long_summary)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        path = self.root / "shared" / "news" / "digests" / f"2026-11-14-{GUILD_A}.md"
        self.assertTrue(path.exists())
        digest = path.read_text(encoding="utf-8")
        self.assertIn("DIGEST-INTRO", digest)
        self.assertIn("DIGEST-QUIP", digest)
        self.assertIn("Last sentence.", digest)  # the full summary, not the preview
        self.assertIn("<https://example.com/1>", digest)

    def test_dry_run_posts_and_saves_nothing(self):
        logs = []
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=1), discord=discord)
        jobs.dry_run = True
        jobs.log = logs.append
        self.write_material("2026-11-14", [make_item(1)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        self.assertEqual(discord.posts, [])
        self.assertFalse((self.guilds / GUILD_A / "news-seen.json").exists())
        self.assertFalse((self.root / "shared" / "news" / "digests").exists())
        self.assertTrue(any("dry run" in line for line in logs))

    def test_mass_mentions_are_neutralised(self):
        ask = NewsAsk(commentary='{"intro": "hey @everyone", "quips": {"id1": "@here look"}}')
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=1), discord=discord, ask=ask)
        self.write_material("2026-11-14", [make_item(1)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        joined = "\n".join(text for _, text in discord.posts)
        self.assertNotIn("@everyone", joined)
        self.assertNotIn("@here", joined)
        self.assertIn("@​everyone", joined)
        self.assertIn("@​here", joined)

    def test_news_marks_the_day_as_run(self):
        jobs = self.make_jobs(self.news_cfg(items=1))
        self.write_material("2026-11-14", [make_item(1)])

        run(jobs.news(GUILD_A, jobs.store.get(GUILD_A)))

        state = json.loads((self.guilds / GUILD_A / "state.json").read_text(encoding="utf-8"))
        self.assertIn("news", state["done"])
        self.assertEqual(state["date"], "2026-11-14")


class TestOnceCommand(NewsCase):
    def test_once_runs_news_for_a_configured_guild(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(items=1), discord=discord)
        self.write_material("2026-11-14", [make_item(1)])

        run(scheduler.once(jobs, "news", GUILD_A))

        self.assertEqual(len(discord.posts), 2)

    def test_once_skips_news_when_off(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(on=False), discord=discord)
        self.write_material("2026-11-14", [make_item(1)])
        run(scheduler.once(jobs, "news", GUILD_A))
        self.assertEqual(discord.posts, [])

    def test_once_skips_news_when_no_channel(self):
        discord = FakeDiscord()
        jobs = self.make_jobs(self.news_cfg(channel=None), discord=discord)
        self.write_material("2026-11-14", [make_item(1)])
        run(scheduler.once(jobs, "news", GUILD_A))
        self.assertEqual(discord.posts, [])


if __name__ == "__main__":
    unittest.main()


class ShortSummaryCutTest(unittest.TestCase):
    def test_dot_inside_code_or_number_is_not_a_sentence_end(self):
        import scheduler
        text = ("Rust ships custom allocators and more. " * 3
                + "Cargo adds a [lints.cargo] table and runs at 2.5 tok/s on phones " * 5)
        out = scheduler.short_summary(text, limit=200)
        self.assertTrue(out.endswith("more.") or out.endswith("…"), out)
        self.assertFalse(out.endswith("[lints."))
        self.assertFalse(out.endswith("2."))
