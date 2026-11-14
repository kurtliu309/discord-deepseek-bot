"""v3.7 tools: schemas, fetcher-backed readers, DeepSeek search, caps and the bot gate.

No network: the fetcher and DeepSeek are ``httpx.MockTransport`` fakes.
"""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

import bot
import guildcfg as g
import guildmem
import toolloop as tl


def tool_call(name, args=None, call_id="c1", raw=None):
    arguments = raw if raw is not None else json.dumps(args or {})
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def model_message(content=None, calls=()):
    return SimpleNamespace(content=content, tool_calls=list(calls))


class FakeAsk:
    """Plays back scripted model messages and records what each call was given."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # (messages snapshot, tools)

    async def __call__(self, messages, tools):
        self.calls.append(([dict(m) for m in messages], tools))
        if self.script:
            return self.script.pop(0)
        return model_message("done")


class FakeTools:
    def __init__(self, replies=None, error=None):
        self.replies = list(replies or [])
        self.error = error
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        if self.error:
            raise self.error
        return self.replies.pop(0) if self.replies else "tool-output"


def json_client(requests, payload=None, status=200, base_url=""):
    """An AsyncClient whose transport records requests and answers with ``payload``."""

    async def handler(request):
        requests.append(request)
        return httpx.Response(status, json=payload or {})

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=base_url
    )


def make_tools(fetcher_requests, fetcher_payload, search_requests=None,
               search_payload=None, search_status=200):
    fetcher = json_client(fetcher_requests, fetcher_payload, base_url="http://fetcher")
    search = json_client(
        search_requests if search_requests is not None else [], search_payload,
        status=search_status,
    )
    return tl.Tools(fetcher=fetcher, api_key="secret-key", model="deepseek-test",
                    search_client=search)


SEARCH_PAYLOAD = {
    "content": [
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
         "input": {"query": "q"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
            {"type": "web_search_result", "title": "Alpha", "url": "https://a.example/x"},
            {"type": "web_search_result", "title": "Beta", "url": "https://b.example/y"},
            {"type": "web_search_result", "title": "Alpha again", "url": "https://a.example/x"},
        ]},
        {"type": "text", "text": "Fact one."},
        {"type": "text", "text": "Fact two."},
    ]
}


def tool_messages(messages):
    return [m for m in messages if m["role"] == "tool"]


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #


class TestSchemas(unittest.TestCase):
    def test_three_read_only_tools(self):
        names = [t["function"]["name"] for t in tl.TOOL_SCHEMAS]
        self.assertEqual(names, ["search", "read_page", "read_video"])
        self.assertEqual(tuple(names), tl.ALLOWED_TOOLS)

    def test_schema_never_mentions_bot_side_keys(self):
        blob = json.dumps(tl.TOOL_SCHEMAS)
        self.assertNotIn("guild_id", blob)
        self.assertNotIn("allow_domains", blob)
        self.assertNotIn("max_results", blob)

    def test_schemas_for_without_search_has_only_the_readers(self):
        names = [t["function"]["name"] for t in tl.schemas_for(False)]
        self.assertEqual(names, ["read_page", "read_video"])
        self.assertNotIn("search", names)

    def test_schemas_for_with_search_has_all_three(self):
        names = [t["function"]["name"] for t in tl.schemas_for(True)]
        self.assertEqual(names, ["search", "read_page", "read_video"])


# --------------------------------------------------------------------------- #
# the tools themselves
# --------------------------------------------------------------------------- #


class TestSearch(unittest.IsolatedAsyncioTestCase):
    async def test_search_hits_the_anthropic_endpoint_with_server_search(self):
        sent = []
        tools = make_tools([], {}, search_requests=sent, search_payload=SEARCH_PAYLOAD)
        await tools.search("houdini 22")
        self.assertEqual(len(sent), 1)
        request = sent[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "https://api.deepseek.com/anthropic/v1/messages")
        self.assertEqual(request.headers["x-api-key"], "secret-key")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
        body = json.loads(request.content)
        self.assertEqual(body["model"], "deepseek-test")
        self.assertEqual(body["max_tokens"], tl.SEARCH_MAX_TOKENS)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["tools"], [{
            "type": "web_search_20250305", "name": "web_search", "max_uses": 2,
        }])
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertIn("houdini 22", body["messages"][0]["content"])

    async def test_search_returns_text_plus_deduped_sources(self):
        tools = make_tools([], {}, search_requests=[], search_payload=SEARCH_PAYLOAD)
        text = await tools.search("q")
        self.assertIn("Fact one.", text)
        self.assertIn("Fact two.", text)
        self.assertIn("Sources:", text)
        self.assertIn("Alpha — https://a.example/x", text)
        self.assertIn("Beta — https://b.example/y", text)
        self.assertEqual(text.count("https://a.example/x"), 1)  # deduped

    async def test_search_http_error_is_a_plain_error_text(self):
        tools = make_tools([], {}, search_requests=[], search_status=500)
        self.assertEqual(await tools.search("q"), "error: search failed (HTTP 500)")

    async def test_search_with_no_text_and_no_sources_is_an_error(self):
        tools = make_tools([], {}, search_requests=[], search_payload={"content": []})
        self.assertIn("error:", await tools.search("q"))


class TestReaders(unittest.IsolatedAsyncioTestCase):
    async def test_read_page_posts_to_url(self):
        sent = []
        tools = make_tools(sent, {"ok": True, "title": "T", "text": "body"})
        text = await tools.call("read_page", {"url": "https://x.example/a", "max_chars": 900})
        self.assertEqual(text, "T\n\nbody")
        self.assertEqual(sent[0].url.path, "/url")
        self.assertEqual(json.loads(sent[0].content),
                         {"url": "https://x.example/a", "max_chars": 900})

    async def test_read_video_posts_to_video(self):
        sent = []
        tools = make_tools(sent, {"ok": True, "title": "", "text": "[00:01] hi"})
        text = await tools.call("read_video", {"url": "https://v.example/v"})
        self.assertEqual(text, "[00:01] hi")  # no title: text only
        self.assertEqual(sent[0].url.path, "/video")
        self.assertEqual(json.loads(sent[0].content), {"url": "https://v.example/v"})

    async def test_fetcher_error_becomes_error_text(self):
        tools = make_tools([], {"ok": False, "error": "fetch failed: ConnectError"})
        self.assertEqual(
            await tools.call("read_page", {"url": "https://x"}),
            "error: fetch failed: ConnectError",
        )

    async def test_truncated_result_says_so_at_the_end(self):
        tools = make_tools([], {"ok": True, "title": "T", "text": "body", "truncated": True})
        text = await tools.call("read_page", {"url": "https://x"})
        self.assertTrue(text.startswith("T\n\nbody"))
        self.assertIn("truncated", text.split("body", 1)[1])

    async def test_extra_arguments_are_dropped_before_the_request(self):
        sent = []
        tools = make_tools(sent, {"ok": True, "title": "T", "text": "b"})
        await tools.call("read_page", {
            "url": "https://x", "max_chars": 100,
            "guild_id": "evil", "allow_domains": ["evil.example"], "max_results": 9,
        })
        self.assertEqual(json.loads(sent[0].content), {"url": "https://x", "max_chars": 100})

    async def test_unknown_tool_name_is_never_forwarded(self):
        sent, searches = [], []
        tools = make_tools(sent, {"ok": True}, search_requests=searches)
        text = await tools.call("read_file", {"path": "/etc/passwd"})
        self.assertEqual(sent, [])
        self.assertEqual(searches, [])
        self.assertIn("no tool named", text)


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #


class TestToolLoop(unittest.IsolatedAsyncioTestCase):
    async def test_no_tool_call_returns_the_answer(self):
        ask = FakeAsk([model_message("plain answer")])
        tools = FakeTools()
        schemas = tl.schemas_for(False)
        answer = await tl.run_tool_loop(ask, [], tools, schemas, lambda: True)
        self.assertEqual(answer, "plain answer")
        self.assertEqual(tools.calls, [])
        self.assertIs(ask.calls[0][1], schemas)

    async def test_tool_output_is_wrapped_as_untrusted_data(self):
        ask = FakeAsk([model_message(calls=[tool_call("read_page", {"url": "https://x"})])])
        tools = FakeTools(replies=["page text"])
        await tl.run_tool_loop(ask, [], tools, tl.schemas_for(False), lambda: True)
        result = tool_messages(ask.calls[-1][0])[0]["content"]
        self.assertEqual(result, "=== tool: read_page ===\npage text\n=== end tool ===")
        self.assertEqual(tools.calls[0], ("read_page", {"url": "https://x"}))

    async def test_unknown_tool_name_is_refused_without_a_call(self):
        ask = FakeAsk([
            model_message(calls=[tool_call("run_shell", {"cmd": "rm -rf /"})]),
            model_message("fine"),
        ])
        tools = FakeTools()
        answer = await tl.run_tool_loop(ask, [], tools, tl.schemas_for(True), lambda: True)
        self.assertEqual(answer, "fine")
        self.assertEqual(tools.calls, [])
        self.assertIn("no tool named", tool_messages(ask.calls[-1][0])[-1]["content"])

    async def test_search_is_refused_when_the_question_has_no_search_schema(self):
        ask = FakeAsk([
            model_message(calls=[tool_call("search", {"query": "x"})]),
            model_message("fine"),
        ])
        tools = FakeTools()
        await tl.run_tool_loop(ask, [], tools, tl.schemas_for(False), lambda: True)
        self.assertEqual(tools.calls, [])
        self.assertIn("no tool named", tool_messages(ask.calls[-1][0])[-1]["content"])

    async def test_bad_json_arguments_are_not_forwarded(self):
        ask = FakeAsk([
            model_message(calls=[tool_call("search", raw="{oops")]),
            model_message("fine"),
        ])
        tools = FakeTools()
        await tl.run_tool_loop(ask, [], tools, tl.schemas_for(True), lambda: True)
        self.assertEqual(tools.calls, [])
        self.assertIn("not valid JSON", tool_messages(ask.calls[-1][0])[-1]["content"])

    async def test_charge_false_means_no_call(self):
        ask = FakeAsk([model_message(calls=[tool_call("search", {"query": "x"})])])
        tools = FakeTools()
        charged = []

        def charge():
            charged.append(1)
            return False

        answer = await tl.run_tool_loop(ask, [], tools, tl.schemas_for(True), charge)
        self.assertEqual(tools.calls, [])
        self.assertEqual(len(charged), 1)
        self.assertIn("budget is used up", tool_messages(ask.calls[-1][0])[-1]["content"])
        self.assertEqual(answer, "done")
        self.assertIsNone(ask.calls[-1][1])  # the final ask carries no tools

    async def test_fifth_call_is_refused_and_final_ask_has_no_tools(self):
        script = [
            model_message(calls=[tool_call("search", {"query": f"q{i}"}, f"c{i}")])
            for i in range(5)
        ]
        ask = FakeAsk(script)
        tools = FakeTools()
        answer = await tl.run_tool_loop(ask, [], tools, tl.schemas_for(True), lambda: True)

        self.assertEqual(len(tools.calls), 4)  # only four executions
        self.assertEqual([call[1] is None for call in ask.calls], [False] * 5 + [True])
        refused = tool_messages(ask.calls[-1][0])[-1]["content"]
        self.assertIn("all 4 tool calls", refused)
        self.assertFalse(refused.startswith("=== tool:"))  # our own note, not tool data
        self.assertEqual(answer, "done")

    async def test_total_output_char_cap_truncates_then_refuses(self):
        script = [
            model_message(calls=[tool_call("search", {"query": "a"})]),
            model_message(calls=[
                tool_call("read_page", {"url": "https://x"}, "c2"),
                tool_call("read_page", {"url": "https://y"}, "c3"),
            ]),
        ]
        ask = FakeAsk(script)
        tools = FakeTools(replies=["A" * 8, "B" * 8, "C" * 8])
        answer = await tl.run_tool_loop(
            ask, [], tools, tl.schemas_for(True), lambda: True, max_chars=10
        )

        self.assertEqual(len(tools.calls), 2)  # the third is never executed
        results = tool_messages(ask.calls[-1][0])
        self.assertIn("A" * 8, results[0]["content"])
        self.assertIn("BB\n\n[result truncated", results[1]["content"])
        self.assertIn("tool output limit", results[2]["content"])
        self.assertEqual(answer, "done")

    async def test_tool_exception_becomes_a_result_not_a_crash(self):
        ask = FakeAsk([
            model_message(calls=[tool_call("read_video", {"url": "https://v"})]),
            model_message("answered anyway"),
        ])
        tools = FakeTools(error=RuntimeError("boom"))
        answer = await tl.run_tool_loop(ask, [], tools, tl.schemas_for(False), lambda: True)
        self.assertEqual(answer, "answered anyway")
        self.assertIn("failed (RuntimeError)", tool_messages(ask.calls[-1][0])[-1]["content"])

    async def test_a_model_that_only_asks_for_unknown_tools_still_stops(self):
        # Guards against an endless loop: unknown names never reach max_calls, so the
        # round cap (max_calls + MAX_ROUNDS_PAD) has to stop the model instead.
        rounds = tl.MAX_CALLS + tl.MAX_ROUNDS_PAD
        script = [model_message(calls=[tool_call("nope")]) for _ in range(rounds)]
        script.append(model_message("done"))
        ask = FakeAsk(script)
        tools = FakeTools()
        answer = await tl.run_tool_loop(ask, [], tools, tl.schemas_for(True), lambda: True)
        self.assertEqual(tools.calls, [])
        self.assertEqual(len(ask.calls), rounds + 1)  # + the final, tool-less ask
        self.assertIsNone(ask.calls[-1][1])
        self.assertEqual(answer, "done")


# --------------------------------------------------------------------------- #
# bot side: which requests carry which tool list
# --------------------------------------------------------------------------- #


class TestBotGate(unittest.TestCase):
    def _cfg(self, tools_on):
        return g.GuildConfig.model_validate(
            {"name": "x", "features": {"tools": {"on": tools_on}}}
        )

    def test_interjection_never_gets_tools(self):
        self.assertIsNone(bot.tool_permission(self._cfg(True), "interject"))

    def test_search_off_gets_the_two_readers(self):
        names = [t["function"]["name"] for t in bot.tool_permission(self._cfg(False), "mention")]
        self.assertEqual(names, ["read_page", "read_video"])

    def test_search_on_gets_three_tools(self):
        names = [t["function"]["name"] for t in bot.tool_permission(self._cfg(True), "mention")]
        self.assertEqual(names, ["search", "read_page", "read_video"])

    def test_unconfigured_guild_still_gets_the_readers(self):
        names = [t["function"]["name"] for t in bot.tool_permission(None, "mention")]
        self.assertEqual(names, ["read_page", "read_video"])

    def test_tools_addendum_mentions_search_only_when_it_is_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            personas = Path(tmp) / "personas"
            (personas / "tiredfx").mkdir(parents=True)
            (personas / "tiredfx" / "character.md").write_text("C\n", encoding="utf-8")
            (personas / "_styles").mkdir()
            (personas / "_styles" / "normal.md").write_text("S\n", encoding="utf-8")
            (personas / "_base.md").write_text("B\n", encoding="utf-8")

            cfg = self._cfg(False)
            plain = bot.build_system(personas, cfg)
            readers = bot.build_system(personas, cfg, include_tools=True)
            with_search = bot.build_system(
                personas, cfg, include_tools=True, include_search=True
            )
        self.assertNotIn("call the provided tools", plain)
        self.assertIn("call the provided tools", readers)
        self.assertNotIn("search tool", readers)
        self.assertIn("search tool", with_search)


class FakeModelBot(bot.Bot):
    """A real Bot's model methods without ``discord.Client.__init__``."""

    def __init__(self, seen):
        async def create(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=model_message("hi", []))]
            )

        self.config = SimpleNamespace(model="deepseek-test")
        self.deepseek = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )


class TestReplyWiring(unittest.IsolatedAsyncioTestCase):
    """``Bot.reply`` itself: interjections never use the loop, real replies always do."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ts = root / "personas"
        (self.ts / "tiredfx").mkdir(parents=True)
        (self.ts / "tiredfx" / "character.md").write_text("C\n", encoding="utf-8")
        (self.ts / "_styles").mkdir()
        (self.ts / "_styles" / "normal.md").write_text("S\n", encoding="utf-8")
        (self.ts / "_base.md").write_text("B\n", encoding="utf-8")
        self.guilds = root / "guilds"
        guildmem.GuildMemory(self.guilds / "111").ensure()

    def tearDown(self):
        self._tmp.cleanup()

    def _bot(self, cfg):
        personas, guilds = self.ts, self.guilds

        class FakeBot(bot.Bot):
            def __init__(inner):
                inner.config = SimpleNamespace(owner_id=1, tz=dt.timezone.utc)
                inner.store = SimpleNamespace(guilds_dir=guilds, get=lambda gid: cfg)
                inner.personas_dir = personas
                inner.budgets = bot.BudgetBook(dt.timezone.utc)
                inner._memories = {}
                inner.tools = FakeTools()
                inner.asks, inner.system, inner.plan = [], "", []

            async def build_context(inner, message):
                return [{"id": 1, "role": "user", "content": "hi", "author_id": "5",
                         "name": "Ann", "message": message}]

            async def gather_data(inner, message, **kwargs):
                return "", []

            async def ask_model(inner, system, turns, max_tokens):
                inner.asks.append(None)  # no tool list
                inner.system = system
                return "hello"

            async def ask_model_message(inner, system, msgs, max_tokens, tools=None):
                inner.asks.append(tools)
                inner.system = system
                return model_message("hello")

            async def _send_plan(inner, target, plan):
                inner.plan.append(plan)

        return FakeBot()

    def _message(self):
        return SimpleNamespace(
            author=SimpleNamespace(id=5), content="hi",
            guild=SimpleNamespace(id=111), channel=SimpleNamespace(id=222),
        )

    async def test_search_on_reply_carries_all_three_tools(self):
        cfg = g.GuildConfig.model_validate(
            {"name": "x", "features": {"tools": {"on": True}}}
        )
        fake = self._bot(cfg)
        await bot.Bot.reply(fake, self._message(), "mention", False, cfg, "111")
        self.assertEqual(
            [t["function"]["name"] for t in fake.asks[0]],
            ["search", "read_page", "read_video"],
        )
        self.assertIn("call the provided tools", fake.system)
        self.assertIn("search tool", fake.system)
        self.assertEqual(fake.plan, [[("reply", "hello", 0.0)]])

    async def test_search_off_reply_still_has_the_two_readers(self):
        cfg = g.GuildConfig.model_validate(
            {"name": "x", "features": {"tools": {"on": False}}}
        )
        fake = self._bot(cfg)
        await bot.Bot.reply(fake, self._message(), "mention", False, cfg, "111")
        self.assertEqual(
            [t["function"]["name"] for t in fake.asks[0]],
            ["read_page", "read_video"],
        )
        self.assertIn("call the provided tools", fake.system)
        self.assertNotIn("search tool", fake.system)

    async def test_interjection_has_no_tool_list(self):
        cfg = g.GuildConfig.model_validate(
            {"name": "x", "features": {"tools": {"on": True}}}
        )
        fake = self._bot(cfg)
        await bot.Bot.reply(fake, self._message(), "interject", False, cfg, "111")
        self.assertEqual(fake.asks, [None])  # exactly one plain model call, no tools
        self.assertNotIn("call the provided tools", fake.system)


class TestAskModel(unittest.IsolatedAsyncioTestCase):
    def _fake_bot(self, seen):
        return FakeModelBot(seen)

    async def test_plain_ask_has_no_tools_field_at_all(self):
        seen = {}
        bot_obj = self._fake_bot(seen)
        text = await bot.Bot.ask_model(bot_obj, "sys", [{"role": "user", "content": "u"}], 5)
        self.assertEqual(text, "hi")
        self.assertNotIn("tools", seen)

    async def test_tool_ask_passes_the_schemas_and_returns_the_message(self):
        seen = {}
        bot_obj = self._fake_bot(seen)
        message = await bot.Bot.ask_model_message(
            bot_obj, "sys", [{"role": "user", "content": "u"}], max_tokens=5,
            tools=tl.schemas_for(True),
        )
        self.assertEqual(message.content, "hi")
        self.assertEqual(seen["tools"], tl.schemas_for(True))

    async def test_empty_tool_list_is_still_no_tools_field(self):
        seen = {}
        bot_obj = self._fake_bot(seen)
        await bot.Bot.ask_model_message(
            bot_obj, "sys", [{"role": "user", "content": "u"}], max_tokens=5, tools=None
        )
        self.assertNotIn("tools", seen)
