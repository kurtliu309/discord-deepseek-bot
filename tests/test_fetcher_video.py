"""fetcher.py: POST /video (public subtitles, moved here from tools/server.py), the
optional max_chars on POST /url, and POST /read (video or page, decided by yt-dlp).

Offline: yt-dlp and the HTTP download are replaced by fakes; the endpoint tests talk to an
in-process ThreadingHTTPServer on 127.0.0.1, so nothing leaves the machine.
"""

import http.client
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import fetcher


SRT = ("1\n00:00:00,000 --> 00:00:02,000\nhello\n\n"
       "2\n00:00:03,000 --> 00:00:05,000\nworld\n")


def _srt_ts(sec):
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{int(round((sec - int(sec)) * 1000)):03d}"


def long_srt(n=150):
    """A transcript long enough that a 1000-char cap actually bites."""
    return "\n".join(f"{i + 1}\n{_srt_ts(i)} --> {_srt_ts(i + 0.8)}\n"
                     f"segment{i:03d} of the transcript" for i in range(n)) + "\n"


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: records options, returns `info`, serves `data`."""

    info = None
    data = b""
    opts_seen = []
    urls_seen = []

    @classmethod
    def reset(cls):
        cls.info = None
        cls.data = b""
        cls.opts_seen = []
        cls.urls_seen = []

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.opts_seen.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.urls_seen.append(("extract_info", url))
        if isinstance(FakeYDL.info, Exception):
            raise FakeYDL.info
        return FakeYDL.info

    def urlopen(self, url):
        FakeYDL.urls_seen.append(("urlopen", url))
        return FakeResponse(FakeYDL.data)


class FakeYtdlpMixin(unittest.TestCase):
    def setUp(self):
        FakeYDL.reset()
        self._real_ydl = fetcher.yt_dlp.YoutubeDL
        fetcher.yt_dlp.YoutubeDL = FakeYDL
        self._real_download = fetcher.download
        self._real_env = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy")}

    def tearDown(self):
        fetcher.yt_dlp.YoutubeDL = self._real_ydl
        fetcher.download = self._real_download
        for k, v in self._real_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------- #
# Pure subtitle helpers (moved from tools/tests/test_server.py, guild bits dropped)
# --------------------------------------------------------------------------- #
class SubtitleChoiceTests(unittest.TestCase):
    def test_language_order_within_manual(self):
        # Among tracks of the same kind, zh-Hans is still preferred over en.
        subs = {"en": [{"ext": "vtt", "url": "u"}], "zh-Hans": [{"ext": "json3", "url": "v"}]}
        kind, lang, entry = fetcher.pick_subtitle(subs, {})
        self.assertEqual((kind, lang, entry["ext"]), ("manual", "zh-Hans", "json3"))

    def test_manual_wins_at_same_language(self):
        subs = {"en": [{"ext": "vtt", "url": "u"}]}
        auto = {"en": [{"ext": "json3", "url": "v"}]}
        kind, lang, _ = fetcher.pick_subtitle(subs, auto)
        self.assertEqual((kind, lang), ("manual", "en"))

    def test_manual_english_beats_auto_chinese(self):
        # An English video: real English subtitles must win over YouTube's auto track.
        subs = {"en": [{"ext": "json3", "url": "https://yt/timedtext?lang=en"}]}
        auto = {"zh-Hans": [{"ext": "json3", "url": "https://yt/timedtext?lang=en&tlang=zh-Hans"}]}
        self.assertEqual(fetcher.pick_subtitle(subs, auto)[:2], ("manual", "en"))

    def test_original_auto_beats_machine_translation(self):
        auto = {
            "zh-Hans": [{"ext": "json3", "url": "https://yt/timedtext?lang=en&kind=asr&tlang=zh-Hans"}],
            "en": [{"ext": "json3", "url": "https://yt/timedtext?lang=en&kind=asr"}],
        }
        self.assertEqual(fetcher.pick_subtitle({}, auto)[:2], ("auto", "en"))

    def test_translation_is_the_last_resort(self):
        auto = {"zh-Hans": [{"ext": "json3", "url": "https://yt/timedtext?lang=ja&tlang=zh-Hans"}]}
        self.assertEqual(fetcher.pick_subtitle({}, auto)[:2], ("auto", "zh-Hans"))

    def test_danmaku_is_not_a_subtitle(self):
        subs = {"danmaku": [{"ext": "xml", "url": "d"}]}
        auto = {"en": [{"ext": "vtt", "url": "e"}]}
        kind, lang, _ = fetcher.pick_subtitle(subs, auto)
        self.assertEqual((kind, lang), ("auto", "en"))
        self.assertIsNone(fetcher.pick_subtitle(subs, {}))

    def test_format_order(self):
        entry = fetcher._choose_entry([{"ext": "vtt", "url": "a"}, {"ext": "json3", "url": "b"}])
        self.assertEqual(entry["ext"], "json3")
        self.assertIsNone(fetcher._choose_entry([{"ext": "xml", "url": "c"}]))


class SubtitleParserTests(unittest.TestCase):
    SRT = ("1\n00:00:01,000 --> 00:00:03,500\nHello <i>world</i> &amp; you\n\n"
           "2\n00:00:04,000 --> 00:00:06,000\nSecond line\n")

    def test_parse_srt(self):
        cues = fetcher.parse_srt(self.SRT)
        self.assertEqual(cues, [(1.0, 3.5, "Hello world & you"), (4.0, 6.0, "Second line")])

    def test_parse_vtt_through_sniffing(self):
        vtt = "WEBVTT\n\n00:01.000 --> 00:02.500\nHi there\n"
        cues = fetcher.parse_subtitle(vtt, "vtt")
        self.assertEqual(cues, [(1.0, 2.5, "Hi there")])

    def test_parse_json3_events(self):
        raw = ('{"events":[{"tStartMs":0,"dDurationMs":1500,'
               '"segs":[{"utf8":"Hi "},{"utf8":"there"}]},'
               '{"tStartMs":2000,"dDurationMs":1000,"segs":[{"utf8":"\\n"}]}]}')
        self.assertEqual(fetcher.parse_subtitle(raw, "json3"), [(0.0, 1.5, "Hi there")])

    def test_parse_json3_bilibili_body(self):
        raw = '{"body":[{"from":0.5,"to":2.0,"content":"你好"}]}'
        self.assertEqual(fetcher.parse_subtitle(raw, "json"), [(0.5, 2.0, "你好")])

    def test_merge_cues(self):
        cues = [(0.0, 2.0, "a"), (1.0, 3.0, "b"), (2.0, 4.0, "c"), (60.0, 62.0, "far")]
        merged = fetcher.merge_cues(cues)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0], (0.0, "a b c"))
        self.assertEqual(merged[1], (60.0, "far"))

    def test_format_video(self):
        cues = [(0.0, 2.0, "hello"), (3.0, 5.0, "world")]
        text = fetcher.format_video("T", "https://youtu.be/x", "U", 65, "en", "manual", cues)
        self.assertIn("# T", text)
        self.assertIn("时长：01:05", text)
        self.assertIn("字幕：en（人工）", text)
        self.assertIn("[00:00] hello world", text)

    def test_timestamp_helpers(self):
        self.assertEqual(fetcher.fmt_ts(3661), "61:01")
        self.assertEqual(fetcher.fmt_duration(65), "01:05")
        self.assertEqual(fetcher.fmt_duration(3725), "1:02:05")


class VideoUrlTests(unittest.TestCase):
    def test_https_kept_and_http_upgraded(self):
        self.assertEqual(fetcher.check_video_url("https://youtu.be/x"), "https://youtu.be/x")
        self.assertEqual(fetcher.check_video_url("http://youtu.be/x"), "https://youtu.be/x")
        self.assertEqual(fetcher.check_video_url("  youtu.be/x  "), "https://youtu.be/x")

    def test_other_schemes_rejected(self):
        for url in ("", "   ", "file:///etc/passwd", "ftp://example.com/x"):
            with self.assertRaises(ValueError, msg=repr(url)):
                fetcher.check_video_url(url)

    def test_private_hosts_rejected(self):
        for url in ("https://127.0.0.1/x", "https://localhost:8080/",
                    "https://10.1.2.3/", "https://192.168.1.1/",
                    "https://172.20.0.5/", "https://169.254.169.254/latest/meta-data/",
                    "https://foo.local/", "http://[::1]/x"):
            with self.assertRaises(ValueError, msg=url):
                fetcher.check_video_url(url)

    def test_video_opts_carry_the_egress_proxy(self):
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://egress:3128"}):
            opts = fetcher.video_opts()
        self.assertEqual(opts["proxy"], "http://egress:3128")
        self.assertTrue(opts["skip_download"])
        self.assertTrue(opts["noplaylist"])
        self.assertNotIn("cookiefile", opts)
        self.assertNotIn("format", opts)


# --------------------------------------------------------------------------- #
# fetch_video with a fake yt-dlp
# --------------------------------------------------------------------------- #
class FetchVideoTests(FakeYtdlpMixin):
    def info(self, **kw):
        info = {
            "title": "T",
            "webpage_url": "https://www.youtube.com/watch?v=abc",
            "uploader": "U",
            "duration": 65,
            "subtitles": {"en": [{"ext": "srt", "data": SRT}]},
            "automatic_captions": {},
        }
        info.update(kw)
        return info

    def test_subtitles_become_timestamped_text(self):
        FakeYDL.info = self.info()
        out = fetcher.fetch_video("https://youtu.be/abc")
        self.assertTrue(out["ok"])
        self.assertEqual(set(out), {"ok", "title", "text", "truncated"})
        self.assertEqual(out["title"], "T")
        self.assertIn("# T", out["text"])
        self.assertIn("字幕：en（人工）", out["text"])
        self.assertIn("[00:00] hello world", out["text"])
        self.assertFalse(out["truncated"])

    def test_auto_caption_track_is_used_when_no_manual_one(self):
        FakeYDL.info = self.info(subtitles={}, automatic_captions={
            "zh-Hans": [{"ext": "json3", "data": '{"events":[{"tStartMs":0,'
                                                 '"dDurationMs":2000,'
                                                 '"segs":[{"utf8":"你好"}]}]}'}]})
        out = fetcher.fetch_video("https://youtu.be/abc")
        self.assertIn("字幕：zh-Hans（自动）", out["text"])
        self.assertIn("[00:00] 你好", out["text"])

    def test_playlist_takes_the_first_entry(self):
        first = self.info(title="First")
        FakeYDL.info = {"_type": "playlist", "entries": [None, first]}
        out = fetcher.fetch_video("https://youtu.be/abc")
        self.assertEqual(out["title"], "First")

    def test_subtitle_url_is_downloaded_with_the_proxy(self):
        FakeYDL.info = self.info(subtitles={"en": [{"ext": "srt", "url": "https://subs/x"}]})
        FakeYDL.data = SRT.encode()
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://egress:3128"}):
            out = fetcher.fetch_video("https://youtu.be/abc")
        self.assertIn("[00:00] hello world", out["text"])
        self.assertEqual(FakeYDL.urls_seen[-1], ("urlopen", "https://subs/x"))
        # both the extract_info call and the subtitle download carry the proxy
        for opts in FakeYDL.opts_seen:
            self.assertEqual(opts.get("proxy"), "http://egress:3128")

    def test_max_chars_truncates_and_is_clamped(self):
        FakeYDL.info = self.info(subtitles={"en": [{"ext": "srt", "data": long_srt()}]})
        out = fetcher.fetch_video("https://youtu.be/abc", max_chars=1000)
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["text"]), 1000)
        # way below the floor: clamped up to 1000, not to something absurd
        out = fetcher.fetch_video("https://youtu.be/abc", max_chars=1)
        self.assertEqual(len(out["text"]), 1000)
        # nothing to truncate with a high cap
        out = fetcher.fetch_video("https://youtu.be/abc", max_chars=60000)
        self.assertFalse(out["truncated"])
        self.assertIn("segment149 of the transcript", out["text"])

    def test_no_subtitle_track_is_an_error(self):
        FakeYDL.info = self.info(subtitles={}, automatic_captions={})
        with self.assertRaises(ValueError) as ctx:
            fetcher.fetch_video("https://youtu.be/abc")
        self.assertIn("没有公开字幕", str(ctx.exception))

    def test_empty_subtitle_track_is_an_error(self):
        FakeYDL.info = self.info(subtitles={"en": [{"ext": "srt", "data": "not a subtitle"}]})
        with self.assertRaises(ValueError) as ctx:
            fetcher.fetch_video("https://youtu.be/abc")
        self.assertIn("没有公开字幕", str(ctx.exception))

    def test_extract_error_is_explained(self):
        FakeYDL.info = RuntimeError("Unsupported URL: https://x.test/v")
        with self.assertRaises(ValueError) as ctx:
            fetcher.fetch_video("https://x.test/v")
        self.assertIn("不支持的链接", str(ctx.exception))

    def test_private_host_is_refused_before_ytdlp_runs(self):
        with self.assertRaises(ValueError):
            fetcher.fetch_video("https://192.168.1.1/v")
        self.assertEqual(FakeYDL.urls_seen, [])
        self.assertEqual(FakeYDL.opts_seen, [])


# --------------------------------------------------------------------------- #
# /read: video or page, decided by yt-dlp's extractors (no network)
# --------------------------------------------------------------------------- #
class LooksLikeVideoTests(unittest.TestCase):
    def test_video_sites_are_recognised(self):
        for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "https://youtu.be/dQw4w9WgXcQ",
                    "https://www.bilibili.com/video/BV1xx411c7mD",
                    "https://vimeo.com/76979871"):
            self.assertTrue(fetcher.looks_like_video(url), url)

    def test_other_links_are_pages(self):
        for url in ("https://example.com/a", "https://www.bilibili.com/read/cv1"):
            self.assertFalse(fetcher.looks_like_video(url), url)

    def test_extractor_list_is_built_once_and_skips_generic(self):
        first = fetcher.video_extractors()
        self.assertIs(first, fetcher.video_extractors())
        self.assertNotIn("GenericIE", [ie.__name__ for ie in first])


class FetchReadTests(unittest.TestCase):
    URL = "https://youtu.be/dQw4w9WgXcQ"
    PAGE = {"ok": True, "title": "P", "text": "page body", "truncated": False}

    def setUp(self):
        self.video = mock.patch("fetcher.fetch_video").start()
        self.page = mock.patch("fetcher.fetch_url").start()
        self.looks = mock.patch("fetcher.looks_like_video").start()
        self.addCleanup(mock.patch.stopall)

    def test_video_link_is_read_as_subtitles(self):
        self.looks.return_value = True
        self.video.return_value = {"ok": True, "title": "V", "text": "[00:01] hi",
                                   "truncated": False}
        out = fetcher.fetch_read(self.URL, 1000)
        self.assertTrue(out["ok"])
        self.assertEqual(out["kind"], "video")
        self.assertEqual(out["title"], "V")
        self.assertEqual(out["text"], "[00:01] hi")
        self.video.assert_called_once_with(self.URL, 1000)
        self.page.assert_not_called()

    def test_unreadable_video_falls_back_to_the_page_with_a_note(self):
        self.looks.return_value = True
        self.video.side_effect = ValueError("这个视频没有公开字幕")
        self.page.return_value = dict(self.PAGE)
        out = fetcher.fetch_read(self.URL)
        self.assertTrue(out["ok"])
        self.assertEqual(out["kind"], "page")
        self.assertEqual(out["title"], "P")
        self.assertTrue(out["text"].startswith(
            "[This link looks like a video, but its subtitles could not be read: "
            "这个视频没有公开字幕."))
        self.assertIn("Below is the page text instead.", out["text"])
        self.assertTrue(out["text"].endswith("page body"))

    def test_both_fail_reports_both_reasons(self):
        self.looks.return_value = True
        self.video.side_effect = ValueError("这个视频需要登录或会员权限；只读工具不带 cookies")
        self.page.side_effect = ValueError("only http(s) urls")
        out = fetcher.fetch_read(self.URL)
        self.assertFalse(out["ok"])
        self.assertIn("视频读取失败", out["error"])
        self.assertIn("需要登录", out["error"])
        self.assertIn("网页读取也失败", out["error"])
        self.assertIn("only http(s) urls", out["error"])

    def test_plain_link_skips_ytdlp_and_is_a_page(self):
        self.looks.return_value = False
        self.page.return_value = dict(self.PAGE)
        out = fetcher.fetch_read("https://example.com/a", 5000)
        self.assertEqual(out["kind"], "page")
        self.assertEqual(out["text"], "page body")
        self.page.assert_called_once_with("https://example.com/a", 5000)
        self.video.assert_not_called()

    def test_fallback_text_is_capped(self):
        self.looks.return_value = True
        self.video.side_effect = ValueError("没有公开字幕")
        self.page.return_value = {"ok": True, "title": "P", "text": "x" * 5000,
                                  "truncated": False}
        out = fetcher.fetch_read(self.URL, 1000)
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["text"]), 1000)


# --------------------------------------------------------------------------- #
# /url max_chars (the useful bit of tools/server.py's do_read_page)
# --------------------------------------------------------------------------- #
class UrlMaxCharsTests(FakeYtdlpMixin):
    BODY = "x" * 5000

    def setUp(self):
        super().setUp()
        html = (f"<html><head><title>Page</title></head><body><p>{self.BODY}</p>"
                "</body></html>").encode()
        fetcher.download = lambda url: (html, "text/html", "https://example.com/page")

    def test_default_keeps_the_old_behaviour(self):
        out = fetcher.fetch_url("https://example.com/page")
        self.assertEqual(set(out), {"ok", "title", "text", "truncated"})
        self.assertEqual(out["title"], "Page")
        self.assertFalse(out["truncated"])
        self.assertEqual(out["text"], self.BODY)

    def test_max_chars_truncates(self):
        out = fetcher.fetch_url("https://example.com/page", 1000)
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["text"]), 1000)

    def test_cap_uses_max_text_by_default(self):
        text, truncated = fetcher.cap("y" * (fetcher.MAX_TEXT + 10))
        self.assertTrue(truncated)
        self.assertEqual(len(text), fetcher.MAX_TEXT)


# --------------------------------------------------------------------------- #
# The HTTP endpoint itself (in-process server, no network)
# --------------------------------------------------------------------------- #
class EndpointTests(FakeYtdlpMixin):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), fetcher.Handler)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=5)

    def post(self, path, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def test_video_ok(self):
        FakeYDL.info = {
            "title": "T",
            "webpage_url": "https://youtu.be/abc",
            "uploader": "U",
            "duration": 65,
            "subtitles": {"en": [{"ext": "srt", "data": SRT}]},
        }
        status, data = self.post("/video", {"url": "https://youtu.be/abc"})
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"ok", "title", "text", "truncated"})
        self.assertTrue(data["ok"])
        self.assertEqual(data["title"], "T")
        self.assertIn("[00:00] hello world", data["text"])

    def test_video_error_shape(self):
        FakeYDL.info = {"title": "T", "subtitles": {}, "automatic_captions": {}}
        status, data = self.post("/video", {"url": "https://youtu.be/abc"})
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"ok", "error"})
        self.assertFalse(data["ok"])
        self.assertIn("没有公开字幕", data["error"])

    def test_video_rejects_bad_urls_and_input(self):
        for payload in ({}, {"url": ""}, {"url": "https://10.0.0.1/x"},
                        {"url": "file:///etc/passwd"}):
            _, data = self.post("/video", payload)
            self.assertFalse(data["ok"], payload)
            self.assertTrue(data["error"], payload)
        _, data = self.post("/video", b"not json")
        self.assertFalse(data["ok"])

    def test_video_max_chars_from_request(self):
        FakeYDL.info = {"title": "T", "duration": 65,
                        "subtitles": {"en": [{"ext": "srt", "data": long_srt()}]}}
        _, data = self.post("/video", {"url": "https://youtu.be/abc", "max_chars": 1000})
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["text"]), 1000)

    def test_url_max_chars_from_request(self):
        html = ("<html><head><title>P</title></head><body>"
                + "z" * 5000 + "</body></html>").encode()
        fetcher.download = lambda url: (html, "text/html", "https://example.com/p")
        _, data = self.post("/url", {"url": "https://example.com/p", "max_chars": 1200})
        self.assertTrue(data["ok"])
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["text"]), 1200)
        _, data = self.post("/url", {"url": "https://example.com/p"})
        self.assertFalse(data["truncated"])

    # -- /read ----------------------------------------------------------------- #

    def test_read_video_returns_kind_video(self):
        FakeYDL.info = {
            "title": "T",
            "webpage_url": "https://youtu.be/dQw4w9WgXcQ",
            "uploader": "U",
            "duration": 65,
            "subtitles": {"en": [{"ext": "srt", "data": SRT}]},
        }
        status, data = self.post("/read", {"url": "https://youtu.be/dQw4w9WgXcQ"})
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"ok", "title", "text", "truncated", "kind"})
        self.assertTrue(data["ok"])
        self.assertEqual(data["kind"], "video")
        self.assertIn("[00:00] hello world", data["text"])

    def test_read_video_max_chars_from_request(self):
        FakeYDL.info = {"title": "T", "duration": 65,
                        "subtitles": {"en": [{"ext": "srt", "data": long_srt()}]}}
        _, data = self.post("/read", {"url": "https://youtu.be/dQw4w9WgXcQ", "max_chars": 1000})
        self.assertEqual(data["kind"], "video")
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["text"]), 1000)

    def test_read_unreadable_video_falls_back_to_page_text(self):
        FakeYDL.info = {"title": "T", "subtitles": {}, "automatic_captions": {}}
        html = b"<html><head><title>P</title></head><body>page body</body></html>"
        fetcher.download = lambda url: (html, "text/html", "https://youtu.be/dQw4w9WgXcQ")
        _, data = self.post("/read", {"url": "https://youtu.be/dQw4w9WgXcQ"})
        self.assertTrue(data["ok"])
        self.assertEqual(data["kind"], "page")
        self.assertTrue(data["text"].startswith(
            "[This link looks like a video, but its subtitles could not be read:"))
        self.assertIn("没有公开字幕", data["text"])
        self.assertIn("page body", data["text"])

    def test_read_plain_link_never_runs_ytdlp(self):
        html = b"<html><head><title>P</title></head><body>page body</body></html>"
        fetcher.download = lambda url: (html, "text/html", "https://example.com/a")
        _, data = self.post("/read", {"url": "https://example.com/a"})
        self.assertTrue(data["ok"])
        self.assertEqual(data["kind"], "page")
        self.assertEqual(data["title"], "P")
        self.assertEqual(data["text"], "page body")
        self.assertEqual(FakeYDL.opts_seen, [])

    def test_read_both_fail_reports_both_reasons(self):
        FakeYDL.info = {"title": "T", "subtitles": {}, "automatic_captions": {}}

        def boom(url):
            raise ValueError("page is down")

        fetcher.download = boom
        _, data = self.post("/read", {"url": "https://youtu.be/dQw4w9WgXcQ"})
        self.assertFalse(data["ok"])
        self.assertIn("没有公开字幕", data["error"])
        self.assertIn("page is down", data["error"])


if __name__ == "__main__":
    unittest.main()
