"""v3 budgets: guild cap plus per-person cap, refills, owner DMs, /feed and /setup."""

import asyncio
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bot
import guildcfg as g
import setup_ui

TZ = dt.timezone.utc
MORNING = dt.datetime(2026, 11, 14, 8, 0, tzinfo=TZ)


def cfg_with(**budget):
    return g.GuildConfig(name="Guild", budget=budget)


def make_ledger(book, cfg, key="111"):
    b = cfg.budget
    return book.ledger(
        key, b.reset_time, b.replies_per_day, b.replies_per_user,
        b.lookups_per_day, b.lookups_per_user,
    )


class FakeBot(bot.Bot):
    """A real Bot's methods without ``discord.Client.__init__`` or the model."""

    def __init__(self, book, cfg=None, owner_id=1, guild_name="Guild"):
        self.config = SimpleNamespace(owner_id=owner_id, tz=TZ)
        self.budgets = book
        self.store = SimpleNamespace(get=lambda gid: cfg if str(gid) == "111" else None)
        self.dms = []
        self.notices = []
        self._dm_tasks = set()
        self.guild_name = guild_name

    async def _dm_owner(self, text):
        self.dms.append(text)

    async def _send_out_of_budget(self, message):
        self.notices.append(message)

    def get_user(self, user_id):
        return SimpleNamespace(id=user_id, display_name=f"user{user_id}", name=f"user{user_id}")

    async def fetch_user(self, user_id):
        return self.get_user(user_id)


def message(author_id=5, guild_name="Guild"):
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id, display_name="Ann", name="Ann"),
        guild=SimpleNamespace(name=guild_name),
        content="hi",
    )


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


class TestBudgetSettings(unittest.TestCase):
    def test_new_per_person_defaults(self):
        cfg = g.GuildConfig()
        self.assertEqual(cfg.budget.replies_per_user, 30)
        self.assertEqual(cfg.budget.lookups_per_user, 20)

    def test_old_config_without_the_new_fields_uses_defaults(self):
        cfg = g.parse_config({"name": "old", "budget": {"replies_per_day": 7}})
        self.assertEqual(cfg.budget.replies_per_day, 7)
        self.assertEqual(cfg.budget.replies_per_user, 30)
        self.assertEqual(cfg.budget.lookups_per_day, 0)
        self.assertEqual(cfg.budget.lookups_per_user, 20)

    def test_values_roundtrip_through_yaml(self):
        cfg = g.parse_config(
            {"name": "x", "budget": {"replies_per_day": 100, "replies_per_user": 10}}
        )
        self.assertIn("replies_per_user: 10", g.dump_config(cfg))
        with self.assertRaises(g.ConfigError):
            g.parse_config({"name": "x", "budget": {"replies_per_user": -1}})


# --------------------------------------------------------------------------- #
# counters
# --------------------------------------------------------------------------- #


class TestTwoTierLimits(unittest.TestCase):
    def test_guild_limit_can_be_the_first_to_stop_someone(self):
        ledger = bot.GuildLedger(reply_limit=2, reply_per_user=10)
        self.assertTrue(bot.Bot._charge_reply(ledger, 1))
        self.assertTrue(bot.Bot._charge_reply(ledger, 2))
        self.assertFalse(bot.Bot._charge_reply(ledger, 3))  # guild empty
        self.assertEqual(ledger.replies_by_user.remaining(3), 10)  # person untouched

    def test_person_limit_can_be_the_first_to_stop_someone(self):
        ledger = bot.GuildLedger(reply_limit=10, reply_per_user=2)
        self.assertTrue(bot.Bot._charge_reply(ledger, 1))
        self.assertTrue(bot.Bot._charge_reply(ledger, 1))
        self.assertFalse(bot.Bot._charge_reply(ledger, 1))  # Ann is capped
        self.assertEqual(ledger.replies.remaining, 8)  # guild still has budget
        self.assertTrue(bot.Bot._charge_reply(ledger, 2))  # others unaffected
        self.assertEqual(ledger.replies_by_user.remaining(2), 1)

    def test_owner_and_dms_are_never_charged(self):
        owner = 1
        self.assertFalse(bot.should_charge("mention", owner, owner))
        self.assertFalse(bot.should_charge("dm", owner, owner))
        self.assertTrue(bot.should_charge("mention", 2, owner))

        cfg = cfg_with(lookups_per_day=5, lookups_per_user=2)
        fake = FakeBot(bot.BudgetBook(TZ, clock=lambda: MORNING), cfg)
        ledger = make_ledger(fake.budgets, cfg)
        self.assertTrue(bot.Bot.try_charge_lookup(fake, "111", owner))
        self.assertEqual(ledger.lookups.remaining, 5)  # free
        self.assertEqual(ledger.lookups_by_user.remaining(owner), 2)

    def test_refund_gives_back_both_counters(self):
        ledger = bot.GuildLedger(reply_limit=5, reply_per_user=1)
        self.assertTrue(bot.Bot._charge_reply(ledger, 1))
        self.assertFalse(bot.Bot._charge_reply(ledger, 1))
        bot.Bot._refund_reply(ledger, 1)
        self.assertEqual(ledger.replies.remaining, 5)
        self.assertEqual(ledger.replies_by_user.remaining(1), 1)


class TestReset(unittest.TestCase):
    def test_reset_clears_the_guild_and_every_person(self):
        now = [MORNING]
        book = bot.BudgetBook(TZ, clock=lambda: now[0])
        cfg = cfg_with(replies_per_day=5, replies_per_user=2)
        ledger = make_ledger(book, cfg)
        bot.Bot._charge_reply(ledger, 1)
        bot.Bot._charge_reply(ledger, 1)
        bot.Bot._charge_reply(ledger, 2)

        now[0] = dt.datetime(2026, 11, 14, 9, 0, 1, tzinfo=TZ)
        self.assertEqual(book.refill_due(), ["111"])
        self.assertEqual(ledger.replies.remaining, 5)
        self.assertEqual(ledger.replies_by_user.remaining(1), 2)
        self.assertEqual(ledger.replies_by_user.remaining(2), 2)

    def test_access_after_reset_also_refills(self):
        now = [MORNING]
        book = bot.BudgetBook(TZ, clock=lambda: now[0])
        cfg = cfg_with(replies_per_day=5, replies_per_user=2)
        ledger = make_ledger(book, cfg)
        bot.Bot._charge_reply(ledger, 1)
        now[0] = dt.datetime(2026, 11, 14, 9, 0, 1, tzinfo=TZ)
        self.assertEqual(make_ledger(book, cfg).replies_by_user.remaining(1), 2)


# --------------------------------------------------------------------------- #
# owner DMs
# --------------------------------------------------------------------------- #


class TestOwnerNotifications(unittest.IsolatedAsyncioTestCase):
    def test_message_wording(self):
        self.assertEqual(
            bot.budget_used_up_message("Guild", "reply", user_name="Ann", limit=30),
            "Guild: Ann used up their daily reply limit (30/30).",
        )
        self.assertEqual(
            bot.budget_used_up_message("Guild", "reply", limit=100),
            "Guild: daily reply budget used up (100/100).",
        )
        self.assertEqual(
            bot.budget_used_up_message("Guild", "lookup", user_name="Ann", limit=20),
            "Guild: Ann used up their daily lookup limit (20/20).",
        )

    async def test_person_and_guild_dm_once_per_cycle(self):
        cfg = cfg_with(replies_per_day=5, replies_per_user=1)
        fake = FakeBot(bot.BudgetBook(TZ, clock=lambda: MORNING), cfg)
        ledger = make_ledger(fake.budgets, cfg)
        user = SimpleNamespace(id=5, display_name="Ann", name="Ann")

        bot.Bot._charge_reply(ledger, 5)
        await bot.Bot._announce_used_up(fake, "Guild", user, ledger)
        self.assertEqual(
            fake.dms,
            ["Guild: Ann used up their daily reply limit (1/1)."],
        )

        # the guild total reaching zero is a separate DM, and neither repeats
        ledger.replies.remaining = 0
        await bot.Bot._announce_used_up(fake, "Guild", user, ledger)
        self.assertEqual(fake.dms[1], "Guild: daily reply budget used up (5/5).")
        await bot.Bot._announce_used_up(fake, "Guild", user, ledger)
        self.assertEqual(len(fake.dms), 2)

        # a new cycle may notify again
        ledger.refill()
        bot.Bot._charge_reply(ledger, 5)
        await bot.Bot._announce_used_up(fake, "Guild", user, ledger)
        self.assertEqual(len(fake.dms), 3)

    async def test_only_the_first_blocked_mention_gets_the_notice(self):
        cfg = cfg_with(replies_per_day=1, replies_per_user=1)
        fake = FakeBot(bot.BudgetBook(TZ, clock=lambda: MORNING), cfg)
        make_ledger(fake.budgets, cfg).refill()  # create the ledger
        ledger = make_ledger(fake.budgets, cfg)
        bot.Bot._charge_reply(ledger, 5)  # uses both up

        msg = message(5)
        await bot.Bot.reply(fake, msg, "mention", True, cfg, "111")
        await bot.Bot.reply(fake, msg, "mention", True, cfg, "111")
        self.assertEqual(fake.notices, [msg])  # once, not twice
        self.assertEqual(fake.dms, [])  # the DM happens when it is spent, not when blocked

    async def test_lookup_dms_are_queued_once(self):
        cfg = cfg_with(lookups_per_day=1, lookups_per_user=1)
        fake = FakeBot(bot.BudgetBook(TZ, clock=lambda: MORNING), cfg)

        self.assertTrue(bot.Bot.try_charge_lookup(fake, "111", 5))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(
            sorted(fake.dms),
            [
                "Guild: daily lookup budget used up (1/1).",
                "Guild: user5 used up their daily lookup limit (1/1).",
            ],
        )
        self.assertFalse(bot.Bot.try_charge_lookup(fake, "111", 6))  # guild empty
        await asyncio.sleep(0)
        self.assertEqual(len(fake.dms), 2)  # no repeat

    async def test_dm_failure_only_logs(self):
        class Owner:
            async def send(self, text):
                raise RuntimeError("DMs closed")

        fake = SimpleNamespace(
            config=SimpleNamespace(owner_id=1), get_user=lambda uid: Owner()
        )
        await bot.Bot._dm_owner(fake, "whatever")  # must not raise

    def test_lookup_charges_guild_and_person(self):
        cfg = cfg_with(lookups_per_day=5, lookups_per_user=1)
        fake = FakeBot(bot.BudgetBook(TZ, clock=lambda: MORNING), cfg)
        ledger = make_ledger(fake.budgets, cfg)

        self.assertTrue(bot.Bot.try_charge_lookup(fake, "111", 5))
        self.assertFalse(bot.Bot.try_charge_lookup(fake, "111", 5))  # Ann capped
        self.assertTrue(bot.Bot.try_charge_lookup(fake, "111", 6))
        self.assertEqual(ledger.lookups.remaining, 3)
        self.assertFalse(bot.Bot.try_charge_lookup(fake, "999", 5))  # no config


# --------------------------------------------------------------------------- #
# /feed
# --------------------------------------------------------------------------- #


class TestFeedCommand(unittest.IsolatedAsyncioTestCase):
    class Response:
        def __init__(self):
            self.sent = []

        async def send_message(self, content, **kwargs):
            self.sent.append(content)

    def _fake(self, book, cfg):
        return SimpleNamespace(
            config=SimpleNamespace(owner_id=1),
            store=SimpleNamespace(maybe_reload=lambda: None, get=lambda gid: cfg),
            budgets=book,
            _ledger=lambda key, c: make_ledger(book, c, str(key)),
        )

    async def test_feed_without_a_member_refills_the_whole_guild(self):
        book = bot.BudgetBook(TZ, clock=lambda: MORNING)
        cfg = cfg_with(replies_per_day=5, replies_per_user=2,
                       lookups_per_day=4, lookups_per_user=1)
        ledger = make_ledger(book, cfg)
        bot.Bot._charge_reply(ledger, 1)
        ledger.lookups_by_user.spend(1)
        ledger.lookups_by_user.spend(2)

        fake = self._fake(book, cfg)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1), guild=SimpleNamespace(id=111), response=self.Response()
        )
        await bot.Bot.feed(fake, interaction)

        self.assertEqual(ledger.replies.remaining, 5)
        self.assertEqual(ledger.replies_by_user.remaining(1), 2)
        self.assertEqual(ledger.lookups_by_user.remaining(1), 1)
        self.assertEqual(ledger.lookups_by_user.remaining(2), 1)
        self.assertEqual(interaction.response.sent, ["fed. budget's full again."])

    async def test_feed_one_member_resets_only_that_person(self):
        book = bot.BudgetBook(TZ, clock=lambda: MORNING)
        cfg = cfg_with(replies_per_day=5, replies_per_user=2,
                       lookups_per_day=4, lookups_per_user=1)
        ledger = make_ledger(book, cfg)
        for _ in range(2):
            bot.Bot._charge_reply(ledger, 1)
        bot.Bot._charge_reply(ledger, 2)
        ledger.lookups_by_user.spend(1)
        ledger.lookups.remaining = 3

        fake = self._fake(book, cfg)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1), guild=SimpleNamespace(id=111), response=self.Response()
        )
        member = SimpleNamespace(id=1, display_name="Ann", name="Ann")
        await bot.Bot.feed(fake, interaction, member)

        self.assertEqual(ledger.replies_by_user.remaining(1), 2)
        self.assertEqual(ledger.lookups_by_user.remaining(1), 1)
        # the guild total and other people are untouched
        self.assertEqual(ledger.replies.remaining, 2)
        self.assertEqual(ledger.replies_by_user.remaining(2), 1)
        self.assertEqual(ledger.lookups.remaining, 3)
        self.assertIn("Ann", interaction.response.sent[0])

    async def test_feed_still_owner_only(self):
        book = bot.BudgetBook(TZ)
        cfg = cfg_with()
        fake = self._fake(book, cfg)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=2), guild=SimpleNamespace(id=111), response=self.Response()
        )
        await bot.Bot.feed(fake, interaction)
        self.assertEqual(interaction.response.sent, ["nope"])


# --------------------------------------------------------------------------- #
# /setup and /rule
# --------------------------------------------------------------------------- #


class FakeGuild:
    id = 111
    name = "New server"

    def get_channel(self, channel_id):
        return SimpleNamespace(name=f"ch{channel_id}")


class FakeModalInteraction:
    def __init__(self):
        self.edits = []
        self.messages = []
        self.response = self

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))

    async def edit_message(self, content=None, view=None):
        self.edits.append((content, view))


class TestSetupPerPersonFields(unittest.IsolatedAsyncioTestCase):
    def _view(self, tmp):
        store = g.GuildConfigStore(tmp)
        base = g.GuildConfig(name="New server", trust=g.Trust.OWNER)
        store.save("111", base)
        return store, setup_ui.SetupView(
            guild=FakeGuild(),
            base=base,
            form=setup_ui.form_from_config(base),
            store=store,
            personas_dir="/nonexistent",
        )

    def test_form_carries_the_new_fields_and_applies_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, view = self._view(tmp)
            self.assertEqual(
                (view.form.replies_per_user, view.form.lookups_per_user), (30, 20)
            )

            view.form.replies_per_user = 7
            view.form.lookups_per_user = 3
            cfg = setup_ui.save_form(store, "111", view.base, view.form)
            self.assertEqual(
                (cfg.budget.replies_per_user, cfg.budget.lookups_per_user), (7, 3)
            )
            text = (Path(tmp) / "111" / "config.yaml").read_text(encoding="utf-8")
            self.assertIn("replies_per_user: 7", text)
            self.assertIn("lookups_per_user: 3", text)

    async def test_budget_modal_has_both_new_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = self._view(tmp)
            modal = setup_ui.BudgetModal(view)
            # replies, replies per person, lookups, lookups per person
            self.assertEqual(len(modal.children), 4)
            self.assertEqual(
                [str(item.default) for item in modal.children], ["50", "30", "0", "20"]
            )

            for item, value in zip(
                modal.children,
                ("9", "4", "6", "2"),
            ):
                item._value = value
            interaction = FakeModalInteraction()
            await modal.on_submit(interaction)
            self.assertEqual(
                (view.form.replies_per_day, view.form.replies_per_user,
                 view.form.lookups_per_day, view.form.lookups_per_user),
                (9, 4, 6, 2),
            )
            self.assertIn("9", interaction.edits[-1][0])

    def test_both_languages_have_the_new_strings(self):
        for key in ("field_replies_per_user", "field_lookups_per_user"):
            self.assertIn(key, setup_ui.TEXT["zh"])
            self.assertIn(key, setup_ui.TEXT["en"])
        self.assertEqual(set(setup_ui.TEXT["zh"]), set(setup_ui.TEXT["en"]))

    def test_render_shows_both_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = self._view(tmp)
            view.form.replies_per_day = 100
            view.form.replies_per_user = 10
            content = view.render()
            self.assertIn("100", content)
            self.assertIn("10", content)


class TestRulesCardTwoTiers(unittest.TestCase):
    def test_shows_guild_and_the_askers_own_remaining(self):
        cfg = g.GuildConfig(name="g", budget={"replies_per_day": 100, "replies_per_user": 30})
        card = bot.build_rules_card(cfg, remaining=40, user_remaining=7)
        self.assertIn("40/100", card)
        self.assertIn("7/30", card)
        self.assertNotRegex(card, r"\d{15,}")

    def test_defaults_show_a_full_person_budget(self):
        cfg = g.GuildConfig(name="g")
        card = bot.build_rules_card(cfg)
        self.assertIn("30/30", card)


if __name__ == "__main__":
    unittest.main()
