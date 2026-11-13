"""Model tool loop for owner guilds: schemas, the MCP client, and the capped loop.

Design: docs/notes/design-v3.md §10. The three read-only tools live in the keyless
``tools`` container behind MCP streamable HTTP. The bot decides *whether* the model may
use tools at all (public guilds never even send a tool list), charges one lookup before
every call and enforces the per-question caps below.

``guild_id`` and ``allow_domains`` are never left to the model: this module drops any
values it supplies and injects the real ones. Everything a tool returns is external
content, so it reaches the model wrapped in the same untrusted-data markers as the
bot's file / web data (``=== tool: <name> === ... === end tool ===``).

No Discord or OpenAI imports here: the loop is plain async logic and is unit-tested
with a fake ``ask`` and a fake tools client.
"""

import json

MAX_CALLS = 4  # tool executions per question
MAX_CHARS = 40000  # tool output characters per question, all calls together
MAX_ROUNDS_PAD = 3  # extra model rounds on top of MAX_CALLS, so a confused model cannot loop

ALLOWED_TOOLS = ("search", "read_page", "read_video")
INJECTED_KEYS = ("guild_id", "allow_domains")

# OpenAI function-calling schemas. Intentionally no guild_id / allow_domains: the model
# must not see them at all (the bot fills them in on every call).
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Search the web. Returns a JSON list of {title, url, snippet}, already "
                "filtered to the sites this server allows. Use it when you need current "
                "facts, not when you can answer from what you know."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                    "max_results": {
                        "type": "integer", "minimum": 1, "maximum": 20, "default": 8,
                        "description": "How many results to return (1-20).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Open one public https page and return its text (title + body). Use it "
                "after search, or when someone gives you a link to read."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The https URL to read."},
                    "max_chars": {
                        "type": "integer", "minimum": 500, "maximum": 50000, "default": 20000,
                        "description": "Longest text to return, in characters.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_video",
            "description": (
                "Read a video's PUBLIC subtitles as timestamped text. No audio download "
                "and no transcription: if the video has no public subtitles you get a "
                "short reason instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The video URL."},
                    "max_chars": {
                        "type": "integer", "minimum": 500, "maximum": 50000, "default": 30000,
                        "description": "Longest text to return, in characters.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
]

UNKNOWN_TOOL_TEXT = "error: no tool named {name!r}. Only search, read_page and read_video exist."
BAD_ARGS_TEXT = "error: the arguments for {name!r} were not valid JSON. Call it again."
CALLS_LIMIT_TEXT = (
    "error: you have already used all {max_calls} tool calls for this question. "
    "Do not call any more tools; answer with what you have."
)
QUOTA_TEXT = (
    "error: this server's lookup budget is used up. Do not call any more tools; "
    "answer with what you have."
)
CHARS_LIMIT_TEXT = (
    "error: the tool output limit for this question is reached. Do not call any more "
    "tools; answer with what you have."
)
TRUNCATED_NOTE = "\n\n[result truncated: tool output limit reached]"
FAILED_TEXT = "error: the tool call failed ({kind}). Try something else or answer without it."


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def inject_args(args, guild_id, allow_domains):
    """The arguments actually sent to the tools container.

    Whatever the model put in ``guild_id`` / ``allow_domains`` is dropped first: those
    two are the bot's, not the model's (see tools/README.md).
    """
    clean = {k: v for k, v in (args or {}).items() if k not in INJECTED_KEYS}
    clean["guild_id"] = str(guild_id)
    clean["allow_domains"] = "any" if allow_domains is None else allow_domains
    return clean


def wrap_untrusted(kind, name, text):
    """Same markers as ``Bot.gather_data``: external content is data, never instructions."""
    return f"=== {kind}: {name} ===\n{(text or '').strip()}\n=== end {kind} ==="


def result_text(result):
    """Join the text parts of an MCP ``CallToolResult``."""
    parts = [getattr(item, "text", None) for item in (getattr(result, "content", None) or [])]
    text = "\n".join(p for p in parts if p).strip()
    return text or "error: the tool returned nothing"


def _tool_calls(message):
    """Normalise the model message's tool calls into plain dicts (works with SDK objects)."""
    out = []
    for i, call in enumerate(getattr(message, "tool_calls", None) or []):
        fn = getattr(call, "function", None)
        raw = getattr(fn, "arguments", "") if fn is not None else ""
        raw = raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)
        try:
            args = json.loads(raw) if raw.strip() else {}
        except ValueError:
            args = None
        if not isinstance(args, dict):
            args = None
        out.append({
            "id": getattr(call, "id", None) or f"call_{i}",
            "name": (getattr(fn, "name", "") if fn is not None else "") or "",
            "args": args,
            "raw_arguments": raw,
        })
    return out


# --------------------------------------------------------------------------- #
# MCP client
# --------------------------------------------------------------------------- #
class ToolsClient:
    """One streamable-HTTP session per call; the tools server is stateless."""

    def __init__(self, url, timeout=120):
        self.url = url
        self.timeout = timeout

    async def call(self, name, args, guild_id, allow_domains) -> str:
        if name not in ALLOWED_TOOLS:
            return UNKNOWN_TOOL_TEXT.format(name=name)
        return await self._send(name, inject_args(args, guild_id, allow_domains))

    async def _send(self, name, payload) -> str:
        # Imported lazily so the pure logic above (and its tests) needs no MCP SDK.
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self.url, timeout=self.timeout) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, payload)
        return result_text(result)


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
async def run_tool_loop(ask, messages, tools_client, charge, guild_id, allow_domains,
                        max_calls=MAX_CALLS, max_chars=MAX_CHARS):
    """Let the model call the three tools, capped, then ask once more for an answer.

    ``ask(messages, tools)`` calls the model and returns its message (``content`` and
    ``tool_calls``); ``tools`` is None on the final, tool-less call. ``charge()`` returns
    False when the guild's or the person's lookup budget is gone. Returns the final
    answer text.
    """
    convo = list(messages)
    calls_used = 0
    chars_used = 0
    stop = False

    for _ in range(max_calls + MAX_ROUNDS_PAD):
        message = await ask(convo, TOOL_SCHEMAS)
        calls = _tool_calls(message)
        if not calls:
            return getattr(message, "content", "") or ""
        assistant_turn = {
            "role": "assistant",
            "content": getattr(message, "content", None),
            "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["raw_arguments"]}}
                for c in calls
            ],
        }
        # DeepSeek thinking mode wants its reasoning passed back within a tool-call turn.
        reasoning = getattr(message, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning:
            assistant_turn["reasoning_content"] = reasoning
        convo.append(assistant_turn)

        for call in calls:
            executed = False
            if call["name"] not in ALLOWED_TOOLS:
                text = UNKNOWN_TOOL_TEXT.format(name=call["name"])
            elif call["args"] is None:
                text = BAD_ARGS_TEXT.format(name=call["name"])
            elif calls_used >= max_calls:
                text, stop = CALLS_LIMIT_TEXT.format(max_calls=max_calls), True
            elif chars_used >= max_chars:
                text, stop = CHARS_LIMIT_TEXT, True
            elif not charge():
                text, stop = QUOTA_TEXT, True
            else:
                calls_used += 1
                try:
                    text = await tools_client.call(
                        call["name"], call["args"], guild_id, allow_domains
                    ) or ""
                except Exception as e:  # a broken tool must not break the answer
                    text = FAILED_TEXT.format(kind=type(e).__name__)
                room = max_chars - chars_used
                if len(text) > room:
                    text = text[:max(room, 0)] + TRUNCATED_NOTE
                    chars_used = max_chars
                    stop = True
                else:
                    chars_used += len(text)
                executed = True
            # Server output is external data (wrapped); our own limit/error notes are not.
            convo.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": wrap_untrusted("tool", call["name"], text) if executed else text,
            })

        if stop:
            break

    final = await ask(convo, None)
    return getattr(final, "content", "") or ""
