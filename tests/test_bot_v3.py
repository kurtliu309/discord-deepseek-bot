"""v3 behaviour: per-guild budgets, channels, configs, system prompt and /setup."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bot
import guildcfg as g
import guildmem
import persona
import setup_ui


TZ = dt.timezone.utc
NOW = dt.datetime(2026, 11, 14, 22, 5, tzinfo=TZ)


class FakeRNG:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def channel(cid, parent=None):
    return SimpleNamespace(id=cid, parent=parent)


# --------------------------------------------------------------------------- #
# budgets
# --------------------------------------------------------------------------- #


class TestBudgetBook(unittest.TestCase):
    def _book(self, at):
        now = [at]
        return bot.BudgetBook(TZ, clock=lambda: now[0]), now

    def test_one_budget_per_guild(self):
        book, _ = self._book(dt.datetime(2026, 11, 14, 8, 0, tzinfo=TZ))
        a = book.get("g1", "09:00", 5)
        b = book.get("g2", "09:00", 2)
        self.assertEqual((a.limit, b.limit), (5, 2))
        self.assertTrue(a.spend())
        self.assertTrue(a.spend())
        self.assertEqual(a.remaining, 3)
        self.assertEqual(b.remaining, 2)  # isolated
        self.assertIs(book.get("g1", "09:00", 5), a)

    def test_refill_at_each_guilds_own_reset_time(self):
        book, now = self._book(dt.datetime(2026, 11, 14, 8, 0, tzinfo=TZ))
        a = book.get("g1", "09:00", 5)
        b = book.get("g2", "10:00", 2)
        a.spend()
        b.spend()
        now[0] = dt.datetime(2026, 11, 14, 9, 1, tzinfo=TZ)
        self.assertEqual(book.refill_due(), ["g1"])
        self.assertEqual(a.remaining, 5)
        self.assertEqual(b.remaining, 1)  # g2 resets at 10:00
        now[0] = dt.datetime(2026, 11, 14, 10, 1, tzinfo=TZ)
        self.assertEqual(book.refill_due(), ["g2"])
        self.assertEqual(b.remaining, 2)

    def test_access_after_reset_refills(self):
        book, now = self._book(dt.datetime(2026, 11, 14, 8, 0, tzinfo=TZ))
        a = book.get("g1", "09:00", 3)
        a.spend()
        a.spend()
        now[0] = dt.datetime(2026, 11, 14, 9, 0, 1, tzinfo=TZ)
        self.assertEqual(book.get("g1", "09:00", 3).remaining, 3)

    def test_limit_change_never_hands_back_spent_replies(self):
        book, _ = self._book(dt.datetime(2026, 11, 14, 8, 0, tzinfo=TZ))
        a = book.get("g1", "09:00", 5)
        a.spend()
        a.spend()
        lowered = book.get("g1", "09:00", 1)
        self.assertIs(lowered, a)
        self.assertEqual((a.limit, a.remaining), (1, 1))
        raised = book.get("g1", "09:00", 8)
        self.assertEqual((raised.limit, raised.remaining), (8, 1))

    def test_seconds_until_next(self):
        book, _ = self._book(dt.datetime(2026, 11, 14, 8, 0, tzinfo=TZ))
        self.assertIsNone(book.seconds_until_next())
        book.get("g1", "09:00", 5)
        self.assertAlmostEqual(book.seconds_until_next(), 3600.0)


class TestFeedCommand(unittest.IsolatedAsyncioTestCase):
    class Response:
        def __init__(self):
            self.sent = []

        async def send_message(self, content, **kwargs):
            self.sent.append(content)

    async def test_feed_refills_only_the_current_guild(self):
        book = bot.BudgetBook(TZ, clock=lambda: dt.datetime(2026, 11, 14, 8, 0, tzinfo=TZ))
        cfg = g.GuildConfig(name="x", budget={"replies_per_day": 5})
        store = SimpleNamespace(
            maybe_reload=lambda: None,
            get=lambda gid: cfg if str(gid) == "111" else None,
        )
        a = book.get("111", "09:00", 5)
        b = book.get("222", "09:00", 5)
        a.spend()
        b.spend()

        fake = SimpleNamespace(
            config=SimpleNamespace(owner_id=1),
            store=store,
            budgets=book,
            _budget=lambda key, c: book.get(key, c.budget.reset_time, c.budget.replies_per_day),
        )
        response = self.Response()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1), guild=SimpleNamespace(id=111), response=response
        )
        await bot.Bot.feed(fake, interaction)
        self.assertEqual(a.remaining, 5)
        self.assertEqual(b.remaining, 4)
        self.assertEqual(response.sent, ["fed. budget's full again."])

    async def test_feed_is_owner_only_and_needs_a_config(self):
        book = bot.BudgetBook(TZ)
        fake = SimpleNamespace(
            config=SimpleNamespace(owner_id=1),
            store=SimpleNamespace(maybe_reload=lambda: None, get=lambda gid: None),
            budgets=book,
        )
        response = self.Response()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=2), guild=SimpleNamespace(id=111), response=response
        )
        await bot.Bot.feed(fake, interaction)
        self.assertEqual(response.sent, ["nope"])

        interaction.user.id = 1
        response.sent.clear()
        await bot.Bot.feed(fake, interaction)
        self.assertIn("isn't set up", response.sent[0])


# --------------------------------------------------------------------------- #
# channels / activity
# --------------------------------------------------------------------------- #


class TestChannelActivityPerGuild(unittest.TestCase):
    def test_same_channel_id_in_two_guilds_is_separate(self):
        a = bot.ChannelActivity(
            burst_count=100, burst_window_seconds=1000, cooldown_seconds=10, rng=FakeRNG(0.5)
        )
        self.assertTrue(a.should_interject(("g1", "c"), 0.9, 1))
        self.assertFalse(a.should_interject(("g1", "c"), 0.9, 2))  # cooldown, g1 only
        self.assertTrue(a.should_interject(("g2", "c"), 0.9, 2))   # other guild unaffected

    def test_per_call_parameters_from_guild_config(self):
        a = bot.ChannelActivity(
            burst_count=100, burst_window_seconds=1000, cooldown_seconds=10, rng=FakeRNG(1.0)
        )
        self.assertFalse(a.should_interject("c", 0.0, 0, burst_count=1))
        self.assertTrue(a.should_interject("c", 0.0, 1, burst_count=1))


class TestChannelGates(unittest.TestCase):
    def test_allowed_channels(self):
        cfg = g.GuildConfig(name="x", allowed_channels=[10])
        self.assertTrue(bot.channel_active(cfg, channel(10)))
        self.assertFalse(bot.channel_active(cfg, channel(11)))
        self.assertTrue(bot.channel_active(g.GuildConfig(name="x"), channel(11)))

    def test_quiet_channels_include_parent_category(self):
        cfg = g.GuildConfig(name="x", quiet_channels=[20])
        self.assertFalse(bot.channel_active(cfg, channel(30, parent=channel(20))))
        self.assertTrue(bot.channel_active(cfg, channel(30)))
        self.assertFalse(bot.channel_active(None, channel(30)))


# --------------------------------------------------------------------------- #
# DM / unconfigured guilds
# --------------------------------------------------------------------------- #


class TestDmAndUnconfigured(unittest.IsolatedAsyncioTestCase):
    class FakeBot:
        def __init__(self, cfg):
            self.config = SimpleNamespace(owner_id=7)
            self.store = SimpleNamespace(maybe_reload=lambda: None, get=lambda gid: cfg)
            self.calls = []
            self.notified = []

        async def reply(self, message, trigger, charged, cfg, guild_key):
            self.calls.append((trigger, charged, str(guild_key)))

        async def _notify_unconfigured(self, guild):
            self.notified.append(guild.id)
            return True

    async def test_dm_replies_to_owner_only_and_never_charges(self):
        fake = self.FakeBot(None)
        owner_msg = SimpleNamespace(guild=None, author=SimpleNamespace(id=7), content="hi")
        other_msg = SimpleNamespace(guild=None, author=SimpleNamespace(id=8), content="hi")
        await bot.Bot._on_human_message(fake, owner_msg)
        await bot.Bot._on_human_message(fake, other_msg)
        self.assertEqual(fake.calls, [("dm", False, g.DM_GUILD_ID)])

    async def test_unconfigured_guild_gets_no_reply(self):
        fake = self.FakeBot(None)
        message = SimpleNamespace(
            guild=SimpleNamespace(id=42),
            channel=channel(1),
            author=SimpleNamespace(id=7),
            content="hello",
            mentions=[],
            reference=None,
        )
        await bot.Bot._on_human_message(fake, message)
        self.assertEqual(fake.calls, [])
        self.assertEqual(fake.notified, [42])

    async def test_unconfigured_notice_only_once_per_process(self):
        class Owner:
            def __init__(self):
                self.sent = []

            async def send(self, text):
                self.sent.append(text)

        owner = Owner()
        fake = SimpleNamespace(
            config=SimpleNamespace(owner_id=7),
            get_user=lambda uid: owner,
            _notified_unconfigured=set(),
        )
        guild = SimpleNamespace(id=42, name="頭腦警察")
        self.assertTrue(await bot.Bot._notify_unconfigured(fake, guild))
        self.assertFalse(await bot.Bot._notify_unconfigured(fake, guild))
        self.assertEqual(len(owner.sent), 1)
        self.assertIn("頭腦警察", owner.sent[0])
        self.assertIn("/setup there", owner.sent[0])


# --------------------------------------------------------------------------- #
# chatty sending
# --------------------------------------------------------------------------- #


class TestChatPlan(unittest.TestCase):
    def test_normal_style_is_one_reply(self):
        self.assertEqual(bot.chat_plan("one short line"), [("reply", "one short line", 0.0)])

    def test_chatty_style_splits_turns(self):
        plan = bot.chat_plan("first\n[[next]]\nsecond", chatty=True)
        self.assertEqual(plan[0], ("reply", "first", 0.0))
        self.assertEqual(plan[1][0], "send")
        self.assertEqual(plan[1][1], "second")
        self.assertAlmostEqual(plan[1][2], persona.typing_delay("second"))

    def test_chatty_still_honours_discord_limit_and_sanitises(self):
        plan = bot.chat_plan("@everyone x " * 400, chatty=True)
        self.assertEqual(plan[0][0], "reply")
        self.assertTrue(all(len(text) <= bot.DISCORD_MSG_LIMIT for _, text, _ in plan))
        self.assertFalse(any("@everyone" in text for _, text, _ in plan))

    def test_empty(self):
        self.assertEqual(bot.chat_plan("", chatty=True), [])


# --------------------------------------------------------------------------- #
# /setup
# --------------------------------------------------------------------------- #


class TestSetupPermissions(unittest.TestCase):
    def test_owner_with_manage_guild(self):
        self.assertIsNone(setup_ui.permission_error(1, 1, True))

    def test_non_owner_is_refused_even_with_manage_guild(self):
        self.assertIn("主人", setup_ui.permission_error(2, 1, True))

    def test_missing_manage_guild_is_refused(self):
        self.assertIn("管理服务器", setup_ui.permission_error(1, 1, False))
        self.assertIn("管理服务器", setup_ui.permission_error(2, 1, False))

    def test_has_manage_guild_reads_interaction_permissions(self):
        interaction = SimpleNamespace(permissions=SimpleNamespace(manage_guild=True))
        self.assertTrue(setup_ui.has_manage_guild(interaction))
        self.assertFalse(setup_ui.has_manage_guild(SimpleNamespace(permissions=None)))


class TestSetupForm(unittest.TestCase):
    def _base(self):
        return g.GuildConfig.model_validate(
            {
                "name": "群",
                "trust": "owner",
                "allowed_channels": [7],
                "quiet_channels": [1],
                "budget": {"replies_per_day": 50, "lookups_per_day": 0, "reset_time": "08:30"},
                "extra_prompt": "be nice",
                "features": {
                    "tools": {"on": True},
                    "topics": {"on": False, "count": 4,
                               "feeds": [{"name": "n", "url": "https://example.com/feed"}]},
                },
            }
        )

    def test_form_roundtrip(self):
        form = setup_ui.form_from_config(self._base())
        self.assertEqual(form.trust, "owner")
        self.assertEqual(form.recap_time, "22:00")
        cfg = setup_ui.apply_form(self._base(), form)
        self.assertEqual(
            cfg.model_dump(), self._base().model_dump()
        )  # unchanged form => unchanged config

    def test_form_to_config(self):
        form = setup_ui.SetupForm(
            trust="public",
            persona="turtle",
            style="chatty",
            language="zh",
            replies_per_day=20,
            lookups_per_day=3,
            interject_on=False,
            interject_chance=0.12,
            quiet_channels=[5, 6],
            recap_on=True,
            recap_channel=9,
            recap_time="23:15",
            topics_on=True,
            topics_channel=8,
            topics_time="07:45",
            consent=True,
        )
        cfg = setup_ui.apply_form(self._base(), form)
        self.assertEqual(str(cfg.trust), "public")
        self.assertEqual((cfg.persona, cfg.style, cfg.language), ("turtle", "chatty", "zh"))
        self.assertEqual((cfg.budget.replies_per_day, cfg.budget.lookups_per_day), (20, 3))
        self.assertEqual(cfg.quiet_channels, [5, 6])
        self.assertFalse(cfg.features.interject.on)
        self.assertAlmostEqual(cfg.features.interject.chance, 0.12)
        self.assertTrue(cfg.features.recap.on)
        self.assertEqual((cfg.features.recap.channel, cfg.features.recap.time), (9, "23:15"))
        self.assertTrue(cfg.features.topics.on)
        self.assertEqual((cfg.features.topics.channel, cfg.features.topics.time), (8, "07:45"))
        self.assertTrue(cfg.consent)
        # not in the menu: preserved from the existing config
        self.assertEqual(cfg.allowed_channels, [7])
        self.assertEqual(cfg.budget.reset_time, "08:30")
        self.assertEqual(cfg.extra_prompt, "be nice")
        self.assertEqual(cfg.features.topics.count, 4)
        self.assertEqual(len(cfg.features.topics.feeds), 1)
        # public cannot keep tools on
        self.assertFalse(cfg.features.tools.on)

    def test_owner_form_keeps_tools(self):
        form = setup_ui.form_from_config(self._base())
        self.assertTrue(setup_ui.apply_form(self._base(), form).features.tools.on)

    def test_bad_time_is_reported(self):
        base = self._base()
        form = setup_ui.form_from_config(base)
        form.recap_time = "25:00"
        with self.assertRaises(g.ConfigError):
            setup_ui.apply_form(base, form)

    def test_parse_helpers(self):
        self.assertTrue(setup_ui.parse_switch("开"))
        self.assertFalse(setup_ui.parse_switch("off"))
        with self.assertRaises(ValueError):
            setup_ui.parse_switch("maybe")
        self.assertAlmostEqual(setup_ui.parse_percent("3%"), 0.03)
        self.assertAlmostEqual(setup_ui.parse_percent("12"), 0.12)
        with self.assertRaises(ValueError):
            setup_ui.parse_percent("101")
        self.assertEqual(setup_ui.parse_time("07:05"), "07:05")
        with self.assertRaises(ValueError):
            setup_ui.parse_time("7:5")

    def test_default_config_for_new_guild(self):
        cfg = setup_ui.default_config("新群")
        self.assertEqual(cfg.name, "新群")
        self.assertEqual(str(cfg.trust), "public")
        self.assertFalse(cfg.consent)
        self.assertTrue(cfg.features.chat.on)


class TestSetupStart(unittest.IsolatedAsyncioTestCase):
    class Response:
        def __init__(self):
            self.calls = []

        async def send_message(self, content=None, **kwargs):
            self.calls.append((content, kwargs))

    def _interaction(self, user_id):
        response = self.Response()
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=111, name="新群"),
            user=SimpleNamespace(id=user_id),
            permissions=SimpleNamespace(manage_guild=True),
            response=response,
        )
        return interaction, response

    def _personas(self, tmp):
        root = Path(tmp)
        (root / "tiredfx").mkdir(parents=True)
        (root / "tiredfx" / "character.md").write_text("x", encoding="utf-8")
        (root / "_styles").mkdir()
        (root / "_styles" / "normal.md").write_text("x", encoding="utf-8")
        return root

    async def test_refusal_and_form_are_ephemeral(self):
        store = SimpleNamespace(maybe_reload=lambda: None, get=lambda gid: None)
        interaction, response = self._interaction(user_id=2)
        await setup_ui.start_setup(
            interaction, owner_id=1, store=store, personas_dir="/nonexistent"
        )
        self.assertEqual(len(response.calls), 1)
        self.assertTrue(response.calls[0][1]["ephemeral"])

        with tempfile.TemporaryDirectory() as tmp:
            interaction, response = self._interaction(user_id=1)
            await setup_ui.start_setup(
                interaction, owner_id=1, store=store, personas_dir=self._personas(tmp)
            )
            content, kwargs = response.calls[0]
            self.assertIn("服务器设置", content)
            self.assertTrue(kwargs["ephemeral"])
            self.assertIsInstance(kwargs["view"], setup_ui.SetupView)

    async def test_dm_is_refused(self):
        response = self.Response()
        interaction = SimpleNamespace(
            guild=None,
            user=SimpleNamespace(id=1),
            permissions=None,
            response=response,
        )
        await setup_ui.start_setup(
            interaction, owner_id=1, store=SimpleNamespace(), personas_dir="/nonexistent"
        )
        self.assertTrue(response.calls[0][1]["ephemeral"])


class TestSetupToolsAndPersonas(unittest.TestCase):
    def test_tools_greyed_out_for_public(self):
        self.assertFalse(setup_ui.tools_available("public"))
        self.assertTrue(setup_ui.tools_available("owner"))
        self.assertIn("不可用", setup_ui.tools_status("public"))
        self.assertIn("可用", setup_ui.tools_status("owner"))

    def test_personas_skip_underscore_and_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tiredfx").mkdir()
            (root / "tiredfx" / "character.md").write_text("x", encoding="utf-8")
            (root / "turtle").mkdir()
            (root / "turtle" / "character.md").write_text("x", encoding="utf-8")
            (root / "_styles").mkdir()
            (root / "_styles" / "normal.md").write_text("x", encoding="utf-8")
            (root / "empty").mkdir()
            (root / "_base.md").write_text("x", encoding="utf-8")
            self.assertEqual(setup_ui.list_personas(root), ["tiredfx", "turtle"])
            self.assertEqual(setup_ui.list_styles(root), ["normal"])
            self.assertTrue(setup_ui.persona_exists(root, "turtle"))
            self.assertFalse(setup_ui.persona_exists(root, "_base"))


# --------------------------------------------------------------------------- #
# system prompt: persona + profile blocks
# --------------------------------------------------------------------------- #


class TestSystemPrompt(unittest.TestCase):
    def setUp(self):
        persona.clear_cache()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.personas = root / "personas"
        (self.personas / "tiredfx").mkdir(parents=True)
        (self.personas / "tiredfx" / "character.md").write_text("CHAR-ACTER\n", encoding="utf-8")
        (self.personas / "_styles").mkdir()
        (self.personas / "_styles" / "normal.md").write_text("STYLE-NORMAL\n", encoding="utf-8")
        (self.personas / "_base.md").write_text("BASE-FLOOR\n", encoding="utf-8")

        self.memory = guildmem.GuildMemory(root / "guilds" / "111")
        self.memory.ensure()
        self.memory.seed_kurt("KURT-NOTES")
        self.memory.write_block("people", "PEOPLE-NOTES")
        self.memory.write_block("threads", "THREAD-NOTES")
        self.memory.write_block("jokes", "JOKE-NOTES")

    def tearDown(self):
        self._tmp.cleanup()

    def _cfg(self, **kw):
        kw.setdefault("consent", True)
        return g.GuildConfig(name="x", **kw)

    def test_chat_notes_use_kurt_people_jokes(self):
        notes = bot.group_notes(self.memory, "chat", consent=True)
        system = bot.build_system(self.personas, self._cfg(), notes=notes, now=NOW)
        self.assertIn("CHAR-ACTER", system)
        self.assertIn("BASE-FLOOR", system)
        self.assertIn("STYLE-NORMAL", system)
        self.assertIn("Group notes", system)
        self.assertIn("KURT-NOTES", system)
        self.assertIn("PEOPLE-NOTES", system)
        self.assertIn("JOKE-NOTES", system)
        self.assertNotIn("THREAD-NOTES", system)
        self.assertIn("November 14 2026", system)
        self.assertIn("<@ID>", system)

    def test_interject_notes_use_kurt_and_jokes(self):
        notes = bot.group_notes(self.memory, "interject", consent=True)
        self.assertIn("KURT-NOTES", notes)
        self.assertIn("JOKE-NOTES", notes)
        self.assertNotIn("PEOPLE-NOTES", notes)
        self.assertNotIn("THREAD-NOTES", notes)

    def test_consent_off_means_no_notes(self):
        self.assertEqual(bot.group_notes(self.memory, "chat", consent=False), "")
        system = bot.build_system(self.personas, self._cfg(consent=False), now=NOW)
        self.assertNotIn("Group notes", system)

    def test_untrusted_addendum_only_with_external_data(self):
        notes = bot.group_notes(self.memory, "chat", consent=True)
        plain = bot.build_system(self.personas, self._cfg(), notes=notes, now=NOW)
        external = bot.build_system(
            self.personas, self._cfg(), notes=notes, include_untrusted=True, now=NOW
        )
        self.assertNotIn("untrusted data", plain)
        self.assertIn("untrusted data", external)

    def test_notes_stay_inside_the_token_budget(self):
        # kurt.md is Kurt's file (the bot's API is read-only), so the fixture writes it directly.
        (self.memory.profile_dir / "kurt.md").write_text("K" * 4000 + "\n", encoding="utf-8")
        self.memory.write_block("people", "P" * 600)
        notes = bot.group_notes(self.memory, "chat", consent=True)
        self.assertLessEqual(guildmem.estimate_tokens(notes), g.FEATURES["chat"].token_budget)
        self.assertIn("K", notes)  # truncated, not dropped

    def test_feature_off_or_missing_persona(self):
        with self.assertRaises(FileNotFoundError):
            bot.build_system(self.personas, self._cfg(persona="nobody"), now=NOW)


# --------------------------------------------------------------------------- #
# /rule card reflects this guild
# --------------------------------------------------------------------------- #


class TestGuildRulesCard(unittest.TestCase):
    def test_card_shows_guild_settings_without_ids(self):
        cfg = g.GuildConfig.model_validate(
            {
                "name": "x",
                "trust": "owner",
                "features": {
                    "interject": {"on": False},
                    "recap": {"on": True, "channel": 123456789012345678, "time": "21:30"},
                    "topics": {"on": False},
                },
            }
        )
        card = bot.build_rules_card(cfg, remaining=4, blocked_names=["quiet-here"], owner_name="Kurt")
        self.assertIn("4/50", card)
        self.assertIn("21:30", card)
        self.assertIn("quiet-here", card)
        self.assertIn("Kurt", card)
        self.assertIn("don't butt in", card)
        self.assertNotRegex(card, r"\d{15,}")  # channel IDs stay out of the card


if __name__ == "__main__":
    unittest.main()
