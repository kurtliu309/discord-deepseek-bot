---
name: control-panel
description: Kurt 能控制 bot 的哪些项、怎么让 Claude 改、哪些要先问 Kurt、哪些锁死不能动
status: 有效
updated: 2026-11-13
---

# 控制面板：能改什么、怎么改

结论：日常改动都可以直接跟电脑上的 Claude 说（手机 Remote Control 也行），比如“把人设改成 xx”“加一个朋友 ID”。Claude 会加载 skill `chatbot-ops` 去改，然后重建容器。只有三把密钥要 Kurt 自己用记事本改 `.env`。

## 可以随时改的

| 想改什么 | 改哪里 | 生效方式 | 跟 Claude 怎么说 |
|---|---|---|---|
| 人设 / 说话风格 | `prompts/default.txt` | `docker compose up -d --build bot` | “把人设改成……（风格、语言、口头禅）” |
| 临时人设（不改文件） | `.env` 的 `SYSTEM_PROMPT`（一行，换行写 `\n`） | `docker compose up -d` | “临时把人设换成……”；清空就回到默认 |
| 谁能用 | `.env` 的 `ALLOWED_USER_IDS`（逗号分隔） | `docker compose up -d` | “加上这个 user ID：……” |
| 在哪个频道回复 | `.env` 的 `ALLOWED_CHANNEL_IDS`（频道或分类 ID） | `docker compose up -d` | “再允许这个频道：……” |
| 能不能私信 | `.env` 的 `ALLOW_DMS` | `docker compose up -d` | “关掉私信” |
| 模型 | `.env` 的 `DEEPSEEK_MODEL` | `docker compose up -d` | “换成 deepseek-xxx”（Claude 会先查 ID 存不存在） |
| 纯文本回复（不用 embed） | `.env` 的 `USE_PLAIN_RESPONSES` | `docker compose up -d` | “改成纯文本回复” |
| bot 在线状态文字 | `.env` 的 `STATUS_MESSAGE` | `docker compose up -d` | “状态改成……” |
| 启停 | — | `docker compose stop` / `start` | “先停掉 bot” / “开回来” |
| 看状态、看日志 | — | `docker compose ps` / `logs` | “bot 现在正常吗？” |

注意：`docker compose restart` 不会读新的 `.env`，改 `.env` 后必须 `up -d`。

### 人设的底线段落

`prompts/default.txt` 最后有一段底线（不针对种族、宗教、性别等群体的仇恨用语，不人肉，不威胁真人）。改人设时保留它，否则服务器可能被 Discord 封。要改底线本身，Claude 会先问 Kurt。

## 要先问 Kurt 才能改的

- 往出网白名单 `egress/allowlist.txt` 加域名（第一阶段只有 Discord 和 `api.deepseek.com`）。
- 改人设的底线段落。
- `ALLOW_PUBLIC=true`（对所有人开放）。
- 升级 llmcord（只能按固定 commit + sha256 升级）。

## 只有 Kurt 自己改的

`.env` 里的 `DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY`。用 `notepad .env` 改完告诉 Claude，它跑 `docker compose up -d`。Claude 不读、不打印这三项。

## 锁死、不能动的（隔离要求）

bot 只接 `botnet`；不加 volume、端口、privileged、docker socket；不删只读、非 root、cap_drop、资源上限；第一阶段不给模型加工具或代码执行。完整清单见根目录 `AGENTS.md` 和 skill `chatbot-ops` 的硬规则。

## 坑

- 在频道里要 @ **bot 本人**（候选列表里带头像、标着 BOT 的那项）。同名的身份组也会出现在列表里，选了它 bot 不会回复。私信不用 @。
- bot 离线期间收到的消息不会补回复。
