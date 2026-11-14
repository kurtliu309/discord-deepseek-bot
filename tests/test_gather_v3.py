"""v3.4: reading links/files in the replied message, and in an unrequested jump-in."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bot
import guildcfg as g
import guildmem


def cfg(**kw):
    kw.setdefault("name", "x")
    return g.GuildConfig(**kw)


class Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeFetcher:
    """Answers ``/url`` and ``/file`` like the real fetcher, and records what was asked."""

    def __init__(self, urls=None, files=None, fail=False):
        self.urls = urls or {}
        self.files = files or {}
        self.fail = fail
        self.url_calls = []
        self.file_calls = []

    async def post(self, path, json=None, params=None, content=None):
        if path == "/url":
            url = json["url"]
            self.url_calls.append(url)
            if self.fail or url not in self.urls:
                return Resp({"ok": False})
            title, text = self.urls[url]
            return Resp({"ok": True, "title": title, "text": text, "truncated": False})
        if path == "/file":
            name = params["name"]
            self.file_calls.append(name)
            if self.fail or name not in self.files:
                return Resp({"ok": False})
            return Resp({"ok": True, "text": self.files[name], "truncated": False})
        raise AssertionError(path)


class FakeAttachment:
    def __init__(self, filename="a.txt", data=b"hello", size=None, fail=False):
        self.filename = filename
        self._data = data
        self.size = len(data) if size is None else size
        self.fail = fail

    async def read(self):
        if self.fail:
            raise OSError("boom")
        return self._data


class FakeChannel:
    def __init__(self, replies=None):
        self.replies = replies or {}  # message id -> the message that reply points at
        self.fetches = []

    async def fetch_message(self, mid):
        self.fetches.append(mid)
        if mid not in self.replies:
            raise KeyError(mid)
        return self.replies[mid]


def msg(mid, content="", author_id=5, attachments=(), reference=None, channel=None):
    return SimpleNamespace(
        id=mid,
        content=content,
        author=SimpleNamespace(id=author_id, bot=False),
        attachments=list(attachments),
        reference=reference,
        channel=channel if channel is not None else FakeChannel(),
    )


def reply_ref(mid):
    return SimpleNamespace(resolved=None, message_id=mid)


class FakeBot(bot.Bot):
    """A real Bot's fetch methods without ``discord.Client.__init__``."""

    def __init__(self, fetcher=None):
        self.fetcher = fetcher or FakeFetcher()
        self.config = SimpleNamespace(owner_id=1)


class TestGatherDataV34(unittest.IsolatedAsyncioTestCase):
    async def test_interject_reads_current_links_and_failure_is_silent(self):
        fetcher = FakeFetcher(fail=True)
        fake = FakeBot(fetcher)
        message = msg(1, content="see https://example.com/x")

        data, notices = await bot.Bot.gather_data(fake, message, cfg=cfg(), quiet=True)
        self.assertEqual(fetcher.url_calls, ["https://example.com/x"])
        self.assertEqual(data, "")
        self.assertEqual(notices, [])  # a jump-in never complains

        _, notices = await bot.Bot.gather_data(fake, message, cfg=cfg())
        self.assertEqual(notices, [bot.CANT_READ_URL])  # a requested chat still does

    async def test_replied_message_links_and_files_are_read(self):
        fetcher = FakeFetcher(
            urls={"https://example.com/b": ("B page", "B body")},
            files={"b.txt": "B file"},
        )
        fake = FakeBot(fetcher)
        replied = msg(
            2,
            content="look https://example.com/b",
            attachments=[FakeAttachment("b.txt", b"B file")],
        )
        channel = FakeChannel({2: replied})
        message = msg(1, content="what do you think", reference=reply_ref(2), channel=channel)

        data, notices = await bot.Bot.gather_data(fake, message, cfg=cfg())

        self.assertEqual(fetcher.url_calls, ["https://example.com/b"])
        self.assertEqual(fetcher.file_calls, ["b.txt"])
        self.assertIn("=== web (from replied message): B page ===", data)
        self.assertIn("=== file (from replied message): b.txt ===", data)
        self.assertIn("B body", data)
        self.assertEqual(notices, [])

    async def test_current_message_comes_first(self):
        fetcher = FakeFetcher(
            urls={
                "https://example.com/now": ("Now", "now body"),
                "https://example.com/then": ("Then", "then body"),
            }
        )
        fake = FakeBot(fetcher)
        channel = FakeChannel({2: msg(2, content="https://example.com/then")})
        message = msg(1, content="https://example.com/now", reference=reply_ref(2), channel=channel)

        data, _ = await bot.Bot.gather_data(fake, message, cfg=cfg())
        self.assertEqual(fetcher.url_calls, ["https://example.com/now", "https://example.com/then"])
        self.assertLess(data.index("=== web: Now"), data.index("=== web (from replied message): Then"))

    async def test_same_url_is_fetched_once(self):
        fetcher = FakeFetcher(urls={"https://example.com/same": ("Same", "body")})
        fake = FakeBot(fetcher)
        channel = FakeChannel({2: msg(2, content="https://example.com/same")})
        message = msg(1, content="https://example.com/same", reference=reply_ref(2), channel=channel)

        await bot.Bot.gather_data(fake, message, cfg=cfg())
        self.assertEqual(fetcher.url_calls, ["https://example.com/same"])

    async def test_url_limit_is_shared_by_both_messages(self):
        fetcher = FakeFetcher(
            urls={
                "https://a.example/1": ("A1", "1"),
                "https://a.example/2": ("A2", "2"),
                "https://a.example/3": ("A3", "3"),
                "https://a.example/4": ("A4", "4"),
            }
        )
        fake = FakeBot(fetcher)
        channel = FakeChannel({2: msg(2, content="https://a.example/4")})
        message = msg(
            1,
            content="https://a.example/1 https://a.example/2 https://a.example/3",
            reference=reply_ref(2),
            channel=channel,
        )

        await bot.Bot.gather_data(fake, message, cfg=cfg())
        self.assertEqual(fetcher.url_calls, ["https://a.example/1", "https://a.example/2", "https://a.example/3"])

    async def test_blocked_replied_author_is_not_read(self):
        fetcher = FakeFetcher(
            urls={"https://example.com/b": ("B", "body")},
            files={"b.txt": "B file"},
        )
        fake = FakeBot(fetcher)
        replied = msg(
            2,
            content="https://example.com/b",
            author_id=9,
            attachments=[FakeAttachment("b.txt", b"B file")],
        )
        channel = FakeChannel({2: replied})
        message = msg(1, reference=reply_ref(2), channel=channel)

        data, notices = await bot.Bot.gather_data(
            fake, message, cfg=cfg(blocked_users=[9])
        )
        self.assertEqual(fetcher.url_calls, [])
        self.assertEqual(fetcher.file_calls, [])
        self.assertEqual(data, "")
        self.assertEqual(notices, [])

    async def test_bots_own_replied_message_is_read(self):
        fetcher = FakeFetcher(urls={"https://example.com/mine": ("Mine", "body")})
        fake = FakeBot(fetcher)
        replied = msg(2, content="https://example.com/mine", author_id=999)
        replied.author.bot = True
        channel = FakeChannel({2: replied})
        message = msg(1, reference=reply_ref(2), channel=channel)

        data, _ = await bot.Bot.gather_data(fake, message, cfg=cfg())
        self.assertEqual(fetcher.url_calls, ["https://example.com/mine"])
        self.assertIn("from replied message", data)

    async def test_unresolvable_reference_is_skipped(self):
        fetcher = FakeFetcher(urls={"https://example.com/now": ("Now", "body")})
        fake = FakeBot(fetcher)
        channel = FakeChannel({})  # fetch_message raises
        message = msg(1, content="https://example.com/now", reference=reply_ref(2), channel=channel)

        data, notices = await bot.Bot.gather_data(fake, message, cfg=cfg())
        self.assertEqual(fetcher.url_calls, ["https://example.com/now"])
        self.assertIn("=== web: Now ===", data)
        self.assertEqual(notices, [])

    async def test_missing_reference_attribute_is_skipped(self):
        fetcher = FakeFetcher(urls={"https://example.com/now": ("Now", "body")})
        fake = FakeBot(fetcher)
        message = SimpleNamespace(
            id=1,
            content="https://example.com/now",
            author=SimpleNamespace(id=5),
            attachments=[],
            channel=FakeChannel(),
        )

        data, notices = await bot.Bot.gather_data(fake, message, cfg=cfg())
        self.assertIn("=== web: Now ===", data)
        self.assertEqual(notices, [])


# --------------------------------------------------------------------------- #
# Bot.reply: when read_links is off nothing is read; jump-ins stay quiet
# --------------------------------------------------------------------------- #


class WiringBot(bot.Bot):
    def __init__(self, personas, guilds, cfg):
        self.config = SimpleNamespace(owner_id=1, tz=dt.timezone.utc)
        self.store = SimpleNamespace(guilds_dir=guilds, get=lambda gid: cfg)
        self.personas_dir = personas
        self.budgets = bot.BudgetBook(dt.timezone.utc)
        self._memories = {}
        self.gathers = []
        self.turns = []
        self.plans = []

    async def build_context(self, message):
        return [
            {
                "id": 1,
                "role": "user",
                "content": "hi",
                "author_id": "5",
                "name": "Ann",
                "message": message,
            }
        ]

    async def gather_data(self, message, **kwargs):
        self.gathers.append(kwargs)
        return "", []

    async def ask_model(self, system, turns, max_tokens):
        self.turns.append(turns)
        return "hello"

    async def _send_plan(self, target, plan):
        self.plans.append(plan)


class TestReadLinksWiring(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.personas = root / "personas"
        (self.personas / "tiredfx").mkdir(parents=True)
        (self.personas / "tiredfx" / "character.md").write_text("C\n", encoding="utf-8")
        (self.personas / "_styles").mkdir()
        (self.personas / "_styles" / "normal.md").write_text("S\n", encoding="utf-8")
        (self.personas / "_base.md").write_text("B\n", encoding="utf-8")
        self.guilds = root / "guilds"
        guildmem.GuildMemory(self.guilds / "111").ensure()

    def tearDown(self):
        self._tmp.cleanup()

    def _message(self):
        return SimpleNamespace(
            id=1,
            author=SimpleNamespace(id=5),
            content="hi",
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=222),
        )

    async def test_read_links_off_reads_neither_message_nor_reply(self):
        off = cfg(features={"read_links": {"on": False}})
        for trigger in ("mention", "interject"):
            fake = WiringBot(self.personas, self.guilds, off)
            await bot.Bot.reply(fake, self._message(), trigger, False, off, "111")
            self.assertEqual(fake.gathers, [], trigger)

    async def test_interject_reads_quietly_and_feeds_the_prompt(self):
        on = cfg()
        fake = WiringBot(self.personas, self.guilds, on)
        seen = {}

        async def gather_data(message, **kwargs):
            seen.update(kwargs)
            return "=== web (from replied message): T ===", []

        fake.gather_data = gather_data

        await bot.Bot.reply(fake, self._message(), "interject", False, on, "111")

        self.assertTrue(seen["quiet"])
        self.assertIs(seen["cfg"], on)
        prompt = fake.turns[0][0]["content"]
        self.assertIn("jump into the conversation", prompt)
        self.assertIn("=== web (from replied message): T ===", prompt)
        self.assertEqual(fake.plans, [[("reply", "hello", 0.0)]])


if __name__ == "__main__":
    unittest.main()
