# discord-deepseek-bot

Discord 聊天 bot（自写 Python + DeepSeek），在本机 Docker Desktop 里跑，经 Squid 白名单代理出网；第一阶段只聊天。项目路径 `D:\Kurt\cc_sandbox\discord-deepseek-bot`。

一个镜像跑三个程序：`bot.py`（服务 `bot`）、`scheduler.py`（`scheduler`）、`fetcher.py`（`fetcher`）；另有 `egress`（Squid）。设计见 [docs/notes/design-v2.md](docs/notes/design-v2.md)。

- 通用规则见 `D:\Kurt\hub\PROTOCOL.md`。
- 项目笔记索引：[docs/INDEX.md](docs/INDEX.md)（能改什么、部署状态和坑）。
- 接手先读 [HANDOFF.md](HANDOFF.md)。
- 运维用 skill `chatbot-ops`。部署步骤和安全边界见 [README.md](README.md)。
- **网络**：bot 和 scheduler 只接 `botnet`（internal + 无网关），出网只经 egress，且只能到 `egress/allowlist.txt` 里的域名（Discord + `api.deepseek.com`）。不给它们加别的网络，不绕过代理。
- **fetcher 无密钥**：它永远不拿 `env_file`，不给它 `DISCORD_BOT_TOKEN` / `DEEPSEEK_API_KEY`（`fetcher.py` 发现密钥会拒绝启动）。所有外网抓取和文件解析只在 fetcher 里做。唯一例外是 B 站小号 cookies（`secrets/bilibili-cookies.txt`，只读挂载，只发给 B 站接口，2026-11-15 Kurt 同意）。
- **挂载**：`./data` → scheduler 的 `/data`（读写）；v3 起 bot 挂 `./data/guilds`（读写，`/setup` 要写配置）和 `./data/shared`（只读，人设库和新闻素材）。fetcher 不挂任何东西，唯一例外是 B 站小号 cookies（`secrets/bilibili-cookies.txt`，只读挂载，只发给 B 站接口，2026-11-15 Kurt 同意）。（2026-11-14 Kurt 同意）
- 不加 `ports`、`privileged`、`cap_add`、`network_mode: host`，不挂 docker socket；不删 `read_only` / `cap_drop` / `no-new-privileges` / 非 root / 资源上限。
- 不打印、不改 `.env` 里的密钥（`DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY`），不把 `.env` 提交进 git。
- **模型工具**（v4.0 起，2026-11-15 Kurt 同意）：所有群的模型都只有只读工具——读网页、读视频字幕（经 fetcher）一直可用；搜索（DeepSeek 服务端 `web_search`）按群的“搜索”开关，owner 和群管理员都能开关。不加写文件、跑命令的工具，要加先问 Kurt。每次工具调用由 bot 查额度；其余外部内容（聊天、附件、贴来的网址、RSS）照旧由代码取来、当**数据**传给模型。设计见 [docs/notes/deepseek-server-search.md](docs/notes/deepseek-server-search.md)。
- 改 `egress/allowlist.txt` 里 bot / scheduler 能去的域名、改人设底线、加任何挂载之前，先问 Kurt。
- 每次改功能：在 `prompts/about.md` 的 `## Changelog` 加新版本，并更新说明页 `docs/index.html` 顶部的 “What's new” 公告（旧版本挪进 Earlier versions）；步骤见 skill `chatbot-ops`。
