# discord-deepseek-bot

Discord 聊天 bot（自写 Python + DeepSeek），在本机 Docker Desktop 里跑，经 Squid 白名单代理出网；第一阶段只聊天。项目路径 `D:\Kurt\cc_sandbox\discord-deepseek-bot`。

一个镜像跑三个程序：`bot.py`（服务 `bot`）、`scheduler.py`（`scheduler`）、`fetcher.py`（`fetcher`）；另有 `egress`（Squid）。设计见 [docs/notes/design-v2.md](docs/notes/design-v2.md)。

- 通用规则见 `D:\Kurt\hub\PROTOCOL.md`。
- 项目笔记索引：[docs/INDEX.md](docs/INDEX.md)（能改什么、部署状态和坑）。
- 接手先读 [HANDOFF.md](HANDOFF.md)。
- 运维用 skill `chatbot-ops`。部署步骤和安全边界见 [README.md](README.md)。
- **网络**：bot 和 scheduler 只接 `botnet`（internal + 无网关），出网只经 egress，且只能到 `egress/allowlist.txt` 里的域名（Discord + `api.deepseek.com`）。不给它们加别的网络，不绕过代理。
- **fetcher 无密钥**：它永远不拿 `env_file`，不给它 `DISCORD_BOT_TOKEN` / `DEEPSEEK_API_KEY`（`fetcher.py` 发现密钥会拒绝启动）。所有外网抓取和文件解析只在 fetcher 里做。
- **挂载**：全栈唯一的挂载是 `./data` → scheduler 的 `/data`（群档案 + 最近 10 天总结）。bot 和 fetcher 不挂任何东西。
- 不加 `ports`、`privileged`、`cap_add`、`network_mode: host`，不挂 docker socket；不删 `read_only` / `cap_drop` / `no-new-privileges` / 非 root / 资源上限。
- 不打印、不改 `.env` 里的密钥（`DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY`），不把 `.env` 提交进 git。
- 不给模型加 tools / function calling / MCP / 代码执行。所有外部内容（聊天、附件、网页、RSS）都由代码取来、当**数据**传给模型。
- 改 `egress/allowlist.txt` 里 bot / scheduler 能去的域名、改人设底线、加任何挂载之前，先问 Kurt。
- 每次改功能：在 `prompts/about.md` 的 `## Changelog` 加新版本，并更新说明页 `docs/index.html` 顶部的 “What's new” 公告（旧版本挪进 Earlier versions）；步骤见 skill `chatbot-ops`。
