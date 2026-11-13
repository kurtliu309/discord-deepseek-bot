---
name: handoff
description: v3 交接（2026-11-14）：v3.0 地基在分支 v3 上、测试栈在跑、等 Kurt 验收；v3.1 工具容器和 v3.2 新闻素材已做完待接入；接手先读这页
status: 有效
updated: 2026-11-14
---

# 交接：discord-deepseek-bot v3

结论：**v3.0（多群配置）代码完成，在分支 `v3`，测试栈在 `#bot-test` 跑着，等 Kurt 验收**。正式 bot（TiredFX Guy）仍是 v2，没动过。v3.1 工具容器、v3.2 新闻素材都已做完并单独测通，还没接进 bot。

设计全文：[docs/notes/design-v3.md](docs/notes/design-v3.md)。计划（grill 结果）：`C:\Users\suzyz\.claude\plans\lazy-inventing-globe.md`。v2 时期的交接内容见 git 历史。

## 现在在跑什么

| 东西 | 状态 |
|---|---|
| 正式栈 `discord-deepseek-bot*`（4 个容器） | v2，镜像 `discord-deepseek-bot:local`（= `:v2.2` 标签），**不要动** |
| 测试栈 `-p ddb-staging`（`docker-compose.staging.yml`） | v3 代码，镜像 `:v3dev`，bot = Kurt News 应用（显示名 `news-turtle#8647`），`env_file: .env.staging`，数据 `data-staging\`，只在 `#bot-test`（`1570775686862471301`）说话 |
| 任务计划程序 `HorizonNewsMaterial` | 每天 07:30 跑 `news-pipeline\horizon_material.py`，写 `data-staging\shared\news\material-*.json`（正式上线时改成 `data\`） |
| news-pipeline 的乌龟 `bot.py` | 已停，代码保留到 v3.2 后退役 |

- 头脑警察群 ID `1317847113559965736`；配置 `data-staging\guilds\1317847113559965736\config.yaml`（tiredfx、owner、回复 100/每人 30、查询 60/每人 20）。
- 备份：`data-backup-20261114\`（v2 原始数据）。
- 改了代码后重启测试 bot：`docker build -t discord-deepseek-bot:v3dev .` → `docker compose -f docker-compose.staging.yml -p ddb-staging up -d --force-recreate bot`。
- 单测：`docker run --rm -v "${PWD}:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python discord-deepseek-bot:v3dev -m unittest discover -s tests`（2026-11-14 最后一次：245 个全过）。

## v3.0 已做（分支 `v3`，5 个提交）

- 每群一个文件夹 `data/guilds/<群ID>/`（config.yaml、profile/ 六块、recaps/、interests.md）；私信 = `dm-owner`。
- 功能登记表 + 信任等级（owner/public）封顶；`guildcfg.py` 热重载。
- 人设库 `data/shared/personas/`：`_base.md`（底线 7 条，Kurt 已审）、`tiredfx`、`turtle`、`_styles/normal|chatty`；`persona.py` 拼接。
- 按群记忆 `guildmem.py`（块上限、token 预算）；scheduler 按群跑 recap/topics。
- `/setup`（`setup_ui.py`）：三页、中英切换（按群记 `setup_language`）；只有 owner 和群 `admins` 能用（还要 Discord“管理服务器”权限）；`trust` 和 `admins` 只有 owner 能改（服务端强制）；每次保存有变化就私信 owner，只列改动。
- 额度两层：群/每人 × 回复/查询；用完立刻私信 owner（同一件事一个周期一次）；`/feed` 补全群，`/feed @某人` 只补这个人；计数不存盘（重启回满，Kurt 定的简单版）。
- 用户黑名单 `blocked_users`：@ 不回、不插话；总结照读。
- 未配置的群：静默 + 私信 owner 去 `/setup`；首次 `/setup` 生成保守默认（public、consent 关、只开聊天）。
- `migrate_v3.py`：v2 数据 → 按群结构（测试栈已用真设置跑过）。
- `AGENTS.md` 的工具规则和挂载规则已按 Kurt 同意改写。

## 下一步（按顺序）

1. ~~等 Kurt 在 `#bot-test` 验收 v3.0~~ **已验收（2026-11-14）**。语言行为维持原样（默认按设置，有人用别的语言就跟着回），Kurt 确认没问题。原验收清单：@ 聊天（TiredFX 口吻、会用群档案）、`/setup` 改一项立即生效、中英切换、`/feed` / `/feed @人`、`/rule`、黑名单、管理员（女朋友账号）、改设置收到私信、把每人上限临时改 2 测“没电”和私信。另建一个空服务器用邀请链接拉测试 bot，走一遍“未配置 → 私信 → /setup”。测试 bot 邀请链接：`https://discord.com/oauth2/authorize?client_id=1570772366794166322&permissions=2147568640&scope=bot%20applications.commands`
2. **v3.1 接工具：代码已合（提交 7410ba3 + bing 修复），测试栈已上线（tools/searxng-staging 在跑，头脑警察 staging 配置 `tools.on: true`），三个工具从 bot 容器实测通，等 Kurt 在 `#bot-test` 验收。** 坑：SearXNG 默认 `bing disabled: true`，`keep_only` 不会打开它，要在 `engines:` 里显式 `disabled: false`；brave 常被限流。以下为原计划（
线 B 已在 `tools/`、`searxng/`、`docker-compose.tools.yml`、`egress/squid.tools.snippet.conf`，29 个单测过，三个工具实测通）：
   - 合并：tools（172.30.0.21）、searxng（.22）进 compose（正式和 staging 各一份 IP），squid 片段并进 `squid.conf` / `squid.staging.conf`，SearXNG `secret_key` 部署时生成、不提交；不开 DuckDuckGo（验证码）。
   - bot 侧：`mcp>=1.30,<2` 客户端连 `http://<tools IP>:8090/mcp`；DeepSeek function calling 循环插在 `Bot.reply` 调模型之前；`effective(cfg,"tools")` 才带工具；给模型的工具 schema 里**不出现** `guild_id`/`allow_domains`（由 bot 注入）；每次调用 `try_charge_lookup()`；≤4 次、抓回合计 ≤4 万字符；public 群不带工具。
   - 派 Flash（**只用 Flash，见 delegate-coding 2.2.0**），任务拆小。
3. **v3.2 新闻：代码已合（5906f88），测试栈 scheduler 已换新镜像；头脑警察 staging 配置 `news.on: true`、频道 `#bot-test`、09:00、5 条；dry-run 两次通过（挑选按兴趣生效、摘要按句截断）。`data-staging\guilds\1317847113559965736\interests.md` 是译自 news-pipeline profile 的草稿，Kurt 已审（删了格式要求段）。2026-11-14 已真推一次到 `#bot-test`（5 条，存档 `data-staging\shared\news\digests\`）。** 坑：思考模式下 max_tokens=400 会被推理吃光返回空；scheduler 内存上限 128MB，**手动跑不要 `docker exec`**（两个进程会 OOM），要先 `stop scheduler` → `run --rm --no-deps scheduler --once news --guild <ID>` → `start scheduler`。原计划：scheduler 加 `news` 功能，读 `data/shared/news/material-<日期>.json`，按群 `interests.md`（没有就 `threads`）挑 N 条、按群 `news-seen.json` 去重、人设开场一句 + 每条一条短消息 + 可选吐槽；长文存 `data/shared/news/digests/`。头脑警察 09:00、5 条。`interests.md` 由 Kurt 手写（可先把 `news-pipeline\memory\profile.md` 译成英文当草稿）。
4. **正式上线：2026-11-14 已做到“正式栈跑 v3”**——备份 `data-backup-20261114-predeploy\`，`migrate_v3.py` 已对 `data\` 跑完，`replies_per_day` 改 100，人设库和 `interests.md` 已复制，`material-*.json` 已复制到 `data\shared\news\`，`docker compose up -d --build` 完成（6 个容器，bot = tired-fx-guy），镜像标签 `:v3.0`（回滚用 `:v2.2`）。正式配置里 `tools`、`news` 仍是关的，等 Kurt 定。**还没做**：`HorizonNewsMaterial` 改写 `data\`；about.md / index.html（已派 Flash）；合并 `v3` 进 `main`（先问 Kurt）。原计划：打镜像标签、备份 `data\`、对 `data\` 跑 `migrate_v3.py`（`docker run --env-file .env ...`，迁移后把头脑警察 `replies_per_day` 改成 100）、复制 `data-staging\shared\personas\` 到 `data\shared\`、`docker compose up -d --build`；`HorizonNewsMaterial` 改写 `data\`；补 `prompts/about.md` changelog 和 `docs/index.html` What's new（v3.0 草稿 A3 已写，后来加的中英切换、两层额度、黑名单/管理员还没写）；`check.py` 项目；合并 `v3` 进 `main`（先问 Kurt）。
5. **收尾**：`chatbot-ops` skill 补固定流程（加群/改设置/退群/体检，含正式 bot 邀请链接）；`.env` 里旧 llmcord 变量（`ALLOW_DMS`、`ALLOWED_USER_IDS` 等）清理；Kurt News 应用和 news-pipeline 的 `bot.py`/`push.py`/`news-agent.ps1` 退役；hub inbox `002-news-pipeline.md` 标完成。

## 待办 / 注意

- **搜索质量差（2026-11-14 Kurt 实测）**：SearXNG 里 brave 一直被限流，bing 抓取结果是垃圾（搜 Houdini 22 返回魔术师维基、服装品牌，甚至 AirPods），模型换 7 次关键词也找不到有效结果。读网页、读视频正常。方案：换正式搜索 API（推荐 Tavily，备选 Brave Search API，额度价格待核实）；Kurt 自己注册拿 key，key 放 tools 容器（要改 AGENTS.md“tools 无密钥”规则，需 Kurt 同意），squid 不用改。Kurt 定了再做。

- **hub 未提交**：`global/agent-ops/skills/delegate-coding/` 三个文件有改动（含 2.2.0“只用 Flash”和别处来的改动），Kurt 说“能提交的时候再提交”——提交前先列出全部改动给 Kurt 确认。
- `%TEMP%\squidcheck` 里两个临时文件删不掉，无害，Kurt 可手动删。
- Whisper 转写（读视频没字幕时）留到以后：要在 fetcher/tools 的“禁止本机”规则上开端口口子，需 Kurt 同意。读视频第一版不带 cookies。
- “访问 Kurt 电脑”（读指定文件夹）下一轮再做：官方 filesystem MCP、只读挂载指定目录，那一轮断开上网。
- 测试栈的 recap（22:00）和 topics（09:00）是开着的，会往 `#bot-test` 发。
