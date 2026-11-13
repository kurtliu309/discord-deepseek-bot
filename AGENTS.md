# discord-deepseek-bot

Discord 聊天 bot（llmcord + DeepSeek），在本机 Docker Desktop 里跑，经 Squid 白名单代理出网；第一阶段只聊天。项目路径 `D:\Kurt\cc_sandbox\discord-deepseek-bot`。

- 通用规则见 `D:\Kurt\hub\PROTOCOL.md`。
- 接手先读 [HANDOFF.md](HANDOFF.md)。
- 运维用 skill `chatbot-ops`。部署步骤和安全边界见 [README.md](README.md)。
- bot 只接 `botnet`（internal + 无网关），出网只经 egress；不给 bot 加别的网络。
- 不加 volume、bind mount、ports、privileged、cap_add，不挂 docker socket；不删 read_only / cap_drop / no-new-privileges / 非 root / 资源上限。
- 不打印、不改 `.env` 里的密钥（`DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY`），不把 `.env` 提交进 git。
- 第一阶段不给模型加 tools / function calling / MCP / 代码执行。
- 加白名单域名、改人设底线、设 `ALLOW_PUBLIC=true` 之前先问 Kurt。
- llmcord 只能按固定的 commit + sha256 升级，不改 `llmcord.py` 本体。
