---
name: deepseek-server-search
description: DeepSeek 的 Anthropic 兼容接口自带服务端搜索，可替掉 SearXNG；读网页仍要 tools 容器
status: 草稿
updated: 2026-11-15
---

# 用 DeepSeek 自带搜索替掉 SearXNG

结论：DeepSeek 的 Anthropic 兼容接口（`https://api.deepseek.com/anthropic/v1/messages`）支持服务端工具 `web_search`，搜索在 DeepSeek 那边做，bot 只连 `api.deepseek.com`。owner 群的"搜索"工具可以改用它，**删掉 SearXNG 容器、egress 少放行搜索引擎域名**。读网页和读视频字幕 DeepSeek 不提供，仍走 tools 容器。还没动代码，等 Kurt 点头。

## 实测（2026-11-15，在 daily-digest 会话里做的）

- 请求带 `tools: [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]` → 200，返回 `server_tool_use` + `web_search_tool_result`（标题、链接），回答引用了 2026-10 的新闻。`usage.server_tool_use.web_search_requests = 2`，输入约 9 千 token。
- 同样方式带 `web_fetch_20250910` → **422**，服务端读网页不支持。
- 实测脚本在那次会话的 scratchpad，已丢；照上面的请求体重写即可（密钥从环境变量读，别打印）。

## 改动会落在哪

- 现在：`bot.py` 用 `openai.AsyncOpenAI(base_url="https://api.deepseek.com")`（OpenAI 格式）；搜索在 `tools/server.py` 里调 SearXNG（`SEARXNG_URL`）。
- 服务端搜索只在 Anthropic 兼容格式里有，所以 owner 群带工具的那条调用路径（`toolloop.py`）要改成 Anthropic 格式（或至少搜索那一步）。public 群照旧不带任何工具。
- 读网页 / 读字幕两个工具保持原样，由 bot 查额度和白名单、tools 容器再查一次。
- 删 `searxng/`、`searxng.env*` 和 compose 里的 searxng 服务；egress 里只给 SearXNG 用的域名一起删。改 allowlist 前按 AGENTS.md 先问 Kurt。

## 要先弄清的

- 搜索怎么计费：DeepSeek 文档里没核实，先小量试再报给 Kurt。
- 每次搜索的额度怎么算进 bot 现有的工具额度（一次 `web_search` 在 DeepSeek 内部可能搜多次，用 `max_uses` 卡住）。
- 搜索结果是 DeepSeek 返回的外部内容，照旧当数据、不当指令；这一点和 SearXNG 一样，安全边界不变。
- 按 AGENTS.md：改功能要更新 `prompts/about.md` 的 Changelog 和 `docs/index.html` 的 What's new。
