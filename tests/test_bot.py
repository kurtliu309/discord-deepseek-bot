import unittest

import bot


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


class TestFxTopicConfig(unittest.TestCase):
    def _config(self, extra=None):
        env = {"DISCORD_BOT_TOKEN": "t", "DEEPSEEK_API_KEY": "k", "OWNER_USER_ID": "1"}
        env.update(extra or {})
        return bot.load_config(env)

    def test_default(self):
        self.assertEqual(self._config().fx_topic_chance, 0.05)

    def test_override(self):
        self.assertEqual(self._config({"FX_TOPIC_CHANCE": "0.1"}).fx_topic_chance, 0.1)


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


class TestSanitizeOutput(unittest.TestCase):
    def test_strips_everyone_and_here(self):
        out = bot.sanitize_output("hey @everyone and @here and @EVERYONE")
        self.assertNotIn("@everyone", out)
        self.assertNotIn("@here", out)


class TestRulesCard(unittest.TestCase):
    def test_no_ids(self):
        cfg = bot.Config("t", "k", 123456789012345678, blocked_channel_names=["general"])
        card = bot.build_rules_card(cfg, remaining=5, blocked_names=["general"])
        self.assertNotRegex(card, r"\d{15,}")
        self.assertIn("general", card)
        self.assertIn("5/50", card)


if __name__ == "__main__":
    unittest.main()
