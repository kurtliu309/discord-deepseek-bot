---
name: design-v2
description: 2026-11-13 grill 定下的 v2 设计：自写 bot 替换 llmcord、主动插话、余额与 /feed、/rule、附件读写、无密钥抓取容器、每日总结与话题、本地群档案
status: 有效
updated: 2026-11-13
---

# v2 设计（2026-11-13 grill 结论）

结论：用自写 bot 替换 llmcord；新增无密钥的 `fetcher` 容器负责一切外网抓取和文件解析；`scheduler` 挂载本地 `data\` 存群档案和总结。模型仍然**没有任何工具**，所有外部内容都由代码取来、当资料喂给模型。

## 容器与隔离

| 容器 | 密钥 | 出网 | 挂载 | 作用 |
|---|---|---|---|---|
| `bot` | Discord token + DeepSeek key | 只 Discord + `api.deepseek.com`（经 egress） | 无 | 回复、插话、余额、`/rule`、`/feed`、附件和网址交给 fetcher |
| `scheduler` | 同上 | 同 bot | `data\`（读写，唯一挂载例外） | 22:00 总结、09:00 话题、群档案 |
| `fetcher` | **无** | 所有公网（经 egress，私网/回环/链路本地照样拦） | 无 | 抓用户贴的网址、RSS、解析附件 |
| `egress` | 无 | — | 无 | Squid：按来源容器区分规则 |

- bot / scheduler 通过内部网络把网址或附件交给 fetcher，拿回纯文本。
- 解析库（pdf、docx 等）只在 fetcher 里跑：被恶意文件攻破也拿不到密钥。
- `data\` 进 `.gitignore`；在 `AGENTS.md` 和 skill 硬规则里登记为唯一例外。

## 谁能用、在哪

- 频道：服务器所有人（按 `@everyone` 身份组放行）。`#general` 全关：不插话，@ 也不回。
- 私信：只回 Kurt，其他人一律不回。
- 只理真人消息，忽略所有 bot。

## 回复

- @ bot 就回。上下文最多 12 条：回复链优先，不够用频道最近消息补。
- 主动插话（`#general` 除外，所有能看到的频道）：
  - 每条真人消息 3% 随机触发；
  - 同一频道 3 分钟内超过 12 条触发；
  - 插话后该频道冷却 10 分钟（不影响 @ 回复）；
  - 用 Discord “回复”接在它自己从最近 12 条里挑的那条下面，关掉被回复人的提醒；可以 @ 人，但只在特别有必要时。
- 人设见 `prompts/default.txt`；回复一两句短话，纯文本。

## 余额

- 频道里的 @ 回复 + 插话 + 读文件的回复共用每天 50 条，每天 09:00 自动恢复满。
- Kurt 的私信、Kurt @ 它、`/rule` 不扣。
- 用完后第一条触发的消息回一句人设口吻的提醒（如 “out of juice. ask kurt to /feed me”），之后安静。
- `/feed`：只有 Kurt 能用，立刻恢复满。
- 余额在 bot 内存里，重启即满（bot 不挂载东西）。

## `/rule`

- 真正的斜杠命令（服务器级），回复只有提问者看得到。
- 单独 `/rule`：简短规则卡（触发方式、冷却、频道、定时任务、余额），结尾提示“可以随便问”。
- `/rule <问题>`：放下人设，正常口吻、简短回答机制问题。
- 绝不显示密钥、`.env` 原文、用户 ID。

## 附件和网址

- 读：只读 Discord 附件和用户贴的网址，由 fetcher 提取文字。
  - 支持：`txt md csv json yaml log`、代码（`py vex vfl js ts cpp h sh glsl` 等）、`usda`、`pdf`、`docx`、`xlsx`、`pptx`。
  - 不支持（回一句读不了）：`hip hipnc hda bgeo`、图片、压缩包、音视频。
  - 单文件 ≤ 10 MB（超过不下载）；每条消息最多 3 个附件；提取文字单文件约 3 万字、合计约 6 万字，超出截断并告知；Office 文件解压后 > 50 MB 拒绝。
  - GitHub 的 `blob` 页面链接自动改抓 `raw.githubusercontent.com`。
- 写：只有对方明确要文档、文件、md、笔记时，才以 `.md` 附件回复；正文正常写，附带的那句聊天用人设口吻；上限 5000 英文单词；文件名由 bot 起；不落盘。
- 读文件或网址的回复照常扣 1 条余额。

## 每晚 22:00 总结

- 读所有能看到的频道（含 `#general`）最近 24 小时的真人消息。太长先分段压缩再合并。
- 参照最近 3 篇总结 + 群档案。
- 英语、人设口吻、最多 5 条要点，不写走向，可以 @ 人，不写隐私。
- 发到 `#deepseek-go-cray`。错过不补。
- 写完后更新群档案。

## 每天 09:00 话题

- 3 个话题（至少 2 个怪话题，剩 1 个由它结合群档案挑）+ 1 句厌世人生感悟。英语，附链接，用 `<...>` 不展开预览。
- 素材：fetcher 抓固定 RSS，模型自己加权挑选：
  - FX / CG：befores & afters、CG Channel、80.lv
  - AI / 科技：Simon Willison、The Verge AI、HN 首页、Show HN
  - 时事：BBC World、Guardian World
  - 澳洲：ABC News、SMH
  - GitHub：GitHub Trending 镜像
  - Reddit（跳过 NSFW）：r/popular、r/nottheonion、r/Showerthoughts、r/mildlyinfuriating、r/oddlyterrifying、r/AskReddit、r/NoStupidQuestions、r/Houdini、r/vfx、r/LocalLLaMA
  - 不用 4chan。搜索 API 以后再研究。
- Reddit 走公开 RSS（匿名 JSON 接口被 Reddit 403），10 个版块合成 2 个合并订阅；官方 API 暂不用，RSS 经常失败再考虑。
- 素材提前抓：08:00 全抓，08:30 只补抓空的源，存 `data/material.json`，09:00 用完即删。
- fetcher 把 `http://` 链接和跳转升级成 `https://`（egress 只放行 443）。
- 错过不补。

## 本地 `data\`

- `group-profile.md`：顶部“Kurt 手写”区 bot 只读不改；其余每晚改写，旧内容慢慢淡出，约 1500 字；不记住址、电话、真名、群外真人。
- `recaps\YYYY-MM-DD.md`：只留最近 10 天。
- 聊天原文不落盘。

## 已确认的事

- 朋友们已同意被读取和总结。
- 新 bot 测试通过前，现在的 llmcord bot 继续跑；scheduler 保持停止。
