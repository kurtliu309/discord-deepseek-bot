import os
import tempfile
import unittest
from pathlib import Path

import guildcfg as g


BASE = {"name": "測試群"}


def make(**overrides):
    data = dict(BASE)
    data.update(overrides)
    return g.GuildConfig.model_validate(data)


class TestDefaults(unittest.TestCase):
    def test_defaults(self):
        cfg = make()
        self.assertEqual(cfg.trust, g.Trust.PUBLIC)
        self.assertFalse(cfg.consent)
        self.assertEqual(cfg.persona, "tiredfx")
        self.assertEqual(cfg.style, "normal")
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.extra_prompt, "")
        self.assertEqual(cfg.budget.replies_per_day, 50)
        self.assertEqual(cfg.budget.lookups_per_day, 0)
        self.assertEqual(cfg.budget.reset_time, "09:00")
        self.assertIsNone(cfg.allowed_channels)
        self.assertEqual(cfg.quiet_channels, [])
        self.assertEqual(cfg.recap_skip_channels, [])
        self.assertTrue(cfg.features.chat.on)
        self.assertTrue(cfg.features.interject.on)
        self.assertEqual(cfg.features.interject.chance, 0.03)
        self.assertEqual(cfg.features.interject.fx_chance, 0.05)
        self.assertEqual(cfg.features.interject.burst_count, 12)
        self.assertEqual(cfg.features.interject.burst_window, 180)
        self.assertEqual(cfg.features.interject.cooldown, 600)
        self.assertTrue(cfg.features.read_links.on)
        self.assertFalse(cfg.features.tools.on)
        self.assertFalse(cfg.features.news.on)
        self.assertFalse(cfg.features.recap.on)
        self.assertEqual(cfg.features.recap.time, "22:00")
        self.assertFalse(cfg.features.topics.on)
        self.assertEqual(cfg.features.topics.time, "09:00")
        self.assertEqual(cfg.features.topics.count, 3)
        self.assertTrue(cfg.features.topics.feeds)


class TestValidation(unittest.TestCase):
    def test_shorthand_and_on_off(self):
        cfg = g.GuildConfig.model_validate(
            {
                "name": "x",
                "features": {
                    "chat": "on",
                    "interject": {"on": "off", "chance": 0.5},
                    "read_links": False,
                },
            }
        )
        self.assertTrue(cfg.features.chat.on)
        self.assertFalse(cfg.features.interject.on)
        self.assertEqual(cfg.features.interject.chance, 0.5)
        self.assertFalse(cfg.features.read_links.on)

    def test_unknown_key_rejected(self):
        with self.assertRaises(g.ConfigError):
            g.parse_config({"name": "x", "typo_field": 1})
        with self.assertRaises(g.ConfigError):
            g.parse_config({"name": "x", "features": {"chat": {"pobability": 1}}})

    def test_bad_values_rejected(self):
        for bad in (
            {"trust": "emperor"},
            {"budget": {"reset_time": "9:99"}},
            {"features": {"recap": {"time": "25:00"}}},
            {"features": {"interject": {"chance": 1.5}}},
            {"persona": "../evil"},
        ):
            with self.assertRaises(g.ConfigError, msg=str(bad)):
                g.parse_config(dict(BASE, **bad))

    def test_id_like_strings_become_ints(self):
        cfg = g.GuildConfig.model_validate(
            {"name": "x", "allowed_channels": ["123"], "quiet_channels": ["456"]}
        )
        self.assertEqual(cfg.allowed_channels, [123])

    def test_top_level_is_mapping(self):
        with self.assertRaises(g.ConfigError):
            g.parse_config(["not", "a", "mapping"])

    def test_unquoted_time_is_a_string(self):
        cfg = g.parse_config(
            g.yaml.load("name: x\nfeatures:\n  recap: {time: 22:00}\nbudget: {reset_time: 09:00}\n",
                        Loader=g._Loader)
        )
        self.assertEqual(cfg.features.recap.time, "22:00")
        self.assertEqual(cfg.budget.reset_time, "09:00")

    def test_yaml_on_key_is_not_a_boolean(self):
        text = """
name: x
features:
  interject: {on: true, chance: 0.2}
  tools: {on: off}
"""
        cfg = g.parse_config(g.yaml.load(text, Loader=g._Loader))
        self.assertTrue(cfg.features.interject.on)
        self.assertEqual(cfg.features.interject.chance, 0.2)
        self.assertFalse(cfg.features.tools.on)


class TestTrust(unittest.TestCase):
    def test_ordered(self):
        self.assertLess(g.Trust.PUBLIC, g.Trust.OWNER)
        self.assertGreater(g.Trust.OWNER, g.Trust.PUBLIC)
        self.assertGreaterEqual(g.Trust.OWNER, "public")
        self.assertLessEqual(g.Trust("public"), "owner")
        self.assertEqual(str(g.Trust.OWNER), "owner")

    def test_design_example_parses(self):
        text = """
name: 頭腦警察
trust: owner
consent: true
persona: turtle
features:
  chat: on
  interject: { on: true, chance: 0.03 }
  read_links: { on: true, allow_domains: any }
  tools: { on: true, allow_domains: any }
  news: { on: true, channel: 1570775686862471301, time: "09:00", items: 5 }
  recap: { on: true, channel: 1570775686862471301, time: "22:00" }
  topics: off
quiet_channels: []
"""
        cfg = g.parse_config(g.yaml.load(text, Loader=g._Loader))
        self.assertEqual(cfg.trust, g.Trust.OWNER)
        self.assertEqual(cfg.features.news.channel, 1570775686862471301)
        self.assertFalse(cfg.features.topics.on)


class TestEffective(unittest.TestCase):
    def test_public_cannot_turn_tools_on(self):
        cfg = make(trust="public", features={"tools": {"on": True}})
        self.assertTrue(cfg.features.tools.on)
        self.assertFalse(g.effective(cfg, "tools"))
        self.assertTrue(g.effective(cfg, "chat"))

    def test_owner_gets_tools(self):
        cfg = make(trust="owner", features={"tools": {"on": True}})
        self.assertTrue(g.effective(cfg, "tools"))

    def test_off_stays_off(self):
        cfg = make(trust="owner", features={"chat": "off"})
        self.assertFalse(g.effective(cfg, "chat"))

    def test_unconfigured_guild(self):
        for name in g.FEATURE_NAMES:
            self.assertFalse(g.effective(None, name))

    def test_registry(self):
        self.assertEqual(g.FEATURES["tools"].min_trust, g.Trust.OWNER)
        self.assertEqual(g.FEATURES["chat"].min_trust, g.Trust.PUBLIC)
        self.assertEqual(g.FEATURES["chat"].profile_parts, ("kurt", "people", "jokes"))
        self.assertEqual(g.FEATURES["chat"].token_budget, 500)
        self.assertEqual(g.FEATURES["interject"].profile_parts, ("kurt", "jokes"))
        self.assertEqual(g.FEATURES["topics"].profile_parts, ("kurt", "threads", "unfinished"))
        self.assertEqual(g.FEATURES["recap"].token_budget, 1500)
        self.assertEqual(g.FEATURES["recap"].recent_recaps, 3)
        self.assertIn("drift", g.FEATURES["recap"].profile_parts)
        self.assertEqual(set(g.FEATURES), set(g.FEATURE_NAMES))


class TestChannels(unittest.TestCase):
    def test_allowed(self):
        cfg = make(allowed_channels=[1, 2])
        self.assertTrue(g.channel_allowed(cfg, 1))
        self.assertTrue(g.channel_allowed(cfg, "2"))
        self.assertFalse(g.channel_allowed(cfg, 3))
        self.assertTrue(g.channel_allowed(make(), 3))  # null = every channel
        self.assertFalse(g.channel_allowed(None, 3))

    def test_quiet(self):
        cfg = make(quiet_channels=[9])
        self.assertTrue(g.channel_quiet(cfg, 9))
        self.assertFalse(g.channel_quiet(cfg, 8))
        self.assertFalse(g.channel_quiet(None, 9))


class TestDm(unittest.TestCase):
    def test_dm_config(self):
        cfg = g.dm_config()
        self.assertEqual(g.DM_GUILD_ID, "dm-owner")
        self.assertEqual(cfg.trust, g.Trust.OWNER)
        self.assertTrue(g.effective(cfg, "chat"))
        self.assertFalse(g.effective(cfg, "interject"))


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.logs = []
        self.store = g.GuildConfigStore(self.root, interval=0, logger=self.logs.append)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, guild_id, text):
        path = self.root / guild_id / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_scan_and_save(self):
        self.store.save("111", make(allowed_channels=[5]))
        self.store.save("222", make(name="two", trust="owner"))
        self.store.reload()
        self.assertEqual(self.store.get(111).name, "測試群")
        self.assertEqual(self.store.get("222").trust, g.Trust.OWNER)
        self.assertEqual(self.store.get(111).allowed_channels, [5])
        self.assertEqual(set(self.store.configs), {"111", "222"})
        self.assertIsNone(self.store.get("333"))
        # no temp files left behind
        self.assertEqual(list((self.root / "111").glob(".*.tmp")), [])

    def test_invalid_config_is_unconfigured_and_logged(self):
        self._write("111", "name: x\ntrust: emperor\n")
        self.store.maybe_reload()
        self.assertIsNone(self.store.get("111"))
        self.assertIn("111", self.store.invalid)
        self.assertTrue(any("111" in line for line in self.logs))

    def test_hot_reload_on_mtime(self):
        self.store.save("111", make(name="one"))
        self.assertEqual(self.store.get("111").name, "one")
        path = self.root / "111" / "config.yaml"
        path.write_text(g.dump_config(make(name="changed", trust="owner")), encoding="utf-8")
        old = path.stat().st_mtime_ns
        os.utime(path, ns=(old + 10 ** 9, old + 10 ** 9))
        self.assertTrue(self.store.maybe_reload())
        self.assertEqual(self.store.get("111").name, "changed")
        self.assertEqual(self.store.get("111").trust, g.Trust.OWNER)

    def test_interval_throttles_disk_reads(self):
        self.store.save("111", make(name="one"))
        now = [100.0]
        store = g.GuildConfigStore(self.root, interval=30, clock=lambda: now[0], logger=self.logs.append)
        self.assertEqual(store.get("111").name, "one")
        self._write("222", g.dump_config(make(name="two")))
        self.assertFalse(store.maybe_reload())  # inside the 30s window
        self.assertIsNone(store.get("222"))
        now[0] += 31
        self.assertTrue(store.maybe_reload())
        self.assertIsNotNone(store.get("222"))

    def test_removed_guild_drops_out(self):
        self.store.save("111", make())
        self.assertTrue(self.store.configured("111"))
        (self.root / "111" / "config.yaml").unlink()
        self.store.maybe_reload()
        self.assertIsNone(self.store.get("111"))


class TestRoundTrip(unittest.TestCase):
    def test_dump_load(self):
        cfg = make(
            trust="owner",
            consent=True,
            allowed_channels=[1, 2],
            quiet_channels=[3],
            features={"tools": {"on": True}, "recap": {"on": True, "channel": 4}},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g" / "config.yaml"
            g.write_config(path, cfg)
            back = g.load_config_file(path)
        self.assertEqual(back.model_dump(), cfg.model_dump())
        self.assertTrue(g.effective(back, "tools"))
        self.assertTrue(g.effective(back, "recap"))


if __name__ == "__main__":
    unittest.main()
