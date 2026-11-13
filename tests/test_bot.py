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

    def test_bad_filename(self):
        chat, name, body = bot.extract_file_block(
            "<<<FILE ../../evil.md>>>\nx\n<<<END FILE>>>"
        )
        self.assertIsNone(name)
        self.assertIsNone(body)

        chat, name, body = bot.extract_file_block("<<<FILE notes.txt>>>\nx\n<<<END FILE>>>")
        self.assertIsNone(name)
        self.assertIsNone(body)


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
