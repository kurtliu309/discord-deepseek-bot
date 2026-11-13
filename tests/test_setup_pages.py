#!/usr/bin/env python3
"""/setup page buttons and news-inside-topics (v3.2 UI cleanup).

Covers: the ``[Page 1] [Page 2] [Page 3] [English]`` navigation row on every page
(including Discord's five-row / five-button limits), the removal of the old
"more settings / members / back" buttons, and the news fields now living in the
topics modal (parse, validate, write back to ``features.news``).
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

import guildcfg as g
import setup_ui

OWNER = 1


class FakeGuild:
    id = 111
    name = "New server"

    def get_channel(self, channel_id):
        return SimpleNamespace(name=f"ch{channel_id}")

    def get_member(self, user_id):
        return None


class FakeResponse:
    def __init__(self):
        self.edits = []
        self.messages = []
        self.response = self

    async def edit_message(self, content=None, view=None):
        self.edits.append((content, view))

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))


def fake_interaction(user_id=OWNER):
    response = FakeResponse()
    interaction = SimpleNamespace(
        guild=FakeGuild(),
        user=SimpleNamespace(id=user_id, display_name=f"user{user_id}", name=f"user{user_id}"),
        response=response,
    )
    return interaction, response


def make_personas(tmp):
    root = Path(tmp)
    (root / "tiredfx").mkdir(parents=True, exist_ok=True)
    (root / "tiredfx" / "character.md").write_text("x", encoding="utf-8")
    (root / "_styles").mkdir(exist_ok=True)
    (root / "_styles" / "normal.md").write_text("x", encoding="utf-8")
    return root


def make_view(tmp, **cfg):
    store = g.GuildConfigStore(tmp)
    base = g.GuildConfig(name="New server", trust=g.Trust.OWNER, **cfg)
    store.save("111", base)
    return store, setup_ui.SetupView(
        guild=FakeGuild(),
        base=base,
        form=setup_ui.form_from_config(base),
        store=store,
        personas_dir=make_personas(tmp),
        owner_id=OWNER,
        actor_id=OWNER,
    )


def buttons(view):
    return [i for i in view.children if isinstance(i, discord.ui.Button)]


def labels(view):
    return [getattr(i, "label", None) for i in view.children]


def rows(view):
    out = {}
    for item in view.children:
        out.setdefault(item.row, []).append(item)
    return out


def page_button(view, page, lang="zh"):
    return next(
        b for b in buttons(view) if b.label == setup_ui.t(lang, "btn_page", page=page)
    )


class TestPageLayout(unittest.TestCase):
    def test_all_pages_fit_discords_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            for page in (1, 2, 3):
                view.page = page
                view._build()
                by_row = rows(view)
                self.assertLessEqual(max(by_row), 4, f"page {page} uses more than 5 rows")
                for row, items in by_row.items():
                    self.assertLessEqual(
                        sum(i.width for i in items), 5, f"page {page} row {row} overflows"
                    )
                    self.assertLessEqual(
                        len([i for i in items if isinstance(i, discord.ui.Button)]), 5
                    )

    def test_every_page_has_the_three_page_buttons_and_the_language_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            for page in (1, 2, 3):
                view.page = page
                view._build()
                for target in (1, 2, 3):
                    button = page_button(view, target)
                    self.assertEqual(button.disabled, target == page)
                    self.assertEqual(
                        button.style,
                        discord.ButtonStyle.primary
                        if target == page
                        else discord.ButtonStyle.secondary,
                    )
                self.assertIn("English", labels(view))
                # all four sit on the last row, in order
                self.assertEqual(
                    [b.label for b in buttons(view) if b.row == 4],
                    ["第 1 页", "第 2 页", "第 3 页", "English"],
                )

    def test_action_rows_are_the_expected_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            expected = {
                1: ["每日额度", "插话设置", "保存", "取消"],
                2: ["回顾设置", "话题设置", "隐私确认", "保存", "取消"],
                3: ["保存", "取消"],
            }
            for page, want in expected.items():
                view.page = page
                view._build()
                self.assertEqual([b.label for b in buttons(view) if b.row == 3], want)

    def test_the_old_page_buttons_are_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            gone = {"更多设置", "成员名单", "返回", "More settings", "Members", "Back"}
            for page in (1, 2, 3):
                view.page = page
                view._build()
                self.assertFalse(gone & set(labels(view)), f"page {page} still has an old button")

    def test_english_page_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp, setup_language="en")
            view._build()
            self.assertEqual(
                [b.label for b in buttons(view) if b.row == 4],
                ["Page 1", "Page 2", "Page 3", "中文"],
            )
            self.assertTrue(page_button(view, 1, "en").disabled)

    def test_page_hint_explains_all_three_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            content = view.render()
            self.assertIn("第 1 页：基础设置", content)
            self.assertIn("第 2 页：频道和定时", content)
            self.assertIn("第 3 页：名单", content)
            view.setup_language = "en"
            content = view.render()
            self.assertIn("Page 1:", content)
            self.assertIn("Page 2:", content)
            self.assertIn("Page 3:", content)


class TestPageNavigation(unittest.IsolatedAsyncioTestCase):
    async def test_page_buttons_jump_and_keep_unsaved_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, view = make_view(tmp)
            view.form.persona = "turtle"  # picked but not saved yet
            view.form.news_items = 9
            interaction, response = fake_interaction()

            await page_button(view, 2).callback(interaction)
            self.assertEqual(view.page, 2)
            await page_button(view, 3).callback(interaction)
            self.assertEqual(view.page, 3)
            await page_button(view, 1).callback(interaction)
            self.assertEqual(view.page, 1)

            self.assertEqual(view.form.persona, "turtle")
            self.assertEqual(view.form.news_items, 9)
            self.assertEqual(store.get("111").persona, "tiredfx")  # nothing saved yet

    async def test_clicking_the_current_page_just_redraws_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            interaction, response = fake_interaction()
            await view._page_button(1)(interaction)
            self.assertEqual(view.page, 1)
            self.assertEqual(len(response.edits), 1)


class FakeModalInteraction:
    def __init__(self):
        self.edits = []
        self.messages = []
        self.response = self

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))

    async def edit_message(self, content=None, view=None):
        self.edits.append((content, view))


class TestTopicsModal(unittest.IsolatedAsyncioTestCase):
    def _modal(self, tmp):
        _, view = make_view(tmp)
        return view, setup_ui.TopicsModal(view)

    def test_has_five_inputs_with_the_current_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, modal = self._modal(tmp)
            self.assertEqual(len(modal.children), 5)
            self.assertEqual(
                [str(i._underlying.label) for i in modal.children],
                ["开关（开/关）", "时间（HH:MM）", "新闻开关（开/关）",
                 "新闻时间（HH:MM）", "新闻条数（1-50）"],
            )
            self.assertEqual(
                [str(i.default) for i in modal.children],
                ["关", "09:00", "关", "09:00", "5"],
            )

    def test_title_mentions_topics_and_news(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, modal = self._modal(tmp)
            self.assertEqual(modal.title, "每日话题和新闻")
            _, en_view = make_view(tmp, setup_language="en")
            self.assertEqual(setup_ui.TopicsModal(en_view).title, "Daily topics & news")

    async def test_submit_updates_the_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            view, modal = self._modal(tmp)
            view.form.topics_channel = 400
            for item, value in zip(modal.children, ("开", "08:00", "开", "08:30", "7")):
                item._value = value
            interaction = FakeModalInteraction()
            await modal.on_submit(interaction)
            f = view.form
            self.assertEqual((f.topics_on, f.topics_time), (True, "08:00"))
            self.assertEqual((f.news_on, f.news_time, f.news_items), (True, "08:30", 7))
            self.assertEqual(len(interaction.edits), 1)

    async def test_bad_time_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            view, modal = self._modal(tmp)
            view.form.topics_channel = 400
            for item, value in zip(modal.children, ("开", "09:00", "开", "8:3", "7")):
                item._value = value
            interaction = FakeModalInteraction()
            await modal.on_submit(interaction)
            self.assertEqual(
                interaction.messages[0][0], setup_ui.t("zh", "err_time")
            )
            self.assertFalse(view.form.news_on)

    async def test_news_items_must_be_1_to_50(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("0", "51", "many"):
                view, modal = self._modal(tmp)
                view.form.topics_channel = 400
                for item, value in zip(modal.children, ("开", "09:00", "开", "09:00", bad)):
                    item._value = value
                interaction = FakeModalInteraction()
                await modal.on_submit(interaction)
                self.assertEqual(
                    interaction.messages[0][0], setup_ui.t("zh", "err_items"), bad
                )
                self.assertEqual(view.form.news_items, 5)

    def test_parse_items_accepts_the_limits(self):
        self.assertEqual(setup_ui.parse_items("1"), 1)
        self.assertEqual(setup_ui.parse_items(" 50 "), 50)

    async def test_news_on_without_a_topics_channel_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            view, modal = self._modal(tmp)
            self.assertIsNone(view.form.topics_channel)
            for item, value in zip(modal.children, ("关", "09:00", "开", "09:00", "5")):
                item._value = value
            interaction = FakeModalInteraction()
            await modal.on_submit(interaction)
            self.assertEqual(
                interaction.messages[0][0], setup_ui.t("zh", "err_news_channel")
            )
            self.assertFalse(view.form.news_on)


class TestNewsConfig(unittest.TestCase):
    def test_form_defaults_come_from_the_config(self):
        cfg = g.GuildConfig(
            name="N",
            features={"news": {"on": True, "channel": 400, "time": "07:15", "items": 8}},
        )
        form = setup_ui.form_from_config(cfg)
        self.assertEqual((form.news_on, form.news_time, form.news_items), (True, "07:15", 8))

    def test_defaults_keep_news_off(self):
        form = setup_ui.form_from_config(g.GuildConfig(name="N"))
        self.assertEqual((form.news_on, form.news_time, form.news_items), (False, "09:00", 5))

    def test_saving_writes_news_and_uses_the_topics_channel(self):
        base = g.GuildConfig(name="N")
        form = setup_ui.form_from_config(base)
        form.topics_channel = 400
        form.topics_on = True
        form.news_on = True
        form.news_time = "08:30"
        form.news_items = 7
        cfg = setup_ui.apply_form(base, form)
        self.assertEqual(cfg.features.news.channel, 400)
        self.assertEqual(cfg.features.news.channel, cfg.features.topics.channel)
        self.assertEqual(
            (cfg.features.news.on, cfg.features.news.time, cfg.features.news.items),
            (True, "08:30", 7),
        )

    def test_clearing_the_topics_channel_clears_the_news_channel(self):
        base = g.GuildConfig(
            name="N",
            features={"news": {"on": False, "channel": 400, "time": "09:00", "items": 5}},
        )
        form = setup_ui.form_from_config(base)
        form.topics_channel = None
        cfg = setup_ui.apply_form(base, form)
        self.assertIsNone(cfg.features.news.channel)

    def test_save_fails_when_news_is_on_without_a_topics_channel(self):
        base = g.GuildConfig(name="N")
        form = setup_ui.form_from_config(base)
        form.news_on = True
        with self.assertRaises(g.ConfigError) as ctx:
            setup_ui.apply_form(base, form)
        self.assertIn(setup_ui.t("zh", "err_news_channel"), str(ctx.exception))
        with self.assertRaises(g.ConfigError) as ctx:
            setup_ui.apply_form(base, form, lang="en")
        self.assertIn(setup_ui.t("en", "err_news_channel"), str(ctx.exception))

    def test_save_button_reports_the_missing_channel(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                store, view = make_view(tmp)
                view.form.news_on = True
                interaction, response = fake_interaction()
                await view._on_save(interaction)
                self.assertEqual(len(response.edits), 0)
                self.assertIn(
                    setup_ui.t("zh", "err_news_channel"), response.messages[0][0]
                )
                self.assertFalse(store.get("111").features.news.on)

        asyncio.run(run())

    def test_save_through_the_store_sets_the_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, view = make_view(tmp)
            view.form.topics_channel = 400
            view.form.news_on = True
            view.form.news_items = 3
            cfg = setup_ui.save_form(store, "111", view.base, view.form)
            self.assertEqual(store.get("111").features.news.channel, 400)
            self.assertEqual(cfg.features.news.items, 3)

    def test_news_status_line_is_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            view.form.topics_channel = 400
            view.form.news_on = True
            view.form.news_time = "08:30"
            view.form.news_items = 7
            content = view.render()
            self.assertIn("新闻：开", content)
            self.assertIn("#ch400", content)
            self.assertIn("08:30", content)
            self.assertIn("7 条", content)

            view.setup_language = "en"
            content = view.render()
            self.assertIn("News: on", content)
            self.assertIn("7 items", content)

    def test_news_off_is_rendered_as_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            self.assertIn("新闻：关", view.render())

    def test_owner_notice_lists_the_news_changes(self):
        old = g.GuildConfig(name="N")
        new = g.GuildConfig(
            name="N",
            features={
                "topics": {"channel": 400},
                "news": {"on": True, "channel": 400, "time": "08:30", "items": 7},
            },
        )
        diff = dict(setup_ui.config_diff(old, new))
        self.assertEqual(diff["news.on"], "false → true")
        self.assertEqual(diff["news.channel"], "none → 400")
        self.assertEqual(diff["news.time"], "09:00 → 08:30")
        self.assertEqual(diff["news.items"], "5 → 7")
        notice = setup_ui.change_notice(
            old, new, guild_name="N", actor_name="Kurt", lang="zh"
        )
        self.assertIn("- news.items: 5 → 7", notice)


if __name__ == "__main__":
    unittest.main()


class TestUserSelectWithoutMemberCache(unittest.IsolatedAsyncioTestCase):
    """No members intent: get_member() is None, picks must still stick and show names."""

    async def test_picked_admins_stay_ticked_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp)
            interaction, response = fake_interaction()
            interaction.data = {
                "values": ["222", "333"],
                "resolved": {
                    "users": {"222": {"username": "gf", "global_name": "Girlfriend"},
                              "333": {"username": "pal"}},
                    "members": {"333": {"nick": "Pal Nick"}},
                },
            }
            view.page = 3
            await view._on_admins(interaction)
            self.assertEqual(view.form.admins, [222, 333])
            text = view.render()
            self.assertIn("Girlfriend", text)
            self.assertIn("Pal Nick", text)
            view.page = 3
            view._build()
            admins = next(i for i in view.children
                          if isinstance(i, discord.ui.UserSelect) and i.row == 1)
            self.assertEqual([d.id for d in admins.default_values], [222, 333])

    def test_saved_admins_are_preselected_by_id_when_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = make_view(tmp, admins=[444])
            view.page = 3
            view._build()
            admins = next(i for i in view.children
                          if isinstance(i, discord.ui.UserSelect) and i.row == 1)
            self.assertEqual([d.id for d in admins.default_values], [444])
            self.assertIn("<@444>", view.render())
