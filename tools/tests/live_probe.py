#!/usr/bin/env python3
"""Live probe for the tools MCP server: really calls search / read_page / read_video once.

Needs the LOCAL TEST STACK (tools/docker-compose.test.yml) running:

    docker compose -f tools/docker-compose.test.yml -p ddb-toolstest up -d --build
    # from the host (the test stack publishes 127.0.0.1:18090):
    docker run --rm --network host --add-host localhost:127.0.0.1 \
        -e TOOLS_MCP_URL=http://localhost:18090/mcp ddb-tools:test python tests/live_probe.py

Simplest way that works the same on Windows: run it inside a short-lived container on the
test network, where the service name resolves:

    docker run --rm --network ddb-toolstest_testnet -e TOOLS_MCP_URL=http://tools:8090/mcp \
        ddb-tools:test python tests/live_probe.py

Exit code 0 = all three tools answered; non-zero = at least one failed (details on stdout).
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ.get("TOOLS_MCP_URL", "http://localhost:18090/mcp")
GUILD = "test-guild"
PROBES = [
    ("search", {"query": "Houdini 21 release notes", "guild_id": GUILD,
                "allow_domains": "any", "max_results": 5}),
    ("read_page", {"url": "https://github.com/Thysrael/Horizon", "guild_id": GUILD,
                   "allow_domains": "any", "max_chars": 20000}),
    ("read_video", {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "guild_id": GUILD,
                    "allow_domains": "any", "max_chars": 30000}),
]


def text_of(result) -> str:
    parts = []
    for item in result.content or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


async def probe(session, name, args) -> bool:
    try:
        result = await session.call_tool(name, args)
    except Exception as e:  # transport error
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        return False
    body = text_of(result)
    ok = not getattr(result, "isError", False) and bool(body)
    first = body.replace("\n", " ")[:100]
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {first}")
    if name == "search" and ok:
        try:
            rows = json.loads(body)
            print(f"        -> {len(rows)} result(s), first url: {rows[0]['url'] if rows else '-'}")
        except json.JSONDecodeError:
            print("        -> (not JSON)")
            ok = False
    return ok


async def main() -> int:
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            print(f"connected to {URL}; tools: {tools}")
            results = [await probe(session, name, args) for name, args in PROBES]
    print(f"{sum(results)}/{len(results)} probes passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"[FAIL] could not talk to {URL}: {type(e).__name__}: {e}")
        sys.exit(1)
