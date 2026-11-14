"""v3.1 tool loop: schemas, injection, caps, errors and the bot-side gate. No network."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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

    async def call(self, name, args, guild_id, allow_domains):
        self.calls.append((name, dict(args), guild_id, allow_domains))
        if self.error:
            raise self.error
        return self.replies.pop(0) if self.replies else "tool-output"


class RecordingClient(tl.ToolsClient):
    """A real ToolsClient with the transport replaced: records the payload it would send."""

    def __init__(self):
        super().__init__("http://tools.invalid/mcp")
        self.sent = []

    async def _send(self, name, payload):
        self.sent.append((name, payload))
        return "ok"


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

    def test_schema_never_mentions_injected_keys(self):
        blob = json.dumps(tl.TOOL_SCHEMAS)
        self.assertNotIn("guild_id", blob)
        self.assertNotIn("allow_domains", blob)


# --------------------------------------------------------------------------- #
# argument injection
# --------------------------------------------------------------------------- #


class TestInjection(unittest.IsolatedAsyncioTestCase):
    async def test_client_drops_model_values_and_injects_the_real_ones(self):
        client = RecordingClient()
        text = await client.call(
            "search",
            {"query": "x", "guild_id": "evil", "allow_domains": ["evil.com"]},
            "123456789012345678",
            ["good.com"],
        )
        self.assertEqual(text, "ok")
        self.assertEqual(
            client.sent,
            [("search", {"query": "x", "guild_id": "123456789012345678",
                         "allow_domains": ["good.com"]})],
        )

    async def test_any_and_missing_allowlist_become_any(self):
        self.assertEqual(tl.inject_args({}, 1, "any")["allow_domains"], "any")
        self.assertEqual(tl.inject_args({}, 1, None)["allow_domains"], "any")
        self.assertEqual(tl.inject_args({}, 1, "any")["guild_id"], "1")  # always a string

    async def test_unknown_tool_name_is_never_forwarded(self):
        client = RecordingClient()
        text = await client.call("read_file", {"path": "/etc/passwd"}, "1", "any")
        self.assertEqual(client.sent, [])
        self.assertIn("no tool named", text)


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #


class TestToolLoop(unittest.IsolatedAsyncioTestCase):
    async def test_no_tool_call_returns_the_answer(self):
        ask = FakeAsk([model_message("plain answer")])
        tools = FakeTools()
        answer = await tl.run_tool_loop(ask, [], tools, lambda: True, "1", "any")
        self.assertEqual(answer, "plain answer")
        self.assertEqual(tools.calls, [])
        self.assertIs(ask.calls[0][1], tl.TOOL_SCHEMAS)

    async def test_tool_output_is_wrapped_as_untrusted_data(self):
        ask = FakeAsk([model_message(calls=[tool_call("search", {"query": "x"})])])
        tools = FakeTools(replies=["search results"])
        await tl.run_tool_loop(ask, [], tools, lambda: True, "1", "any")
        result = tool_messages(ask.calls[-1][0])[0]["content"]
        self.assertEqual(result, "=== tool: search ===\nsearch results\n=== end tool ===")
        self.assertEqual(tools.calls[0][1], {"query": "x"})
        self.assertEqual(tools.calls[0][2:], ("1", "any"))

    async def test_unknown_tool_name_is_refused_without_a_call(self):
        ask = FakeAsk([
            model_message(calls=[tool_call("run_shell", {"cmd": "rm -rf /"})]),
            model_message("fine"),
        ])
        tools = FakeTools()
        answer = await tl.run_tool_loop(ask, [], tools, lambda: True, "1", "any")
        self.assertEqual(answer, "fine")
        self.assertEqual(tools.calls, [])
        self.assertIn("no tool named", tool_messages(ask.calls[-1][0])[-1]["content"])

    async def test_bad_json_arguments_are_not_forwarded(self):
        ask = FakeAsk([
            model_message(calls=[tool_call("search", raw="{oops")]),
            model_message("fine"),
        ])
        tools = FakeTools()
        await tl.run_tool_loop(ask, [], tools, lambda: True, "1", "any")
        self.assertEqual(tools.calls, [])
        self.assertIn("not valid JSON", tool_messages(ask.calls[-1][0])[-1]["content"])

    async def test_charge_false_means_no_call(self):
        ask = FakeAsk([model_message(calls=[tool_call("search", {"query": "x"})])])
        tools = FakeTools()
        charged = []

        def charge():
            charged.append(1)
            return False

        answer = await tl.run_tool_loop(ask, [], tools, charge, "1", "any")
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
        answer = await tl.run_tool_loop(ask, [], tools, lambda: True, "1", "any")

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
            ask, [], tools, lambda: True, "1", "any", max_chars=10
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
        answer = await tl.run_tool_loop(ask, [], tools, lambda: True, "1", "any")
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
        answer = await tl.run_tool_loop(ask, [], tools, lambda: True, "1", "any")
        self.assertEqual(tools.calls, [])
        self.assertEqual(len(ask.calls), rounds + 1)  # + the final, tool-less ask
        self.assertIsNone(ask.calls[-1][1])
        self.assertEqual(answer, "done")


# --------------------------------------------------------------------------- #
# bot side: which requests carry a tool list
# --------------------------------------------------------------------------- #


class TestBotGate(unittest.TestCase):
    def _cfg(self, trust, tools_on):
        return g.GuildConfig.model_validate(
            {"name": "x", "trust": trust, "features": {"tools": {"on": tools_on}}}
        )

    def test_public_guild_never_gets_tools(self):
        # Even with the YAML saying on, public trust wins: the request has no tool list.
        self.assertIsNone(bot.tool_permission(self._cfg("public", True), "mention"))

    def test_owner_guild_with_tools_on_gets_its_allowlist(self):
        perm = bot.tool_permission(self._cfg("owner", True), "mention")
        self.assertIsNotNone(perm)
        self.assertEqual(perm.allow_domains, "any")

    def test_off_interjection_and_unconfigured_get_nothing(self):
        self.assertIsNone(bot.tool_permission(self._cfg("owner", False), "mention"))
        self.assertIsNone(bot.tool_permission(self._cfg("owner", True), "interject"))
        self.assertIsNone(bot.tool_permission(None, "mention"))

    def test_tools_addendum_only_when_requested(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            personas = Path(tmp) / "personas"
            (personas / "tiredfx").mkdir(parents=True)
            (personas / "tiredfx" / "character.md").write_text("C\n", encoding="utf-8")
            (personas / "_styles").mkdir()
            (personas / "_styles" / "normal.md").write_text("S\n", encoding="utf-8")
            (personas / "_base.md").write_text("B\n", encoding="utf-8")

            cfg = self._cfg("owner", True)
            plain = bot.build_system(personas, cfg)
            with_tools = bot.build_system(personas, cfg, include_tools=True)
        self.assertNotIn("call the provided tools", plain)
        self.assertIn("call the provided tools", with_tools)


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
    """``Bot.reply`` itself: owner guilds go through the loop, public ones never do."""

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
                inner.tools_client = FakeTools()
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

    async def test_owner_guild_with_tools_uses_the_loop(self):
        cfg = g.GuildConfig.model_validate(
            {"name": "x", "trust": "owner", "features": {"tools": {"on": True}}}
        )
        fake = self._bot(cfg)
        await bot.Bot.reply(fake, self._message(), "mention", False, cfg, "111")
        self.assertIs(fake.asks[0], tl.TOOL_SCHEMAS)  # first model call carried tools
        self.assertIn("call the provided tools", fake.system)
        self.assertEqual(fake.plan, [[("reply", "hello", 0.0)]])

    async def test_public_guild_ask_has_no_tool_list(self):
        cfg = g.GuildConfig.model_validate(
            {"name": "x", "trust": "public", "features": {"tools": {"on": True}}}
        )
        fake = self._bot(cfg)
        await bot.Bot.reply(fake, self._message(), "mention", False, cfg, "111")
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
            tools=tl.TOOL_SCHEMAS,
        )
        self.assertEqual(message.content, "hi")
        self.assertEqual(seen["tools"], tl.TOOL_SCHEMAS)

    async def test_empty_tool_list_is_still_no_tools_field(self):
        seen = {}
        bot_obj = self._fake_bot(seen)
        await bot.Bot.ask_model_message(
            bot_obj, "sys", [{"role": "user", "content": "u"}], max_tokens=5, tools=None
        )
        self.assertNotIn("tools", seen)
