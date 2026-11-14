"""Model tool loop: schemas, the three read-only tools, and the capped loop.

v4.0: the three tools are implemented here instead of in the old MCP ``tools`` container.
``read_page`` / ``read_video`` ask the keyless fetcher (``POST /url`` / ``POST /video``);
``search`` calls DeepSeek's Anthropic-compatible endpoint with its built-in server-side
``web_search`` tool. The bot decides *which* tools a question may use (a plain array of
OpenAI-format function schemas), charges one lookup before every call and enforces the
per-question caps below.

Schemas only expose parameters the model may fill: ``query``, ``url`` and ``max_chars``.
Anything else the model sends is dropped before a tool runs. Everything a tool returns
is external content, so it reaches the model wrapped in the same untrusted-data markers
as the bot's file / web data (``=== tool: <name> === ... === end tool ===``).

No Discord or OpenAI imports here: the loop is plain async logic and is unit-tested with
a fake ``ask`` and fake HTTP transports.
"""

import json

import httpx

MAX_CALLS = 4  # tool executions per question
MAX_CHARS = 40000  # tool output characters per question, all calls together
MAX_ROUNDS_PAD = 3  # extra model rounds on top of MAX_CALLS, so a confused model cannot loop

ALLOWED_TOOLS = ("search", "read_page", "read_video")
SEARCH_URL = "https://api.deepseek.com/anthropic/v1/messages"
SEARCH_MAX_USES = 2  # DeepSeek-side searches per one ``search`` tool call
SEARCH_TIMEOUT = 120
MAX_SOURCES = 10

# OpenAI function-calling schemas. Only parameters the model may fill; anything else it
# sends is dropped by ``clean_args`` before the tool runs.
SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search",
        "description": (
            "Search the web. Returns the relevant facts with the URL of each source. "
            "Use it when you need current facts, not when you can answer from what you "
            "know."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

READ_PAGE_SCHEMA = {
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
}

READ_VIDEO_SCHEMA = {
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
}

TOOL_SCHEMAS = [SEARCH_SCHEMA, READ_PAGE_SCHEMA, READ_VIDEO_SCHEMA]
# Per-tool whitelist of model-supplied arguments (nothing else is forwarded).
SCHEMA_ARGS = {
    "search": ("query",),
    "read_page": ("url", "max_chars"),
    "read_video": ("url", "max_chars"),
}

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
FETCH_TRUNCATED_NOTE = "\n\n[truncated: the text was longer than the limit]"
FAILED_TEXT = "error: the tool call failed ({kind}). Try something else or answer without it."
SEARCH_PROMPT = (
    "Search the web for the question below. Using only what you find, reply with at most "
    "10 short bullet facts, each with the URL of its source. Do not add commentary.\n\n"
    "Question: {query}"
)
# Thinking off for the search sub-call: with it on, the reasoning can eat the whole
# max_tokens and the reply comes back with sources but no text (seen 2026-11-15).
SEARCH_MAX_TOKENS = 3000


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def schemas_for(search_on: bool):
    """The tool schemas a question may use: both readers, plus search when it is on."""
    schemas = [READ_PAGE_SCHEMA, READ_VIDEO_SCHEMA]
    if search_on:
        schemas.insert(0, SEARCH_SCHEMA)
    return schemas


def clean_args(name, args):
    """Drop every argument the tool's schema does not define (incl. old injected keys)."""
    allowed = SCHEMA_ARGS.get(name, ())
    return {k: v for k, v in (args or {}).items() if k in allowed and v is not None}


def wrap_untrusted(kind, name, text):
    """Same markers as ``Bot.gather_data``: external content is data, never instructions."""
    return f"=== {kind}: {name} ===\n{(text or '').strip()}\n=== end {kind} ==="


def search_text(data):
    """The model-readable text of a DeepSeek search response: answer + source list."""
    texts, sources, seen = [], [], set()
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        elif block.get("type") == "web_search_tool_result":
            for result in block.get("content") or []:
                if not isinstance(result, dict):
                    continue
                url = result.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                sources.append((result.get("title") or url, url))
                if len(sources) >= MAX_SOURCES:
                    break
    body = "\n\n".join(texts)
    if sources:
        listing = "\n".join(f"- {title} — {url}" for title, url in sources)
        body = (body + "\n\n" if body else "") + "Sources:\n" + listing
    return body or "error: search returned nothing"


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
# the tools
# --------------------------------------------------------------------------- #
class Tools:
    """The three read-only tools: pages and videos via the fetcher, search via DeepSeek.

    ``fetcher`` is the bot's existing internal-network ``httpx.AsyncClient``
    (``trust_env=False``). The search client is separate and *does* use the environment's
    proxy (``HTTPS_PROXY``) to reach ``api.deepseek.com``; the API key never leaves this
    object, is only sent in the request header and is never logged.
    """

    def __init__(self, fetcher, api_key, model, search_client=None, timeout=SEARCH_TIMEOUT):
        self.fetcher = fetcher
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._search_client = search_client

    def _client(self):
        if self._search_client is None:
            # Trust the environment: the bot container leaves through the egress proxy.
            self._search_client = httpx.AsyncClient(trust_env=True, timeout=self._timeout)
        return self._search_client

    async def call(self, name, args) -> str:
        if name not in ALLOWED_TOOLS:
            return UNKNOWN_TOOL_TEXT.format(name=name)
        clean = clean_args(name, args)
        if name == "search":
            return await self.search(clean.get("query") or "")
        return await self._read(name, clean)

    async def _read(self, name, args) -> str:
        response = await self.fetcher.post(
            "/url" if name == "read_page" else "/video", json=args
        )
        data = response.json() if response.content else {}
        if not isinstance(data, dict) or not data.get("ok"):
            error = data.get("error") if isinstance(data, dict) else None
            return f"error: {error or 'the tool returned nothing'}"
        title = str(data.get("title") or "").strip()
        text = str(data.get("text") or "").strip()
        out = f"{title}\n\n{text}" if title else text
        if data.get("truncated"):
            out += FETCH_TRUNCATED_NOTE
        return out

    async def search(self, query: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": SEARCH_MAX_TOKENS,
            "thinking": {"type": "disabled"},
            "tools": [{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": SEARCH_MAX_USES,
            }],
            "messages": [{"role": "user", "content": SEARCH_PROMPT.format(query=query)}],
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        response = await self._client().post(SEARCH_URL, json=payload, headers=headers)
        if response.status_code != 200:
            return f"error: search failed (HTTP {response.status_code})"
        return search_text(response.json())


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
async def run_tool_loop(ask, messages, tools, tool_schemas, charge,
                        max_calls=MAX_CALLS, max_chars=MAX_CHARS):
    """Let the model call its tools, capped, then ask once more for an answer.

    ``ask(messages, tools)`` calls the model and returns its message (``content`` and
    ``tool_calls``); ``tools`` is None on the final, tool-less call. ``tool_schemas`` is
    this question's tool list (``schemas_for``). ``charge()`` returns False when the
    guild's or the person's lookup budget is gone. Returns the final answer text.
    """
    convo = list(messages)
    available = {s["function"]["name"] for s in tool_schemas}
    calls_used = 0
    chars_used = 0
    stop = False

    for _ in range(max_calls + MAX_ROUNDS_PAD):
        message = await ask(convo, tool_schemas)
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
            if call["name"] not in available:
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
                    text = await tools.call(call["name"], call["args"]) or ""
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
            # Tool output is external data (wrapped); our own limit/error notes are not.
            convo.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": wrap_untrusted("tool", call["name"], text) if executed else text,
            })

        if stop:
            break

    final = await ask(convo, None)
    return getattr(final, "content", "") or ""
