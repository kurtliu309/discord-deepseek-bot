"""Offline unit tests for tools/server.py — no network, no Docker services.

Run inside the image (see tools/README.md):
    docker build -t ddb-tools:test tools
    docker run --rm ddb-tools:test python -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


class AllowlistTests(unittest.TestCase):
    def test_any_means_no_filter(self):
        for value in (None, "any", "ANY", "*", ["any"]):
            self.assertIsNone(server.normalize_allow_domains(value), value)

    def test_string_and_list_forms(self):
        self.assertEqual(server.normalize_allow_domains("example.com, foo.org"),
                         ["example.com", "foo.org"])
        self.assertEqual(server.normalize_allow_domains(["Example.COM", "foo.org"]),
                         ["example.com", "foo.org"])
        self.assertEqual(server.normalize_allow_domains("*.example.com"), ["example.com"])
        self.assertEqual(server.normalize_allow_domains(".example.com"), ["example.com"])

    def test_empty_or_bad_input(self):
        self.assertEqual(server.normalize_allow_domains(""), [])  # deny everything, not "any"
        with self.assertRaises(server.ToolInputError):
            server.normalize_allow_domains("not a domain!")
        with self.assertRaises(server.ToolInputError):
            server.normalize_allow_domains(42)

    def test_host_matching(self):
        allow = ["example.com"]
        self.assertTrue(server.host_allowed("example.com", allow))
        self.assertTrue(server.host_allowed("www.example.com", allow))
        self.assertTrue(server.host_allowed("a.b.example.com", allow))
        self.assertFalse(server.host_allowed("notexample.com", allow))
        self.assertFalse(server.host_allowed("example.com.evil.net", allow))
        self.assertFalse(server.host_allowed("", allow))
        self.assertTrue(server.host_allowed("anything.test", None))
        self.assertFalse(server.host_allowed("anything.test", []))

    def test_github_raw_alias(self):
        # github.com/<u>/<r>/blob/... is rewritten to raw.githubusercontent.com.
        self.assertTrue(server.host_allowed("raw.githubusercontent.com", ["github.com"]))
        self.assertFalse(server.host_allowed("raw.githubusercontent.com", ["example.com"]))


class UrlTests(unittest.TestCase):
    def test_https_upgrade(self):
        self.assertEqual(server.prepare_url("http://example.com/a"),
                         "https://example.com/a")
        self.assertEqual(server.prepare_url("  example.com/a  "), "https://example.com/a")
        self.assertEqual(server.prepare_url("https://example.com"), "https://example.com")

    def test_other_schemes_rejected(self):
        with self.assertRaises(server.ToolInputError):
            server.prepare_url("file:///etc/passwd")
        with self.assertRaises(server.ToolInputError):
            server.prepare_url("")

    def test_github_blob_rewrite(self):
        self.assertEqual(
            server.prepare_url("https://github.com/Thysrael/Horizon/blob/main/README.md"),
            "https://raw.githubusercontent.com/Thysrael/Horizon/main/README.md")

    def test_private_hosts_rejected(self):
        for url in ("https://127.0.0.1/x", "https://localhost:8080/",
                    "https://10.1.2.3/", "https://192.168.1.1/",
                    "https://172.20.0.5/", "https://169.254.169.254/latest/meta-data/",
                    "https://foo.local/"):
            with self.assertRaises(server.ToolInputError, msg=url):
                server.check_url(url, None)

    def test_allowlist_enforced(self):
        server.check_url("https://docs.example.com/x", ["example.com"])
        with self.assertRaises(server.ToolInputError) as ctx:
            server.check_url("https://evil.net/x", ["example.com"])
        self.assertIn("evil.net", str(ctx.exception))
        self.assertIn("白名单", str(ctx.exception))

    def test_filter_search_results(self):
        items = [
            {"title": "A", "url": "https://example.com/a", "content": "s1"},
            {"title": "B", "url": "https://evil.net/b", "content": "s2"},
            {"title": "C", "url": "https://sub.example.com/c", "content": "s3"},
            {"title": "D", "url": "https://example.com/d", "content": "s4"},
        ]
        out = server.filter_search_results(items, ["example.com"], 8)
        self.assertEqual([r["title"] for r in out], ["A", "C", "D"])
        self.assertEqual(out[0], {"title": "A", "url": "https://example.com/a", "snippet": "s1"})
        self.assertEqual(len(server.filter_search_results(items, None, 2)), 2)
        self.assertEqual(server.filter_search_results(items, ["nothing.test"], 8), [])


class ClipTests(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(server.clip("hello", 100), "hello\n")

    def test_truncated_and_labelled(self):
        out = server.clip("x" * 500, 100)
        self.assertTrue(out.startswith("x" * 100))
        self.assertIn("截断到 100 字", out)
        self.assertEqual(len(out.split("\n")[0]), 100)


class GuildIdTests(unittest.TestCase):
    def test_guild_id_required(self):
        server.check_guild_id("1570775686862471301")
        server.check_guild_id("dm-owner")
        for bad in (None, "", "../etc", "a" * 65, 123):
            with self.assertRaises(server.ToolInputError, msg=repr(bad)):
                server.check_guild_id(bad)

    def test_clamp_int(self):
        self.assertEqual(server.clamp_int(5, 1, 20, 8), 5)
        self.assertEqual(server.clamp_int(99, 1, 20, 8), 20)
        self.assertEqual(server.clamp_int(0, 1, 20, 8), 1)
        self.assertEqual(server.clamp_int("x", 1, 20, 8), 8)


class SubtitleChoiceTests(unittest.TestCase):
    def test_language_order_manual_and_auto_together(self):
        # zh-Hans is preferred over en, even when it is only the automatic track.
        subs = {"en": [{"ext": "vtt", "url": "u"}]}
        auto = {"zh-Hans": [{"ext": "json3", "url": "v"}]}
        kind, lang, entry = server.pick_subtitle(subs, auto)
        self.assertEqual((kind, lang, entry["ext"]), ("auto", "zh-Hans", "json3"))

    def test_manual_wins_at_same_language(self):
        subs = {"en": [{"ext": "vtt", "url": "u"}]}
        auto = {"en": [{"ext": "json3", "url": "v"}]}
        kind, lang, _ = server.pick_subtitle(subs, auto)
        self.assertEqual((kind, lang), ("manual", "en"))

    def test_danmaku_is_not_a_subtitle(self):
        subs = {"danmaku": [{"ext": "xml", "url": "d"}]}
        auto = {"en": [{"ext": "vtt", "url": "e"}]}
        kind, lang, _ = server.pick_subtitle(subs, auto)
        self.assertEqual((kind, lang), ("auto", "en"))
        self.assertIsNone(server.pick_subtitle(subs, {}))

    def test_format_order(self):
        entry = server._choose_entry([{"ext": "vtt", "url": "a"}, {"ext": "json3", "url": "b"}])
        self.assertEqual(entry["ext"], "json3")
        self.assertIsNone(server._choose_entry([{"ext": "xml", "url": "c"}]))


class SubtitleParserTests(unittest.TestCase):
    SRT = ("1\n00:00:01,000 --> 00:00:03,500\nHello <i>world</i> &amp; you\n\n"
           "2\n00:00:04,000 --> 00:00:06,000\nSecond line\n")

    def test_parse_srt(self):
        cues = server.parse_srt(self.SRT)
        self.assertEqual(cues, [(1.0, 3.5, "Hello world & you"), (4.0, 6.0, "Second line")])

    def test_parse_vtt_through_sniffing(self):
        vtt = "WEBVTT\n\n00:01.000 --> 00:02.500\nHi there\n"
        cues = server.parse_subtitle(vtt, "vtt")
        self.assertEqual(cues, [(1.0, 2.5, "Hi there")])

    def test_parse_json3_events(self):
        raw = ('{"events":[{"tStartMs":0,"dDurationMs":1500,'
               '"segs":[{"utf8":"Hi "},{"utf8":"there"}]},'
               '{"tStartMs":2000,"dDurationMs":1000,"segs":[{"utf8":"\\n"}]}]}')
        self.assertEqual(server.parse_subtitle(raw, "json3"), [(0.0, 1.5, "Hi there")])

    def test_parse_json3_bilibili_body(self):
        raw = '{"body":[{"from":0.5,"to":2.0,"content":"你好"}]}'
        self.assertEqual(server.parse_subtitle(raw, "json"), [(0.5, 2.0, "你好")])

    def test_merge_cues(self):
        cues = [(0.0, 2.0, "a"), (1.0, 3.0, "b"), (2.0, 4.0, "c"), (60.0, 62.0, "far")]
        merged = server.merge_cues(cues)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0], (0.0, "a b c"))
        self.assertEqual(merged[1], (60.0, "far"))

    def test_format_video(self):
        cues = [(0.0, 2.0, "hello"), (3.0, 5.0, "world")]
        text = server.format_video("T", "https://youtu.be/x", "U", 65, "en", "manual", cues)
        self.assertIn("# T", text)
        self.assertIn("时长：01:05", text)
        self.assertIn("字幕：en（人工）", text)
        self.assertIn("[00:00] hello world", text)


class SecretCheckTests(unittest.TestCase):
    def test_no_secrets_passes(self):
        server.assert_no_secrets({})
        server.assert_no_secrets({"HTTPS_PROXY": "http://egress:3128"})

    def test_discord_token_refused(self):
        with self.assertRaises(SystemExit):
            server.assert_no_secrets({"DISCORD_BOT_TOKEN": "x"})

    def test_deepseek_key_refused(self):
        with self.assertRaises(SystemExit):
            server.assert_no_secrets({"DEEPSEEK_API_KEY": "x"})


class HtmlTests(unittest.TestCase):
    def test_html_to_text_skips_scripts(self):
        title, text = server.html_to_text(
            "<html><head><title>T</title><style>x{}</style></head>"
            "<body><p>Hello</p><script>bad()</script><div>World</div></body></html>")
        self.assertEqual(title, "T")
        self.assertNotIn("bad()", text)
        self.assertNotIn("x{}", text)
        self.assertEqual(text, "Hello\nWorld")  # block tags become line breaks (fetcher.py)


if __name__ == "__main__":
    unittest.main()
