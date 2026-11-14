#!/usr/bin/env python3
"""Blacklist, guild admins, owner-only fields and the save notification.

Covers: config fields (old configs keep defaults), the owner being unblockable and
always an admin, blocked people getting no reply/interjection and not feeding the
burst counter, admin permissions and the server-side forcing of owner-only fields,
the pure config diff, the owner DM after a save, and the /setup component layout.
"""

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord
import yaml

import bot
import guildcfg as g
import setup_ui

OWNER = 1
ADMIN = 2
BLOCKED = 3


class FakeRNG:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def channel(cid, parent=None):
    return SimpleNamespace(id=cid, parent=parent)


class FakeMember:
    def __init__(self, user_id, name=None):
        self.id = user_id
        self.display_name = name or f"user{user_id}"
        self.name = self.display_name


class FakeGuild:
    def __init__(self, guild_id=111, name="New server", members=()):
        self.id = guild_id
        self.name = name
        self._members = {int(m.id): m for m in members if m is not None}

    def get_channel(self, channel_id):
        return SimpleNamespace(name=f"ch{channel_id}")

    def get_member(self, user_id):
        return self._members.get(int(user_id))


def make_config(**overrides):
    data = {"name": "群"}
    data.update(overrides)
    return g.GuildConfig.model_validate(data)


def make_personas(tmp):
    root = Path(tmp)
    (root / "tiredfx").mkdir(parents=True, exist_ok=True)
    (root / "tiredfx" / "character.md").write_text("x", encoding="utf-8")
    (root / "_styles").mkdir(exist_ok=True)
    (root / "_styles" / "normal.md").write_text("x", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# config fields
# --------------------------------------------------------------------------- #


class TestConfigFields(unittest.TestCase):
    def test_old_config_without_fields_gets_empty_lists(self):
        cfg = g.parse_config({"name": "old", "trust": "owner"})
        self.assertEqual(cfg.blocked_users, [])
        self.assertEqual(cfg.admins, [])

    def test_old_yaml_file_without_fields_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("name: old\ntrust: owner\n", encoding="utf-8")
            cfg = g.load_config_file(path)
            self.assertEqual((cfg.blocked_users, cfg.admins), ([], []))

    def test_ids_are_ints_even_when_yaml_says_strings(self):
        cfg = g.parse_config({"blocked_users": ["3"], "admins": ["2"]})
        self.assertEqual(cfg.blocked_users, [3])
        self.assertEqual(cfg.admins, [2])

    def test_bad_ids_are_rejected(self):
        with self.assertRaises(g.ConfigError):
            g.parse_config({"blocked_users": ["alice"]})

    def test_roundtrip(self):
        cfg = g.parse_config({"blocked_users": [3], "admins": [2]})
        text = g.dump_config(cfg)
        self.assertIn("blocked_users", text)
        self.assertIn("admins", text)
        self.assertEqual(g.parse_config(yaml.safe_load(text)), cfg)


class TestOwnerRules(unittest.TestCase):
    def test_owner_in_blocked_list_is_ignored(self):
        cfg = make_config(blocked_users=[OWNER, BLOCKED])
        self.assertFalse(g.is_blocked(cfg, OWNER, OWNER))
        self.assertTrue(g.is_blocked(cfg, BLOCKED, OWNER))
        self.assertFalse(g.is_blocked(None, BLOCKED, OWNER))

    def test_owner_is_always_admin(self):
        cfg = make_config(admins=[ADMIN])
        self.assertTrue(g.is_admin(cfg, OWNER, OWNER))
        self.assertTrue(g.is_admin(cfg, ADMIN, OWNER))
        self.assertFalse(g.is_admin(cfg, BLOCKED, OWNER))
        self.assertFalse(g.is_admin(None, ADMIN, OWNER))

    def test_without_owner_drops_owner_and_dedupes(self):
        self.assertEqual(g.without_owner([OWNER, 5, 5, 6], OWNER), [5, 6])
        self.assertEqual(g.without_owner([5], None), [5])
        self.assertEqual(g.without_owner(None, OWNER), [])

    def test_store_ignores_the_owner_when_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "111"
            folder.mkdir()
            (folder / "config.yaml").write_text(
                f"name: x\nblocked_users: [{OWNER}, {BLOCKED}]\nadmins: [{OWNER}, {ADMIN}]\n",
                encoding="utf-8",
            )
            store = g.GuildConfigStore(tmp, owner_id=OWNER)
            cfg = store.get("111")
            self.assertEqual(cfg.blocked_users, [BLOCKED])
            self.assertEqual(cfg.admins, [ADMIN])

    def test_store_never_writes_the_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = g.GuildConfigStore(tmp, owner_id=OWNER)
            store.save("111", make_config(
                blocked_users=[OWNER, BLOCKED], admins=[OWNER, ADMIN]
            ))
            text = (Path(tmp) / "111" / "config.yaml").read_text(encoding="utf-8")
            self.assertNotIn(f"- {OWNER}", text)
            self.assertEqual(store.get("111").admins, [ADMIN])


# --------------------------------------------------------------------------- #
# blacklist behaviour
# --------------------------------------------------------------------------- #


class FakeBot:
    def __init__(self, cfg, owner_id=OWNER, mentions=False, burst_count=2):
        self.config = SimpleNamespace(owner_id=owner_id)
        self.store = SimpleNamespace(maybe_reload=lambda: None, get=lambda gid: cfg)
        self.calls = []
        self._mentions = mentions
        self.personas_dir = "/nonexistent"  # no persona.yaml: traits fall back to defaults
        self.channel_activity = bot.ChannelActivity(
            burst_count=burst_count,
            burst_window_seconds=600,
            cooldown_seconds=600,
            rng=FakeRNG(1.0),
        )

    async def _mentions_or_replies_to_me(self, message):
        return self._mentions

    async def reply(self, message, trigger, charged, cfg, guild_key):
        self.calls.append((trigger, message.author.id))


def message(user_id, content=""):
    return SimpleNamespace(
        guild=SimpleNamespace(id=42),
        channel=channel(1),
        author=SimpleNamespace(id=user_id),
        content=content,
        mentions=[],
        reference=None,
    )


class TestBlacklistBehaviour(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_user_is_not_answered_even_when_mentioned(self):
        cfg = make_config(blocked_users=[BLOCKED])
        fake = FakeBot(cfg, mentions=True)
        await bot.Bot._on_human_message(fake, message(BLOCKED, "hey bot"))
        self.assertEqual(fake.calls, [])
        await bot.Bot._on_human_message(fake, message(9, "hey bot"))
        self.assertEqual(fake.calls, [("mention", 9)])

    async def test_blocked_messages_do_not_count_towards_the_burst(self):
        cfg = make_config(
            blocked_users=[BLOCKED],
            features={"interject": {"chance": 0.0, "burst_count": 2}},
        )
        fake = FakeBot(cfg)
        for _ in range(3):
            await bot.Bot._on_human_message(fake, message(BLOCKED, "talk talk"))
        self.assertEqual(fake.calls, [])
        self.assertEqual(fake.channel_activity._timestamps.get(("42", "1"), []), [])

        # Three allowed messages: the burst fires on the third (len 3 > 2). If the
        # blocked messages had been recorded it would have fired on the second.
        await bot.Bot._on_human_message(fake, message(9, "one"))
        await bot.Bot._on_human_message(fake, message(9, "two"))
        self.assertEqual(fake.calls, [])
        await bot.Bot._on_human_message(fake, message(9, "three"))
        self.assertEqual(fake.calls, [("interject", 9)])

    async def test_owner_is_never_blocked(self):
        cfg = make_config(blocked_users=[OWNER])
        fake = FakeBot(cfg, mentions=True)
        await bot.Bot._on_human_message(fake, message(OWNER, "hey bot"))
        self.assertEqual(fake.calls, [("mention", OWNER)])

    async def test_dms_still_only_answer_the_owner(self):
        cfg = make_config(blocked_users=[BLOCKED])
        fake = FakeBot(cfg, mentions=True)
        await bot.Bot._on_human_message(
            fake, SimpleNamespace(guild=None, author=SimpleNamespace(id=BLOCKED), content="hi")
        )
        self.assertEqual(fake.calls, [])


# --------------------------------------------------------------------------- #
# permissions
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self):
        self.messages = []
        self.edits = []

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))

    async def edit_message(self, content=None, view=None):
        self.edits.append((content, view))


def fake_interaction(user_id, *, manage_guild=True, guild=None):
    response = FakeResponse()
    interaction = SimpleNamespace(
        guild=guild if guild is not None else FakeGuild(),
        user=SimpleNamespace(id=user_id, display_name=f"user{user_id}", name=f"user{user_id}"),
        permissions=SimpleNamespace(manage_guild=manage_guild),
        response=response,
        data={},
    )
    return interaction, response


class TestAdminPermissions(unittest.TestCase):
    def test_admin_and_owner_may_use_setup(self):
        self.assertIsNone(setup_ui.permission_error(OWNER, OWNER, True))
        self.assertIsNone(setup_ui.permission_error(ADMIN, OWNER, True, admins=[ADMIN]))
        self.assertIsNone(setup_ui.permission_error(OWNER, OWNER, True, admins=[]))

    def test_stranger_is_refused(self):
        self.assertIn("admin", setup_ui.permission_error(9, OWNER, True, "en", admins=[ADMIN]))
        self.assertIn("主人", setup_ui.permission_error(9, OWNER, True, admins=[ADMIN]))

    def test_admin_still_needs_manage_guild(self):
        self.assertEqual(
            setup_ui.permission_error(ADMIN, OWNER, False, "en", admins=[ADMIN]),
            setup_ui.t("en", "perm_no_manage"),
        )


class TestSetupStartAdmins(unittest.IsolatedAsyncioTestCase):
    def _store(self, tmp, **cfg):
        store = g.GuildConfigStore(tmp)
        store.save("111", make_config(**cfg))
        return store

    async def test_admin_gets_the_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, admins=[ADMIN])
            interaction, response = fake_interaction(ADMIN)
            await setup_ui.start_setup(
                interaction, owner_id=OWNER, store=store, personas_dir="/nonexistent"
            )
            content, kwargs = response.messages[0]
            self.assertIn("服务器设置", content)
            self.assertTrue(kwargs["ephemeral"])
            view = kwargs["view"]
            self.assertFalse(view.is_owner)

    async def test_stranger_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, admins=[ADMIN])
            interaction, response = fake_interaction(9)
            await setup_ui.start_setup(
                interaction, owner_id=OWNER, store=store, personas_dir="/nonexistent"
            )
            self.assertEqual(response.messages[0][0], setup_ui.t("zh", "perm_not_owner"))

    async def test_owner_gets_the_owner_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            interaction, response = fake_interaction(OWNER)
            await setup_ui.start_setup(
                interaction, owner_id=OWNER, store=store, personas_dir="/nonexistent"
            )
            self.assertTrue(response.messages[0][1]["view"].is_owner)


# --------------------------------------------------------------------------- #
# admin can save, but cannot touch trust / admins
# --------------------------------------------------------------------------- #


def make_view(tmp, *, actor_id=None, notify_owner=None, **cfg):
    store = g.GuildConfigStore(tmp)
    base = make_config(**cfg)
    store.save("111", base)
    view = setup_ui.SetupView(
        guild=FakeGuild(members=[FakeMember(OWNER, "Kurt"), FakeMember(ADMIN, "Alice"),
                                 FakeMember(BLOCKED, "Bob")]),
        base=base,
        form=setup_ui.form_from_config(base),
        store=store,
        personas_dir=make_personas(tmp),
        owner_id=OWNER,
        actor_id=actor_id,
        notify_owner=notify_owner,
    )
    return store, view


class TestServerSideOwnerOnlyFields(unittest.TestCase):
    def test_admin_form_cannot_change_trust_or_admins(self):
        base = make_config(trust="public", admins=[ADMIN])
        form = setup_ui.form_from_config(base)
        form.trust = "owner"
        form.admins = [BLOCKED]
        form.persona = "chatty"
        cfg = setup_ui.apply_form(base, form, is_owner=False, owner_id=OWNER)
        self.assertEqual(str(cfg.trust), "public")  # forced back
        self.assertEqual(cfg.admins, [ADMIN])       # forced back
        self.assertEqual(cfg.persona, "chatty")     # other fields do change

    def test_admin_can_change_the_blacklist(self):
        base = make_config(admins=[ADMIN])
        form = setup_ui.form_from_config(base)
        form.blocked_users = [BLOCKED]
        cfg = setup_ui.apply_form(base, form, is_owner=False, owner_id=OWNER)
        self.assertEqual(cfg.blocked_users, [BLOCKED])

    def test_owner_can_change_trust_and_admins_but_never_saves_self(self):
        base = make_config(trust="public", admins=[])
        form = setup_ui.form_from_config(base)
        form.trust = "owner"
        form.admins = [ADMIN, OWNER]
        form.blocked_users = [BLOCKED, OWNER]
        cfg = setup_ui.apply_form(base, form, is_owner=True, owner_id=OWNER)
        self.assertEqual(str(cfg.trust), "owner")
        self.assertEqual(cfg.admins, [ADMIN])
        self.assertEqual(cfg.blocked_users, [BLOCKED])

    async def test_admin_view_save_forces_trust_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, view = make_view(
                tmp, actor_id=ADMIN, trust="public", admins=[ADMIN]
            )
            view.form.trust = "owner"
            view.form.admins = [BLOCKED]
            view.form.blocked_users = [BLOCKED]
            interaction, response = fake_interaction(ADMIN)
            await view._on_save(interaction)
            saved = store.get("111")
            self.assertEqual(str(saved.trust), "public")
            self.assertEqual(saved.admins, [ADMIN])
            self.assertEqual(saved.blocked_users, [BLOCKED])
            self.assertEqual(len(response.edits), 1)


# --------------------------------------------------------------------------- #
# diff + notification
# --------------------------------------------------------------------------- #


class TestConfigDiff(unittest.TestCase):
    def test_only_changed_fields_are_listed(self):
        old = make_config(features={"interject": {"chance": 0.03}})
        new = make_config(
            features={"interject": {"chance": 0.10}},
            blocked_users=[42],
        )
        diff = dict(setup_ui.config_diff(old, new, user_name={42: "Alice"}))
        self.assertEqual(
            diff,
            {"interject.chance": "0.03 → 0.10", "blocked_users": "+ Alice"},
        )

    def test_name_lists_show_adds_and_removes(self):
        old = make_config(blocked_users=[42, 43])
        new = make_config(blocked_users=[43, 44])
        diff = dict(setup_ui.config_diff(
            old, new, user_name={42: "Alice", 43: "Bob", 44: "Carol"}
        ))
        self.assertEqual(diff["blocked_users"], "+ Carol, - Alice")

    def test_unknown_names_never_fall_back_to_ids(self):
        old = make_config(admins=[])
        new = make_config(admins=[123456789012345678])
        diff = dict(setup_ui.config_diff(old, new))
        self.assertEqual(diff["admins"], "+ <未知用户>")
        self.assertNotIn("123456789012345678", diff["admins"])
        diff_en = dict(setup_ui.config_diff(old, new, unknown="unknown user"))
        self.assertEqual(diff_en["admins"], "+ unknown user")

    def test_no_changes_is_an_empty_diff(self):
        cfg = make_config(blocked_users=[BLOCKED], admins=[ADMIN])
        self.assertEqual(setup_ui.config_diff(cfg, cfg.model_copy()), [])

    def test_owner_is_ignored_in_both_name_lists(self):
        old = make_config(blocked_users=[], admins=[])
        new = make_config(blocked_users=[OWNER], admins=[OWNER])
        self.assertEqual(setup_ui.config_diff(old, new, owner_id=OWNER), [])
        # ... and a hand-edited owner entry disappearing is not a change either
        self.assertEqual(
            setup_ui.config_diff(new, old, owner_id=OWNER), []
        )

    def test_scalar_formatting(self):
        old = make_config(budget={"replies_per_day": 50},
                          features={"recap": {"on": False}})
        new = make_config(budget={"replies_per_day": 60},
                          features={"recap": {"on": True}})
        diff = dict(setup_ui.config_diff(old, new))
        self.assertEqual(diff["budget.replies_per_day"], "50 → 60")
        self.assertEqual(diff["recap.on"], "false → true")


class TestChangeNotice(unittest.TestCase):
    def test_no_change_returns_none(self):
        cfg = make_config()
        self.assertIsNone(setup_ui.change_notice(
            cfg, cfg.model_copy(), guild_name="群", actor_name="Kurt"
        ))

    def test_chinese_notice(self):
        old = make_config(features={"interject": {"chance": 0.03}})
        new = make_config(features={"interject": {"chance": 0.10}},
                          blocked_users=[42])
        notice = setup_ui.change_notice(
            old, new, guild_name="頭腦警察", actor_name="Kurt", lang="zh",
            user_name={42: "Alice"},
        )
        self.assertTrue(notice.startswith("頭腦警察 的设置被 Kurt 改了："))
        self.assertIn("- interject.chance: 0.03 → 0.10", notice)
        self.assertIn("- blocked_users: + Alice", notice)
        self.assertNotIn("42", notice)

    def test_english_notice(self):
        old = make_config(budget={"replies_per_day": 50})
        new = make_config(budget={"replies_per_day": 80})
        notice = setup_ui.change_notice(
            old, new, guild_name="Studio", actor_name="Alice", lang="en"
        )
        self.assertTrue(notice.startswith("Studio's settings were changed by Alice:"))
        self.assertIn("- budget.replies_per_day: 50 → 80", notice)


class TestSaveNotification(unittest.IsolatedAsyncioTestCase):
    def _view(self, tmp, sent, *, actor_id=None, **cfg):
        async def notify(text):
            sent.append(text)

        return make_view(tmp, actor_id=actor_id, notify_owner=notify, **cfg)

    async def test_unchanged_save_sends_no_dm(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent = []
            store, view = self._view(tmp, sent, admins=[ADMIN])
            interaction, _ = fake_interaction(OWNER)
            await view._on_save(interaction)
            self.assertEqual(sent, [])

    async def test_changed_save_dms_the_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent = []
            # an admin (not the owner) saves a normal field plus the blacklist
            store, view = self._view(tmp, sent, actor_id=ADMIN, admins=[ADMIN])
            view.form.blocked_users = [BLOCKED]
            view.form.interject_chance = 0.10
            interaction, _ = fake_interaction(ADMIN)
            await view._on_save(interaction)
            self.assertEqual(len(sent), 1)
            text = sent[0]
            self.assertTrue(text.startswith("New server 的设置被 user2 改了："))
            self.assertIn("- interject.chance: 0.03 → 0.10", text)
            self.assertIn("- blocked_users: + Bob", text)

    async def test_owner_saving_also_notifies_and_uses_guild_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent = []
            store, view = self._view(
                tmp, sent, actor_id=OWNER, setup_language="en"
            )
            view.form.interject_chance = 0.10
            interaction, _ = fake_interaction(OWNER)
            await view._on_save(interaction)
            self.assertEqual(len(sent), 1)
            self.assertTrue(
                sent[0].startswith("New server's settings were changed by user1:")
            )

    async def test_a_failed_dm_is_only_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, view = make_view(tmp)
            view.form.interject_chance = 0.10

            async def broken(text):
                raise RuntimeError("DMs closed")

            view.notify_owner = broken
            interaction, response = fake_interaction(OWNER)
            await view._on_save(interaction)  # must not raise
            self.assertEqual(len(response.edits), 1)
            self.assertAlmostEqual(store.get("111").features.interject.chance, 0.10)


# --------------------------------------------------------------------------- #
# /rule card
# --------------------------------------------------------------------------- #


class TestRulesCardHidesLists(unittest.TestCase):
    def test_card_shows_admin_count_only(self):
        cfg = make_config(
            admins=[111111111111111111, 222222222222222222],
            blocked_users=[333333333333333333],
        )
        card = bot.build_rules_card(cfg, blocked_names=["general"])
        self.assertIn("admins: 2", card)
        self.assertNotIn("111111111111111111", card)
        self.assertNotIn("333333333333333333", card)
        self.assertNotRegex(card, r"\d{15,}")
        for name in ("Alice", "Bob"):
            self.assertNotIn(name, card)


# --------------------------------------------------------------------------- #
# /setup layout
# --------------------------------------------------------------------------- #


class TestSetupLayout(unittest.IsolatedAsyncioTestCase):
    def _views(self, tmp):
        owner_store, owner_view = make_view(tmp, admins=[ADMIN])
        admin_store = g.GuildConfigStore(tmp)
        base = make_config(admins=[ADMIN], blocked_users=[BLOCKED])
        admin_view = setup_ui.SetupView(
            guild=FakeGuild(members=[FakeMember(ADMIN, "Alice"), FakeMember(BLOCKED, "Bob")]),
            base=base,
            form=setup_ui.form_from_config(base),
            store=admin_store,
            personas_dir=make_personas(tmp),
            owner_id=OWNER,
            actor_id=ADMIN,
        )
        return owner_view, admin_view

    def test_all_pages_fit_discords_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            for view in self._views(tmp):
                for page in (1, 2, 3):
                    view.page = page
                    view._build()
                    widths = {}
                    for item in view.children:
                        widths[item.row] = widths.get(item.row, 0) + item.width
                    self.assertLessEqual(max(widths), 4, f"page {page} uses row {max(widths)}")
                    self.assertLessEqual(max(widths.values()), 5, f"page {page} overflows")
                    self.assertIn(
                        "English", [getattr(i, "label", None) for i in view.children]
                    )

    def test_page_three_has_both_member_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner_view, admin_view = self._views(tmp)
            for view, disabled in ((owner_view, False), (admin_view, True)):
                view.page = 3
                view._build()
                users = [i for i in view.children if isinstance(i, discord.ui.UserSelect)]
                self.assertEqual(len(users), 2)
                self.assertFalse(users[0].disabled)       # blocked list: admins may edit
                self.assertEqual(users[1].disabled, disabled)  # admins list: owner only

    def test_owner_only_controls_are_disabled_for_admins(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner_view, admin_view = self._views(tmp)
            for view, is_owner in ((owner_view, True), (admin_view, False)):
                view.page = 1
                view._build()
                trust = [i for i in view.children if isinstance(i, discord.ui.Select)][0]
                self.assertEqual(trust.disabled, not is_owner)
                self.assertEqual(
                    "只有主人能改" in view.render(), not is_owner
                )

    async def test_admin_cannot_select_itself_into_the_admin_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, admin_view = self._views(tmp)
            admin_view.page = 3
            admin_view._build()
            update = SimpleNamespace(edit_message=self._edit())
            interaction = SimpleNamespace(
                data={"values": [str(BLOCKED)]}, response=update
            )
            await admin_view._on_admins(interaction)
            self.assertEqual(admin_view.form.admins, [ADMIN])  # ignored

    async def test_blacklist_select_updates_the_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, admin_view = self._views(tmp)
            admin_view.page = 3
            admin_view._build()
            update = SimpleNamespace(edit_message=self._edit())
            interaction = SimpleNamespace(
                data={"values": [str(9), str(10)]}, response=update
            )
            await admin_view._on_blocked(interaction)
            self.assertEqual(admin_view.form.blocked_users, [9, 10])

    async def test_page_buttons_jump_straight_to_a_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner_view, _ = self._views(tmp)
            interaction = SimpleNamespace(response=SimpleNamespace(edit_message=self._edit()))
            buttons = {
                getattr(i, "label", None): i
                for i in owner_view.children
                if isinstance(i, discord.ui.Button)
            }
            await buttons["第 3 页"].callback(interaction)
            self.assertEqual(owner_view.page, 3)
            await buttons["第 1 页"].callback(interaction)
            self.assertEqual(owner_view.page, 1)
            await buttons["第 2 页"].callback(interaction)
            self.assertEqual(owner_view.page, 2)

    @staticmethod
    def _edit():
        async def edit_message(content=None, view=None):
            return None

        return edit_message


class TestFeedAllowsAdmins(unittest.IsolatedAsyncioTestCase):
    class Response:
        def __init__(self):
            self.sent = []

        async def send_message(self, content, **kwargs):
            self.sent.append(content)

    async def test_admin_can_feed_and_strangers_cannot(self):
        book = bot.BudgetBook(dt.timezone.utc)
        cfg = make_config(admins=[ADMIN], budget={"replies_per_day": 5})
        store = SimpleNamespace(maybe_reload=lambda: None, get=lambda gid: cfg)
        ledger = book.ledger("111", "09:00", 5)
        ledger.replies.spend()
        fake = SimpleNamespace(
            config=SimpleNamespace(owner_id=OWNER),
            store=store,
            budgets=book,
            _ledger=lambda key, c: book.ledger(
                key,
                c.budget.reset_time,
                c.budget.replies_per_day,
                c.budget.replies_per_user,
                c.budget.lookups_per_day,
                c.budget.lookups_per_user,
            ),
        )

        response = self.Response()
        admin = SimpleNamespace(
            user=SimpleNamespace(id=ADMIN), guild=SimpleNamespace(id=111), response=response
        )
        await bot.Bot.feed(fake, admin)
        self.assertEqual(ledger.replies.remaining, 5)
        self.assertIn("fed", response.sent[0])

        ledger.replies.spend()
        stranger = SimpleNamespace(
            user=SimpleNamespace(id=9), guild=SimpleNamespace(id=111), response=response
        )
        await bot.Bot.feed(fake, stranger)
        self.assertEqual(response.sent[-1], "nope")
        self.assertEqual(ledger.replies.remaining, 4)


class TestStringTables(unittest.TestCase):
    def test_both_languages_have_the_same_keys(self):
        self.assertEqual(set(setup_ui.TEXT["zh"]), set(setup_ui.TEXT["en"]))

    def test_new_keys_exist_in_both_languages(self):
        for key in (
            "blocked_line",
            "admins_line",
            "owner_only",
            "owner_only_line",
            "select_blocked",
            "select_admins",
            "btn_page",
            "dm_changed_header",
            "unknown_user",
        ):
            self.assertIn(key, setup_ui.TEXT["zh"])
            self.assertIn(key, setup_ui.TEXT["en"])


if __name__ == "__main__":
    unittest.main()
