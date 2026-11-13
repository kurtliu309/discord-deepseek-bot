---
name: control-panel
description: Kurt 能控制 bot 的哪些项、怎么让 Claude 改、哪些要先问 Kurt、哪些锁死不能动
status: 有效
updated: 2026-11-13
---

# 控制面板：能改什么、怎么改

结论：日常改动都可以直接跟电脑上的 Claude 说（手机 Remote Control 也行），比如“把人设改成 xx”“把 `#random` 也关掉”。Claude 会加载 skill `chatbot-ops` 去改，然后重建容器。只有三把密钥要 Kurt 自己用记事本改 `.env`。

## 可以随时改的

| 想改什么 | 改哪里 | 生效方式 | 跟 Claude 怎么说 |
|---|---|---|---|
| 人设 / 说话风格 | `prompts/default.txt`（bot 和 scheduler 共用） | `docker compose up -d --build bot` | “把人设改成……（风格、语言、口头禅）” |
| 临时人设（不改文件） | `.env` 的 `SYSTEM_PROMPT`（一行，换行写 `\n`） | `docker compose up -d` | “临时把人设换成……”；清空就回到默认 |
| 完全不许 bot 说话的频道 | `.env` 的 `BLOCKED_CHANNEL_IDS`（频道或分类 ID，逗号分隔） | `docker compose up -d` | “`#general` 里别说话” |
| 谁是主人 | `.env` 的 `OWNER_USER_ID`（只填 Kurt 自己） | `docker compose up -d` | 换人先问 Kurt：这决定谁能私信、谁能 `/feed` |
| 每天回复条数 / 恢复时间 | `.env` 的 `REPLY_BUDGET`（50）、`BUDGET_RESET_TIME`（09:00） | `docker compose up -d` | “每天给 80 条” / “改成 10:00 恢复” |
| 主动插话的概率 | `.env` 的 `RANDOM_CHANCE`（`0.03` = 3%） | `docker compose up -d` | “少插点话” / “改成 1%” |
| Houdini/FX 话题的插话概率 | `.env` 的 `FX_TOPIC_CHANCE`（`0.05` = 5%） | `docker compose up -d` | “聊特效时多插点话” / “改成 8%” |
| 刷屏时插话 | `.env` 的 `BURST_COUNT`（12）、`BURST_WINDOW_SECONDS`（180） | `docker compose up -d` | “大家聊快了别急着插” |
| 插话后的冷却 | `.env` 的 `COOLDOWN_SECONDS`（600） | `docker compose up -d` | “插完话安静久一点” |
| 回复带多少条上下文 | `.env` 的 `CONTEXT_MESSAGES`（12） | `docker compose up -d` | “记多几条再说” |
| 模型 | `.env` 的 `DEEPSEEK_MODEL` | `docker compose up -d` | “换成 deepseek-xxx”（Claude 会先查 ID 存不存在） |
| bot 在线状态文字 | `.env` 的 `STATUS_MESSAGE` | `docker compose up -d` | “状态改成……” |
| 每日总结 / 早上话题的时间 | `.env` 的 `SUMMARY_TIME`（22:00）、`TOPICS_TIME`（09:00）、`SCHED_TZ` | `docker compose up -d` | “总结改到 23:00” |
| 话题数量、参考几篇旧总结 | `.env` 的 `TOPICS_COUNT`（3）、`RECAP_CONTEXT`（3） | `docker compose up -d` | “早上给 5 个话题” |
| 总结在本地留几天 | `.env` 的 `RECAP_KEEP_DAYS`（10） | `docker compose up -d` | “总结留一个月” |
| 总结 / 话题发到哪个频道 | `.env` 的 `POST_CHANNEL_ID` | `docker compose up -d` | “总结发到这个频道：……” |
| 总结 / 话题的写法 | `scheduler.py` 里 `recap()` / `topics()` / `update_profile()` 的提示词 | `docker compose up -d --build` | “总结写短一点” / “话题多点 Houdini” |
| 早上抓哪些 RSS / Reddit 版块 | `scheduler.py` 的 `FEEDS`（RSS）、`WEIRD_SUBS` / `OTHER_SUBS`（Reddit） | `docker compose up -d --build` | “多抓点 VFX 的” |
| 群的方向（Kurt 手写） | `data/group-profile.md` 顶部 “Kurt (hand-written, never edited by the bot)” 一段 | 不用重建，scheduler 下次读文件 | “群的方向写成……”；bot 只读不改这段 |
| 马上试跑一次 | — | `docker compose run --rm --no-deps scheduler --once recap`（或 `topics`）；加 `--dry-run` 只打印不发 | “现在发一次总结试试”（先 dry-run） |
| 补满今天的回复额度 | — | 在 Discord 里 `/feed`（只有 Kurt）；或者重启 bot | “喂一下” |
| 启停 | — | `docker compose stop` / `start`；单个：`docker compose stop scheduler` | “先停掉 bot” / “开回来” |
| 看状态、看日志 | — | `docker compose ps` / `logs` | “bot 现在正常吗？” |

注意：`docker compose restart` 不会读新的 `.env`，改 `.env` 后必须 `up -d`；改了代码、人设、`egress/` 要加 `--build`。

### 人设的底线段落

`prompts/default.txt` 最后有一段底线（不针对种族、宗教、性别等群体的仇恨用语，不人肉，不威胁真人）。改人设时保留它，否则服务器可能被 Discord 封。要改底线本身，Claude 会先问 Kurt。

## 要先问 Kurt 才能改的

- 往出网白名单 `egress/allowlist.txt` 里加域名（只影响 bot 和 scheduler；现在只有 Discord 和 `api.deepseek.com`）。
- 改人设的底线段落。
- 加任何挂载（现在全栈只有 `./data` → scheduler 的 `/data` 这一个）。
- 改 `OWNER_USER_ID`（等于换主人：私信、`/feed`、免扣余额都跟着走）。
- 删 `data/recaps/` 里的旧总结（bot 靠它记得群里的长期话题）。
- 改 fetcher 的权限或给它任何密钥（它必须保持无密钥）。

## 只有 Kurt 自己改的

`.env` 里的 `DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY`。用 `notepad .env` 改完告诉 Claude，它跑 `docker compose up -d`。Claude 不读、不打印这三项。

## 锁死、不能动的（隔离要求）

- bot 和 scheduler 只接 `botnet`，出网只经 egress，而且只能到 `egress/allowlist.txt` 里的域名；不给它们加别的网络。
- fetcher 永远没有密钥，也不拿 `env_file`；所有外网抓取和文件解析只在它里面做。
- 全栈唯一的挂载是 `./data` → scheduler 的 `/data`；不给任何容器加 volume、端口、privileged、docker socket。
- 不删只读、非 root、`cap_drop`、`no-new-privileges`、资源上限，不改 `botnet` 里的固定地址（egress 按来源地址分规则）。
- 不给模型加 tools / function calling / MCP / 代码执行；外部内容一律由代码取来当数据喂给它。

完整清单见根目录 `AGENTS.md` 和 skill `chatbot-ops` 的硬规则。

## 每日任务怎么记住“群的方向”

不再靠回读频道里的旧总结。scheduler 的本地记忆在 `data/`（唯一挂载，git 忽略）：

- `data/group-profile.md`：顶部 “Kurt” 区是手写的群方向，bot 只读；下面每晚从当天总结里重写（跑题的东西慢慢淡出），约 1500 字。
- `data/recaps/YYYY-MM-DD.md`：每晚一篇，只留最近 `RECAP_KEEP_DAYS`（10）天。写新总结和早上话题时会参考它。

所以**别删 `data/recaps/`**，删了它就“失忆”。总结会读全服务器 bot 能看到的文字频道（含 `#general`），原文会发给 DeepSeek；不想被读的频道，在 Discord 里去掉 bot 的查看权限即可。聊天原文不落盘。

## 坑

- 在频道里要 @ **bot 本人**（候选列表里带头像、标着 BOT 的那项）。同名的身份组也会出现在列表里，选了它 bot 不会回复。
- `BLOCKED_CHANNEL_IDS` 填了分类 ID 时，分类下所有频道都算，包括以后新建的。
- bot 离线期间收到的消息不会补回复；scheduler 错过的时间点也不会补（主机睡过就跳过）。
- **重启 bot 会把当天的回复额度补满**（余额只在内存里）。
- 只放行 HTTPS：明文 `http://` 的链接 bot 读不了。
- Reddit 现在对匿名请求回 403，早上话题暂时没有 Reddit 素材，这是已知情况，不要为它改代理或伪造 UA。
- 改了 `.env` 记得 `docker compose up -d`，`restart` 不重读。
