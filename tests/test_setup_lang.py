#!/usr/bin/env python3
"""/setup language switch: string table, toggle button, defaults, refusals."""

import string
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import guildcfg as g
import setup_ui


def format_fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class FakeResponse:
    def __init__(self):
        self.edits = []
        self.messages = []

    async def edit_message(self, content=None, view=None):
        self.edits.append((content, view))

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))


class FakeGuild:
    def __init__(self, guild_id=111, name="New server"):
        self.id = guild_id
        self.name = name

    def get_channel(self, channel_id):
        return SimpleNamespace(name=f"ch{channel_id}")


def fake_interaction(user_id=1, manage_guild=True, guild=None):
    response = FakeResponse()
    interaction = SimpleNamespace(
        guild=guild if guild is not None else FakeGuild(),
        user=SimpleNamespace(id=user_id),
        permissions=SimpleNamespace(manage_guild=manage_guild),
        response=response,
    )
    return interaction, response


def button_with_label(view, label):
    for item in view.children:
        if getattr(item, "label", None) == label:
            return item
    raise AssertionError(f"no button labelled {label!r}; have {[getattr(i, 'label', None) for i in view.children]}")


class TestTextTable(unittest.TestCase):
    def test_same_keys_in_both_languages(self):
        self.assertEqual(set(setup_ui.TEXT["zh"]), set(setup_ui.TEXT["en"]))

    def test_same_placeholders_in_both_languages(self):
        for key in setup_ui.TEXT["zh"]:
            self.assertEqual(
                format_fields(setup_ui.TEXT["zh"][key]),
                format_fields(setup_ui.TEXT["en"][key]),
                key,
            )

    def test_no_empty_strings(self):
        for lang, table in setup_ui.TEXT.items():
            for key, text in table.items():
                self.assertTrue(text.strip(), f"{lang}.{key} is empty")

    def test_t_falls_back_to_chinese(self):
        self.assertEqual(setup_ui.t("zh", "title"), setup_ui.TEXT["zh"]["title"])
        self.assertEqual(setup_ui.t("en", "btn_save"), "Save")
        self.assertEqual(setup_ui.t("fr", "btn_save"), setup_ui.TEXT["zh"]["btn_save"])
        self.assertEqual(setup_ui.t("en", "guild_name", name="X"), "Server: X")

    def test_toggle_labels_are_the_other_language(self):
        self.assertEqual(setup_ui.t("zh", "btn_language"), "English")
        self.assertEqual(setup_ui.t("en", "btn_language"), "中文")
        self.assertEqual(setup_ui.other_language("zh"), "en")
        self.assertEqual(setup_ui.other_language("en"), "zh")
        self.assertEqual(setup_ui.ui_language(None), "zh")
        self.assertEqual(setup_ui.ui_language("de"), "zh")


class TestConfigField(unittest.TestCase):
    def test_old_config_without_field_defaults_to_zh(self):
        self.assertEqual(g.parse_config({"name": "old", "trust": "owner"}).setup_language, "zh")
        self.assertEqual(g.GuildConfig().setup_language, "zh")

    def test_old_yaml_file_without_field_defaults_to_zh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("name: old\ntrust: owner\nlanguage: en\n", encoding="utf-8")
            cfg = g.load_config_file(path)
            self.assertEqual(cfg.setup_language, "zh")
            self.assertEqual(cfg.language, "en")  # reply language is a separate field

    def test_roundtrip_and_validation(self):
        cfg = g.parse_config({"name": "x", "setup_language": "en"})
        self.assertEqual(cfg.setup_language, "en")
        self.assertIn("setup_language: en", g.dump_config(cfg))
        with self.assertRaises(g.ConfigError):
            g.parse_config({"setup_language": "fr"})


class TestLanguageToggle(unittest.IsolatedAsyncioTestCase):
    def _view(self, tmp, **cfg):
        store = g.GuildConfigStore(tmp)
        base = g.GuildConfig(name="New server", trust=g.Trust.OWNER, **cfg)
        store.save("111", base)
        view = setup_ui.SetupView(
            guild=FakeGuild(),
            base=base,
            form=setup_ui.form_from_config(base),
            store=store,
            personas_dir="/nonexistent",
        )
        return store, view

    async def test_toggle_saves_language_and_keeps_unsaved_form_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, view = self._view(tmp)
            view.form.persona = "turtle"  # picked but not saved yet
            view.form.consent = True
            view.page = 2
            interaction, response = fake_interaction()

            await button_with_label(view, "English").callback(interaction)

            # config: only the language changed
            self.assertEqual(view.setup_language, "en")
            saved = store.get("111")
            self.assertEqual(saved.setup_language, "en")
            self.assertEqual(saved.persona, "tiredfx")
            self.assertFalse(saved.consent)
            yaml_text = (Path(tmp) / "111" / "config.yaml").read_text(encoding="utf-8")
            self.assertIn("setup_language: en", yaml_text)

            # form: pending choices survive, the same page is redrawn in English
            self.assertEqual(view.form.persona, "turtle")
            self.assertTrue(view.form.consent)
            self.assertEqual(view.page, 2)
            content, sent_view = response.edits[-1]
            self.assertIs(sent_view, view)
            self.assertIn("Server setup", content)
            self.assertIn("Persona: turtle", content)
            self.assertIn("Privacy consent: given", content)

            # and back again
            await button_with_label(view, "中文").callback(interaction)
            self.assertEqual(view.setup_language, "zh")
            self.assertEqual(store.get("111").setup_language, "zh")
            self.assertEqual(view.form.persona, "turtle")
            content, _ = response.edits[-1]
            self.assertIn("服务器设置", content)
            self.assertIn("人设：turtle", content)

    async def test_both_pages_show_the_toggle_and_fit_discords_five_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, view = self._view(tmp)
            for page in (1, 2):
                view.page = page
                view._build()
                self.assertIn("English", [getattr(i, "label", None) for i in view.children])
                widths = {}
                for item in view.children:
                    widths[item.row] = widths.get(item.row, 0) + item.width
                self.assertLessEqual(max(widths), 4, f"page {page} uses row {max(widths)}")
                self.assertLessEqual(max(widths.values()), 5, f"page {page} overflows a row")

    async def test_saved_config_keeps_the_chosen_ui_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, view = self._view(tmp)
            interaction, _ = fake_interaction()
            await button_with_label(view, "English").callback(interaction)

            # saving the form afterwards must not lose setup_language
            cfg = setup_ui.save_form(store, "111", view.base, view.form)
            self.assertEqual(cfg.setup_language, "en")
            self.assertEqual(store.get("111").setup_language, "en")


class TestPermissionLanguage(unittest.IsolatedAsyncioTestCase):
    def _store(self, tmp, setup_language=None):
        store = g.GuildConfigStore(tmp)
        if setup_language is not None:
            store.save("111", g.GuildConfig(name="S", setup_language=setup_language))
        return store

    async def test_refusal_uses_the_guilds_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "en")
            interaction, response = fake_interaction(user_id=2)
            await setup_ui.start_setup(
                interaction, owner_id=1, store=store, personas_dir="/nonexistent"
            )
            self.assertEqual(response.messages[0][0], setup_ui.t("en", "perm_not_owner"))
            self.assertTrue(response.messages[0][1]["ephemeral"])

    async def test_missing_manage_guild_uses_the_guilds_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "en")
            interaction, response = fake_interaction(user_id=1, manage_guild=False)
            await setup_ui.start_setup(
                interaction, owner_id=1, store=store, personas_dir="/nonexistent"
            )
            self.assertEqual(response.messages[0][0], setup_ui.t("en", "perm_no_manage"))

    async def test_unconfigured_guild_is_refused_in_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            interaction, response = fake_interaction(user_id=2)
            await setup_ui.start_setup(
                interaction, owner_id=1, store=store, personas_dir="/nonexistent"
            )
            self.assertEqual(response.messages[0][0], setup_ui.t("zh", "perm_not_owner"))

    async def test_zh_guild_is_refused_in_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "zh")
            interaction, response = fake_interaction(user_id=2)
            await setup_ui.start_setup(
                interaction, owner_id=1, store=store, personas_dir="/nonexistent"
            )
            self.assertIn("主人", response.messages[0][0])

    def test_permission_error_takes_a_language(self):
        self.assertIsNone(setup_ui.permission_error(1, 1, True, "en"))
        self.assertIn("Manage Server", setup_ui.permission_error(1, 1, False, "en"))
        self.assertIn("owner", setup_ui.permission_error(2, 1, True, "en"))
        self.assertIn("管理服务器", setup_ui.permission_error(1, 1, False))

    def test_tools_status_takes_a_language(self):
        self.assertIn("unavailable", setup_ui.tools_status("public", "en"))
        self.assertIn("available", setup_ui.tools_status("owner", "en"))
        self.assertIn("不可用", setup_ui.tools_status("public"))
        self.assertIn("可用", setup_ui.tools_status("owner"))


class TestEnglishForm(unittest.IsolatedAsyncioTestCase):
    async def test_form_opens_in_the_guilds_saved_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = g.GuildConfigStore(tmp)
            store.save("111", g.GuildConfig(name="S", setup_language="en"))
            interaction, response = fake_interaction(user_id=1)
            await setup_ui.start_setup(
                interaction, owner_id=1, store=store, personas_dir="/nonexistent"
            )
            content, kwargs = response.messages[0]
            self.assertIn("Server setup", content)
            self.assertIn("Trust level:", content)
            self.assertIsInstance(kwargs["view"], setup_ui.SetupView)
            self.assertEqual(kwargs["view"].setup_language, "en")

    def test_parse_errors_follow_the_language(self):
        with self.assertRaises(ValueError) as ctx:
            setup_ui.parse_switch("maybe", "en")
        self.assertEqual(str(ctx.exception), setup_ui.t("en", "err_switch"))
        with self.assertRaises(ValueError) as ctx:
            setup_ui.parse_percent("101", "en")
        self.assertEqual(str(ctx.exception), setup_ui.t("en", "err_percent"))
        with self.assertRaises(ValueError) as ctx:
            setup_ui.parse_time("7:5", "en")
        self.assertEqual(str(ctx.exception), setup_ui.t("en", "err_time"))
        # the English spellings still parse
        self.assertTrue(setup_ui.parse_switch("on", "en"))
        self.assertFalse(setup_ui.parse_switch("off", "en"))


if __name__ == "__main__":
    unittest.main()
