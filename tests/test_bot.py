import tempfile
import unittest
from pathlib import Path

import bot
import guildcfg
import persona


class FakeRNG:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


class TestBudget(unittest.TestCase):
    def test_charge(self):
        b = bot.Budget(3)
        self.assertTrue(b.spend())
        self.assertTrue(b.spend())
        self.assertTrue(b.spend())
        self.assertEqual(b.remaining, 0)
        self.assertTrue(b.empty)
        self.assertFalse(b.spend())
        self.assertEqual(b.remaining, 0)

    def test_refund_never_exceeds_limit(self):
        b = bot.Budget(3)
        b.refund()
        self.assertEqual(b.remaining, 3)

    def test_notice_once(self):
        b = bot.Budget(2)
        b.spend()
        b.spend()
        self.assertTrue(b.notify_empty())
        self.assertFalse(b.notify_empty())

    def test_refill(self):
        b = bot.Budget(2)
        b.spend()
        b.spend()
        self.assertTrue(b.notify_empty())
        b.refill()
        self.assertEqual(b.remaining, 2)
        b.spend()
        b.spend()
        self.assertTrue(b.notify_empty())

    def test_exempt(self):
        owner = 111111111111111111
        other = 222222222222222222
        self.assertFalse(bot.should_charge("mention", owner, owner))
        self.assertTrue(bot.should_charge("mention", other, owner))
        self.assertFalse(bot.should_charge("dm", owner, owner))
        self.assertTrue(bot.should_charge("interject", owner, owner))


class TestChannelActivity(unittest.TestCase):
    def test_burst_cooldown_reset(self):
        a = bot.ChannelActivity(
            burst_count=3, burst_window_seconds=100, cooldown_seconds=10, rng=FakeRNG(1.0)
        )
        self.assertFalse(a.should_interject("c", 0.0, 0))
        self.assertFalse(a.should_interject("c", 0.0, 10))
        self.assertFalse(a.should_interject("c", 0.0, 20))
        self.assertTrue(a.should_interject("c", 0.0, 30))  # burst: 4 msgs in window

        self.assertFalse(a.should_interject("c", 0.0, 35))  # cooldown until 40

        # burst window was reset; it must rebuild before bursting again
        self.assertFalse(a.should_interject("c", 0.0, 41))
        self.assertFalse(a.should_interject("c", 0.0, 42))
        self.assertTrue(a.should_interject("c", 0.0, 43))

    def test_random_chance_injected_rng(self):
        a = bot.ChannelActivity(
            burst_count=100, burst_window_seconds=1000, cooldown_seconds=10, rng=FakeRNG(0.5)
        )
        self.assertTrue(a.should_interject("c", 0.9, 1))   # 0.5 < 0.9
        self.assertFalse(a.should_interject("c", 0.9, 2))  # now on cooldown

        b = bot.ChannelActivity(
            burst_count=100, burst_window_seconds=1000, cooldown_seconds=10, rng=FakeRNG(0.95)
        )
        self.assertFalse(b.should_interject("c", 0.9, 1))  # 0.95 < 0.9 is False


class TestParseReplyTo(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(bot.parse_reply_to("REPLY_TO: 3\nhey"), 3)
        self.assertEqual(bot.parse_reply_to("reply_to: 0\nhey"), 0)

    def test_invalid(self):
        self.assertIsNone(bot.parse_reply_to("hello there"))
        self.assertIsNone(bot.parse_reply_to("REPLY_TO: x\nhey"))
        self.assertIsNone(bot.parse_reply_to(""))

    def test_skip(self):
        self.assertIs(bot.parse_reply_to("SKIP"), bot.SKIP)
        self.assertIs(bot.parse_reply_to("skip\nnevermind"), bot.SKIP)


class TestExtractFileBlock(unittest.TestCase):
    def test_present(self):
        content = "here you go\n<<<FILE notes.md>>>\n# Hello\nworld\n<<<END FILE>>>\nbye"
        chat, name, body = bot.extract_file_block(content)
        self.assertEqual(name, "notes.md")
        self.assertIn("# Hello", body)
        self.assertIn("here you go", chat)
        self.assertIn("bye", chat)
        self.assertNotIn("<<<FILE", chat)

    def test_absent(self):
        chat, name, body = bot.extract_file_block("just some text")
        self.assertIsNone(name)
        self.assertIsNone(body)
        self.assertEqual(chat, "just some text")

    def test_bad_filename_is_sanitised(self):
        chat, name, body = bot.extract_file_block("<<<FILE ../../evil.md>>>\nx\n<<<END FILE>>>")
        self.assertEqual(name, "evil.md")
        self.assertNotIn("/", name)
        self.assertEqual(body, "x")

        chat, name, body = bot.extract_file_block("<<<FILE notes.txt>>>\nx\n<<<END FILE>>>")
        self.assertEqual(name, "notes.txt")  # txt is an allowed output type

    def test_name_with_spaces_and_quotes(self):
        chat, name, body = bot.extract_file_block('ok\n<<<FILE "My Cool Notes.md">>>\n# A\n<<<END FILE>>>')
        self.assertEqual(name, "My-Cool-Notes.md")
        self.assertEqual(chat, "ok")

    def test_missing_name(self):
        chat, name, body = bot.extract_file_block("<<<FILE>>>\n# A\n<<<END FILE>>>")
        self.assertEqual(name, "doc.md")

    def test_missing_end_marker(self):
        chat, name, body = bot.extract_file_block("here\n<<<FILE guide.md>>>\n# Guide\nline two")
        self.assertEqual(name, "guide.md")
        self.assertEqual(body, "# Guide\nline two")
        self.assertEqual(chat, "here")

    def test_code_fence_inside_block(self):
        chat, name, body = bot.extract_file_block(
            "<<<FILE a.md>>>\n```markdown\n# Title\ntext\n```\n<<<END FILE>>>"
        )
        self.assertEqual(body, "# Title\ntext")

    def test_empty_body_is_not_a_doc(self):
        chat, name, body = bot.extract_file_block("<<<FILE a.md>>>\n<<<END FILE>>>")
        self.assertIsNone(name)


class TestDocRequest(unittest.TestCase):
    def test_detects_english_and_chinese(self):
        for s in ["write me an md doc about vex", "make a markdown file", "can you write up notes",
                  "give me a md", "give us md", "give me HTML", "make a web page of this",
                  "把这个写成文档", "整理成笔记", "给我一个 md 文件", "做成网页"]:
            self.assertTrue(bot.looks_like_doc_request(s), s)

    def test_requested_ext(self):
        self.assertEqual(bot.requested_ext("give me HTML"), "html")
        self.assertEqual(bot.requested_ext("做成网页"), "html")
        self.assertEqual(bot.requested_ext("as a txt please"), "txt")
        self.assertEqual(bot.requested_ext("give me a md"), "md")

    def test_html_block(self):
        chat, name, body = bot.extract_file_block("<<<FILE page.html>>>\n<h1>x</h1>\n<<<END FILE>>>", "html")
        self.assertEqual(name, "page.html")
        chat, name, body = bot.extract_file_block("<<<FILE page>>>\n<h1>x</h1>\n<<<END FILE>>>", "html")
        self.assertEqual(name, "page.html")
        chat, name, body = bot.extract_file_block("<<<FILE evil.exe>>>\nx\n<<<END FILE>>>")
        self.assertTrue(name.endswith(".md"))

    def test_plain_chat_is_not_a_doc(self):
        for s in ["how's your day", "lol this is cooked", "what do you think of houdini 21"]:
            self.assertFalse(bot.looks_like_doc_request(s), s)


class TestFxTopic(unittest.TestCase):
    def test_fx_topics(self):
        for s in ["anyone use houdini?", "my VEX is broken", "pyro sim keeps exploding",
                  "这个特效怎么做", "flip sim looks off", "the SOP solver is cooked",
                  "vellum sim again", "胡迪尼又崩了"]:
            self.assertTrue(bot.is_fx_topic(s), s)

    def test_not_fx_topics(self):
        for s in ["I flipped my car", "effective plan", "fxxk this", "soprano singing",
                  "dope track", "sim city", "", None]:
            self.assertFalse(bot.is_fx_topic(s), s)


class TestGlobalConfig(unittest.TestCase):
    """v3: process-wide settings only; per-guild knobs moved into config.yaml."""

    def _config(self, extra=None):
        env = {"DISCORD_BOT_TOKEN": "t", "DEEPSEEK_API_KEY": "k", "OWNER_USER_ID": "1"}
        env.update(extra or {})
        return bot.load_config(env)

    def test_defaults(self):
        cfg = self._config()
        self.assertEqual(cfg.model, "deepseek-flash")
        self.assertEqual(cfg.data_dir, "/data")
        self.assertEqual(Path(cfg.guilds_dir), Path(cfg.data_dir) / "guilds")
        self.assertEqual(Path(cfg.personas_dir), Path(cfg.data_dir) / "shared" / "personas")
        self.assertEqual(cfg.context_messages, 12)

    def test_override(self):
        cfg = self._config(
            {"DATA_DIR": "/srv/data", "CONTEXT_MESSAGES": "20", "DEEPSEEK_MODEL": "m"}
        )
        self.assertEqual(cfg.data_dir, "/srv/data")
        self.assertEqual(Path(cfg.guilds_dir), Path(cfg.data_dir) / "guilds")
        self.assertEqual(cfg.context_messages, 20)
        self.assertEqual(cfg.model, "m")


class TestDevCharge(unittest.TestCase):
    def test_owner_free_others_pay(self):
        self.assertFalse(bot.should_charge_dev(1, 1))
        self.assertTrue(bot.should_charge_dev(2, 1))


class TestRuleSystem(unittest.TestCase):
    def test_includes_reference_and_card(self):
        s = bot.build_rule_system("CARD-TEXT", "REFERENCE-TEXT")
        self.assertIn("CARD-TEXT", s)
        self.assertIn("REFERENCE-TEXT", s)

    def test_about_file_exists_and_has_no_ids(self):
        about = bot.load_about()
        self.assertGreater(len(about), 2000)
        self.assertNotRegex(about, r"\d{17,20}")
        self.assertIn("What it can output", about)
        self.assertIn("Changelog", about)


class TestSplitMessage(unittest.TestCase):
    def test_short(self):
        self.assertEqual(bot.split_message("short"), ["short"])

    def test_empty(self):
        self.assertEqual(bot.split_message(""), [])

    def test_long_no_newlines(self):
        text = "line " * 500  # 2500 chars
        parts = bot.split_message(text)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(len(p) <= 2000 for p in parts))


class TestFindUrls(unittest.TestCase):
    def test_max_three(self):
        text = "a https://a.com b https://b.com c https://c.com d https://d.com e https://e.com"
        self.assertEqual(
            bot.find_urls(text), ["https://a.com", "https://b.com", "https://c.com"]
        )

    def test_no_urls(self):
        self.assertEqual(bot.find_urls("nothing here"), [])

    def test_keeps_balanced_brackets(self):
        wiki = "https://en.wikipedia.org/wiki/Houdini_(software)"
        self.assertEqual(bot.find_urls(f"read {wiki}"), [wiki])
        self.assertEqual(bot.find_urls(f"read {wiki}."), [wiki])
        self.assertEqual(bot.find_urls(f"read {wiki}, ok?"), [wiki])

    def test_drops_unbalanced_closing_bracket(self):
        self.assertEqual(bot.find_urls("(see https://a.com/x)"), ["https://a.com/x"])
        self.assertEqual(bot.find_urls("(see https://en.wikipedia.org/wiki/A_(b))"),
                         ["https://en.wikipedia.org/wiki/A_(b)"])
        self.assertEqual(bot.find_urls("[https://a.com/y]."), ["https://a.com/y"])


class TestSanitizeOutput(unittest.TestCase):
    def test_strips_everyone_and_here(self):
        out = bot.sanitize_output("hey @everyone and @here and @EVERYONE")
        self.assertNotIn("@everyone", out)
        self.assertNotIn("@here", out)


class TestRulesCard(unittest.TestCase):
    def test_no_ids(self):
        # v3: the card is built from the guild config (Assertions unchanged from v2).
        cfg = guildcfg.GuildConfig(name="g")
        card = bot.build_rules_card(cfg, remaining=5, blocked_names=["general"])
        self.assertNotRegex(card, r"\d{15,}")
        self.assertIn("general", card)
        self.assertIn("5/50", card)


class TestInterjectChance(unittest.TestCase):
    def test_no_persona_numbers_means_no_fx_bonus(self):
        legacy = persona.PersonaTraits()  # no persona.yaml -> fx_multiplier is None
        self.assertAlmostEqual(bot.interject_chance(0.03, legacy), 0.03)
        self.assertAlmostEqual(bot.interject_chance(0.03, legacy, True), 0.03)
        self.assertAlmostEqual(bot.interject_chance(0.10, legacy, True), 0.10)
        self.assertAlmostEqual(bot.interject_chance(0.03, None), 0.03)
        self.assertAlmostEqual(bot.interject_chance(0.03, None, True), 0.03)

    def test_fx_multiplier_multiplies_the_base(self):
        traits = persona.PersonaTraits(fx_multiplier=1.7)
        self.assertAlmostEqual(bot.interject_chance(0.03, traits), 0.03)
        self.assertAlmostEqual(bot.interject_chance(0.03, traits, True), 0.051)
        self.assertAlmostEqual(bot.interject_chance(0.10, traits, True), 0.17)

    def test_chance_scale(self):
        traits = persona.PersonaTraits(chance_scale=0.6, fx_multiplier=1.3)
        self.assertAlmostEqual(bot.interject_chance(0.03, traits), 0.018)
        self.assertAlmostEqual(bot.interject_chance(0.03, traits, True), 0.0234)

    def test_chance_scale_works_without_fx_multiplier(self):
        # A persona that only sets chance_scale still scales, and FX talk gets
        # no extra bonus on top of it.
        traits = persona.PersonaTraits(chance_scale=0.5)
        self.assertIsNone(traits.fx_multiplier)
        self.assertAlmostEqual(bot.interject_chance(0.10, traits), 0.05)
        self.assertAlmostEqual(bot.interject_chance(0.10, traits, True), 0.05)
        self.assertAlmostEqual(bot.interject_chance(0.02, traits, True), 0.01)

    def test_hard_cap_no_persona_can_raise(self):
        traits = persona.PersonaTraits(fx_multiplier=1.7)
        self.assertEqual(bot.INTERJECT_HARD_CAP, 1.0)
        self.assertEqual(bot.interject_chance(0.9, traits, True), 1.0)  # 0.9 x 1.7
        self.assertEqual(
            bot.interject_chance(0.9, persona.PersonaTraits(chance_scale=3.0)),
            bot.INTERJECT_HARD_CAP,
        )
        self.assertEqual(
            bot.interject_chance(
                1.0, persona.PersonaTraits(chance_scale=3.0, fx_multiplier=3.0), True
            ),
            bot.INTERJECT_HARD_CAP,
        )


class TestInterjectCooldown(unittest.TestCase):
    def _ic(self, cooldown):
        return guildcfg.InterjectSettings(cooldown=cooldown)

    def test_scale_multiplies_the_config(self):
        self.assertEqual(bot.interject_cooldown(self._ic(600), persona.PersonaTraits()), 600)
        self.assertEqual(
            bot.interject_cooldown(self._ic(600), persona.PersonaTraits(cooldown_scale=1.5)),
            900,
        )
        self.assertEqual(
            bot.interject_cooldown(self._ic(600), persona.PersonaTraits(cooldown_scale=0.2)),
            120,
        )

    def test_capped_at_one_hour(self):
        self.assertEqual(bot.MAX_INTERJECT_COOLDOWN, 3600)
        self.assertEqual(
            bot.interject_cooldown(self._ic(7200), persona.PersonaTraits(cooldown_scale=3.0)),
            bot.MAX_INTERJECT_COOLDOWN,
        )


class TestInterjectBurstCount(unittest.TestCase):
    def _ic(self, burst_count=12):
        return guildcfg.InterjectSettings(burst_count=burst_count)

    def test_default_is_no_scaling(self):
        self.assertEqual(bot.interject_burst_count(self._ic(), persona.PersonaTraits()), 12)
        self.assertEqual(bot.interject_burst_count(self._ic(), None), 12)

    def test_scale_multiplies_the_config(self):
        self.assertEqual(
            bot.interject_burst_count(self._ic(), persona.PersonaTraits(burst_scale=1.2)), 14
        )
        self.assertEqual(
            bot.interject_burst_count(self._ic(10), persona.PersonaTraits(burst_scale=0.5)), 5
        )

    def test_never_below_one(self):
        self.assertEqual(
            bot.interject_burst_count(self._ic(1), persona.PersonaTraits(burst_scale=0.5)), 1
        )


class TestRulesCardFollowsPersona(unittest.TestCase):
    def test_numbers_match_the_trigger_maths(self):
        cfg = guildcfg.GuildConfig()  # chance 0.03, cooldown 600
        traits = persona.PersonaTraits(
            chance_scale=2.0, fx_multiplier=1.7, cooldown_scale=1.5
        )
        card = bot.build_rules_card(cfg, traits=traits)
        chance = bot.interject_chance(cfg.features.interject.chance, traits, False)
        on_fx = bot.interject_chance(cfg.features.interject.chance, traits, True)
        cooldown = bot.interject_cooldown(cfg.features.interject, traits)
        self.assertIn(f"~{chance:.0%} per message", card)
        self.assertIn(f"~{on_fx:.0%}", card)
        self.assertIn(f"cool off for {cooldown // 60} min", card)
        self.assertIn("~6% per message", card)
        self.assertIn("~10%", card)
        self.assertIn("15 min", card)

    def test_burst_number_matches_the_trigger_maths(self):
        cfg = guildcfg.GuildConfig()  # burst_count 12, window 180s
        traits = persona.PersonaTraits(burst_scale=1.2)
        card = bot.build_rules_card(cfg, traits=traits)
        burst = bot.interject_burst_count(cfg.features.interject, traits)
        self.assertEqual(burst, 14)
        self.assertIn(f"than {burst} messages in 3 min", card)

    def test_no_fx_multiplier_shows_one_number(self):
        card = bot.build_rules_card(guildcfg.GuildConfig())
        self.assertIn("~3% per message", card)
        self.assertNotIn("Houdini/FX", card)
        self.assertNotIn("~5%", card)
        self.assertIn("10 min", card)

    def test_chance_scale_only_still_shows_one_number(self):
        traits = persona.PersonaTraits(chance_scale=0.6)
        card = bot.build_rules_card(guildcfg.GuildConfig(), traits=traits)
        self.assertIn("~2% per message", card)
        self.assertNotIn("Houdini/FX", card)

    def test_card_reads_the_persona_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "turtle"
            folder.mkdir()
            (folder / "persona.yaml").write_text(
                "interject:\n  chance_scale: 0.6\n  fx_multiplier: 1.3\n"
                "  cooldown_scale: 1.5\n",
                encoding="utf-8",
            )
            traits = persona.load_traits(tmp, "turtle")
        try:
            card = bot.build_rules_card(guildcfg.GuildConfig(persona="turtle"), traits=traits)
        finally:
            persona.clear_cache()
        self.assertIn("~2% per message", card)
        self.assertIn("15 min", card)


class TestChatPlanTypingSpeed(unittest.TestCase):
    def test_speed_reaches_the_typing_pause(self):
        body = "first\n[[next]]\n" + "x" * 40
        plan = bot.chat_plan(body, chatty=True, typing_speed=0.5)
        self.assertEqual(plan[0], ("reply", "first", 0.0))
        self.assertEqual(plan[1][2], persona.typing_delay("x" * 40, speed=0.5))

    def test_default_speed_is_unchanged(self):
        plan = bot.chat_plan("first\n[[next]]\nsecond", chatty=True)
        self.assertEqual(plan[1][2], persona.typing_delay("second"))


if __name__ == "__main__":
    unittest.main()
