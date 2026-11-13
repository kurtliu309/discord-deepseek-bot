import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import guildcfg
import migrate_v3

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "group-profile-v2.md"
GUILD_ID = "1317847113559965736"
GUILD_NAME = "頭腦警察"

ENV = {
    "BLOCKED_CHANNEL_IDS": "111, 222",
    "POST_CHANNEL_ID": "999",
    "SUMMARY_TIME": "22:30",
    "TOPICS_TIME": "09:15",
    "TOPICS_COUNT": "4",
    "REPLY_BUDGET": "42",
    "BUDGET_RESET_TIME": "08:00",
    "RANDOM_CHANCE": "0.07",
    "FX_TOPIC_CHANCE": "0.09",
    "BURST_COUNT": "9",
    "BURST_WINDOW_SECONDS": "120",
    "COOLDOWN_SECONDS": "300",
}


class MigrationCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name) / "data"
        self.data.mkdir()
        shutil.copy(FIXTURE, self.data / "group-profile.md")
        (self.data / "recaps").mkdir()
        (self.data / "recaps" / "2026-11-12.md").write_text("[daily recap]\nold\n", encoding="utf-8")
        (self.data / "recaps" / "2026-11-13.md").write_text("[daily recap]\nnew\n", encoding="utf-8")
        self.guild_dir = self.data / "guilds" / GUILD_ID

    def tearDown(self):
        self._tmp.cleanup()

    def migrate(self, **kw):
        kw.setdefault("environ", ENV)
        return migrate_v3.run(self.data, GUILD_ID, GUILD_NAME, **kw)

    def config(self):
        return guildcfg.load_config_file(self.guild_dir / "config.yaml")


class TestSplitProfile(unittest.TestCase):
    def test_all_sections(self):
        blocks = migrate_v3.split_profile(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(set(blocks), {"kurt", "threads", "people", "jokes", "unfinished", "drift"})
        self.assertIn("Be nice to each other", blocks["kurt"])
        self.assertIn("Kurt: write the group's fixed direction", blocks["kurt"])
        self.assertNotIn("Main threads", blocks["kurt"])
        self.assertIn("Tech-finance", blocks["threads"])
        self.assertIn("Hallam", blocks["people"])
        self.assertIn("Chinese spy", blocks["jokes"])
        self.assertIn("bot wars", blocks["unfinished"])
        self.assertIn("Baseline", blocks["drift"])

    def test_heading_free(self):
        blocks = migrate_v3.split_profile(FIXTURE.read_text(encoding="utf-8"))
        for body in blocks.values():
            self.assertFalse(body.lstrip().startswith("#"), body[:40])
            self.assertNotIn("Bot notes", body)


class TestMigration(MigrationCase):
    def test_config_fields(self):
        result = self.migrate()
        self.assertTrue(result["config_written"])
        cfg = self.config()
        self.assertEqual(cfg.name, GUILD_NAME)
        self.assertEqual(cfg.trust, guildcfg.Trust.OWNER)
        self.assertTrue(cfg.consent)
        self.assertEqual(cfg.persona, "tiredfx")
        self.assertEqual(cfg.style, "normal")
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.budget.replies_per_day, 42)
        self.assertEqual(cfg.budget.lookups_per_day, 60)
        self.assertEqual(cfg.budget.reset_time, "08:00")
        self.assertIsNone(cfg.allowed_channels)
        self.assertEqual(cfg.quiet_channels, [111, 222])
        self.assertEqual(cfg.features.interject.chance, 0.07)
        self.assertEqual(cfg.features.interject.fx_chance, 0.09)
        self.assertEqual(cfg.features.interject.burst_count, 9)
        self.assertEqual(cfg.features.interject.burst_window, 120)
        self.assertEqual(cfg.features.interject.cooldown, 300)
        self.assertTrue(cfg.features.recap.on)
        self.assertEqual(cfg.features.recap.channel, 999)
        self.assertEqual(cfg.features.recap.time, "22:30")
        self.assertTrue(cfg.features.topics.on)
        self.assertEqual(cfg.features.topics.channel, 999)
        self.assertEqual(cfg.features.topics.time, "09:15")
        self.assertEqual(cfg.features.topics.count, 4)
        self.assertFalse(cfg.features.tools.on)
        self.assertFalse(cfg.features.news.on)
        self.assertTrue(guildcfg.effective(cfg, "chat"))
        self.assertTrue(guildcfg.effective(cfg, "recap"))

    def test_profile_blocks_written(self):
        self.migrate()
        profile = self.guild_dir / "profile"
        for name in ("kurt", "threads", "people", "jokes", "unfinished", "drift"):
            self.assertTrue((profile / f"{name}.md").exists(), name)
        self.assertIn("Be nice", (profile / "kurt.md").read_text(encoding="utf-8"))
        self.assertIn("Hallam", (profile / "people.md").read_text(encoding="utf-8"))

    def test_v2_originals_backed_up(self):
        original = (self.data / "group-profile.md").read_text(encoding="utf-8")
        self.migrate()
        self.assertFalse((self.data / "group-profile.md").exists())
        backup = self.data / "group-profile.md.v2.bak"
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), original)
        self.assertFalse((self.data / "recaps").exists())
        self.assertTrue((self.data / "recaps.v2.bak").is_dir())

    def test_recaps_moved(self):
        result = self.migrate()
        self.assertEqual(result["recaps"], ["2026-11-12", "2026-11-13"])
        recaps = self.guild_dir / "recaps"
        self.assertEqual(
            sorted(p.stem for p in recaps.glob("*.md")), ["2026-11-12", "2026-11-13"]
        )
        self.assertIn("new", (recaps / "2026-11-13.md").read_text(encoding="utf-8"))

    def test_dm_config_created(self):
        self.migrate()
        cfg = guildcfg.load_config_file(self.data / "guilds" / "dm-owner" / "config.yaml")
        self.assertEqual(cfg.trust, guildcfg.Trust.OWNER)
        self.assertTrue(guildcfg.effective(cfg, "chat"))

    def test_idempotent(self):
        self.migrate()
        first = (self.guild_dir / "config.yaml").read_text(encoding="utf-8")
        blocks = {p.name: p.read_text(encoding="utf-8") for p in (self.guild_dir / "profile").glob("*.md")}
        second = self.migrate()
        self.assertFalse(second["config_written"])
        self.assertEqual((self.guild_dir / "config.yaml").read_text(encoding="utf-8"), first)
        self.assertEqual(
            {p.name: p.read_text(encoding="utf-8") for p in (self.guild_dir / "profile").glob("*.md")},
            blocks,
        )
        self.assertTrue((self.data / "group-profile.md.v2.bak").exists())

    def test_only_channel(self):
        self.migrate(only_channel=1570775686862471301)
        cfg = self.config()
        self.assertEqual(cfg.allowed_channels, [1570775686862471301])
        self.assertEqual(cfg.features.recap.channel, 1570775686862471301)
        self.assertEqual(cfg.features.topics.channel, 1570775686862471301)

    def test_missing_post_channel_is_an_error(self):
        env = dict(ENV)
        env.pop("POST_CHANNEL_ID")
        with self.assertRaises(ValueError):
            self.migrate(environ=env)

    def test_guild_id_must_be_numeric(self):
        with self.assertRaises(ValueError):
            migrate_v3.run(self.data, "../escape", GUILD_NAME, environ=ENV)


class TestCli(MigrationCase):
    def test_main_with_fake_env(self):
        with mock.patch.dict(os.environ, ENV, clear=True), mock.patch("builtins.print"):
            rc = migrate_v3.main(
                ["--data", str(self.data), "--guild-id", GUILD_ID, "--guild-name", GUILD_NAME,
                 "--only-channel", "555"]
            )
        self.assertEqual(rc, 0)
        cfg = self.config()
        self.assertEqual(cfg.allowed_channels, [555])
        self.assertEqual(cfg.features.recap.channel, 555)


if __name__ == "__main__":
    unittest.main()
