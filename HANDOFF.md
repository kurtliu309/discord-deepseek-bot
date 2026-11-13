---
name: handoff
description: 从云端会话交接给 Kurt 电脑上的 Claude：背景、已定决策、当前进度、下一步部署
status: 有效
updated: 2026-11-13
---

# 交接：discord-deepseek-bot

结论：代码已完成并在 Linux Docker 里验证过隔离；**还没在 Kurt 电脑上部署过**。你（电脑上的 Claude）的任务是：装 Docker Desktop → 部署 → 跑隔离自检 → Discord 里实测。

## 背景（为什么是这个方案）

- Kurt 要一个 DeepSeek 驱动的 Discord 聊天 bot，给他和几个朋友用。
- 不用云托管（Fly.io 等）：Kurt 想跑在自己常开的 Windows 电脑上，并用手机 Remote Control 遥控电脑上的 Claude 来管理 bot。
- 不用 Claude Code 的 Discord channels：那样回话的是 Claude 而不是 DeepSeek，而且 Discord 里的人能指挥一个碰得到电脑文件的 agent，违反隔离要求。
- 硬要求：bot 锁死在 sandbox（Docker 容器）里，碰不到电脑文件、进程、局域网；出网只走白名单。控制方向单向：手机 → 电脑上的 Claude → `docker compose` → 容器；容器不能反向碰到 Claude 或主机。

## Grill 已定决策

| 项 | 决定 |
|---|---|
| 使用者 | Kurt + 几个朋友（`ALLOWED_USER_IDS`） |
| 触发 | 频道里 @bot，或私信；白名单里的人都能私信 |
| 服务器 | Kurt 已有的服务器，限定频道（`ALLOWED_CHANNEL_IDS`） |
| 模型 | 只用 `deepseek-flash`，没有 admin，所以没人能 `/model` 切换 |
| 人设 | 英语、活泼、4chan 屌丝风、可以骂脏话；底线：不针对群体的仇恨用语、不人肉/威胁真人（`prompts/default.txt`） |
| 花费 | DeepSeek 只充小额余额；`max_messages: 12` |
| 网络 | 第一阶段就上 Squid 白名单代理（`egress/`），拦私网 IP |
| Kurt 的网络 | 不需要 VPN / 代理，直连 Discord |
| 朋友访问仓库 | 以 collaborator 方式；Kurt 明确说**不需要**“部署前检查 compose 是否被改”的规则 |

## 当前进度

- [x] 代码、`chatbot-ops` skill、README、AGENTS.md 完成，推在分支 `claude/discord-deepseek-bot-gd21vx`。
- [x] hub 已登记（`D:\Kurt\hub\projects\INDEX.md`，hub 仓库同名分支，还没合进 main）。
- [x] Kurt 已在 Developer Portal 建好 bot、关掉 Public Bot（Install Link 设为 None）、打开 Message Content Intent，bot 已进服务器（离线）。
- [x] Kurt 已打开 Discord 开发者模式。
- [x] DeepSeek key、user ID / channel ID：Kurt 已填进 `.env`。
- [x] Docker Desktop 安装、部署、隔离自检（2026-11-13，见 [docs/notes/windows-deploy.md](docs/notes/windows-deploy.md)）。
- [ ] Discord 实测：频道 @bot、私信、非白名单用户无响应。
- [x] v2（自写 bot）代码完成：`bot.py` / `fetcher.py` / `scheduler.py` 写好，隔离在 Docker 里测过，scheduler 的 `--once recap|topics --dry-run` 跑通（见 [docs/notes/design-v2.md](docs/notes/design-v2.md)）。
- [x] 2026-11-13：scheduler 已启动（22:00 总结、08:00/08:30 预抓、09:00 话题）；Reddit 改走公开 RSS（两组合并订阅）；仓库已公开（MIT，默认分支 `main`）；双语说明页已发布为 claude.ai Artifact（私有，Kurt 用 Share 分享）。
- [ ] v2 待办：Discord 实测（@、插话、附件 / 网址、写 md、`/rule`、`/feed`、`#general` 不回、私信只回 Kurt）；第一次 09:00 时确认 Reddit 第二组没被限流。

## 下一步（按顺序）

1. 检查 Docker Desktop；没有就 `winget install -e --id Docker.DockerDesktop`（UAC 弹窗 Kurt 来点），WSL2 后端，装完提醒 Kurt 重启，并打开“登录时启动”。确认 **Docker Engine ≥ 28**（`gateway_mode_ipv4: isolated` 依赖它）。
2. `powercfg /change standby-timeout-ac 0`，插电不睡眠。
3. 加载 skill `chatbot-ops`，按 [README.md](README.md) 部署。复制 `.env.example` 为 `.env` 后**停下来**，让 Kurt 自己用记事本填密钥和 ID。不读、不打印 `.env`。
4. Kurt 说填好后：`docker compose up -d --build`，按 README 跑完隔离自检，确认 `docker network inspect` 里 `botnet` 没有 Gateway。
5. 让 Kurt 在允许的频道 @bot、再私信 bot；再让一个不在白名单的人试，应无响应。

## 没验证过的点（重点看）

- Windows 版 Docker Desktop 是否和 Linux 一样遵守 `gateway_mode_ipv4: isolated` 和 `host.docker.internal` 屏蔽：README 里的自检命令就是为此准备的，失败就停下来告诉 Kurt，不要放松隔离去“修好”。
- `deepseek-flash` 模型 ID 和价格来自搜索结果（云端连不上 DeepSeek 官网）。如果报模型不存在，查 DeepSeek 文档后改 `.env` 的 `DEEPSEEK_MODEL`。
- Squid 只在连接关闭时记 CONNECT 日志，所以长连的 Discord gateway 不会马上出现在 egress 日志里，这是正常的。

## 第二阶段（还没开始）

给 DeepSeek 加工具时：换成自写 bot（DeepSeek function calling + `run_in_sandbox` 工具），另起一个**没有任何密钥**的 sandbox 容器，经同一个 egress 出网，白名单加 PyPI、npm、GitHub、Wikipedia。开始前先和 Kurt 走一遍 grill。
