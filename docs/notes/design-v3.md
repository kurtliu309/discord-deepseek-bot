---
name: design-v3
description: 2026-11-14 讨论定下的 v3 方向：多群配置（每群一个文件夹）、功能登记表 + 信任等级天花板、只有 Kurt 可用的 /setup、模型主动工具（搜索/读网页/读视频）只给 owner 群、新闻推送作为 news 功能；未实现，待查现成方案后出计划
status: 有效
updated: 2026-11-14
---

# v3 设计（草案，2026-11-14）

结论：不另起新 bot。在 v2 上加三样东西：**每个群一份配置和记忆**、**按群开关的功能（信任等级封顶）**、**owner 群里模型可以自己调只读工具**。新闻推送并进来作为一个功能。v2 的容器隔离（bot/scheduler 白名单、fetcher 无密钥）保持不变。

前情：2026-11-13 先做了独立的 `news-pipeline`（Horizon 日报 + 新 Discord 应用 Kurt News + 子代理写日报），能推送，但聊天很弱（模型没有任何工具）。讨论后改为重构旧 bot。原方案见 `D:\Kurt\hub\global\inbox\002-news-pipeline.md`。

## 为什么改回旧 bot

- 之前反对给旧 bot 加权限，前提是“朋友群 + 全网和本机全权限”。现在两个前提都变了：头脑警察只有 Kurt 和女朋友；这一轮只给**只读**工具，不碰本机。
- “关着的”和“放开的”在 v2 里本来就拆在不同容器：新工具全是“无密钥、要上网”的活，放 fetcher，bot 侧不用放开。
- 本机全权限（写文件、跑命令）不在本轮。要做时考虑过 Claude Code Channels（官方 Discord 插件，有权限确认），但它只能用 Claude 额度、要常开会话，Kurt 选择继续用 DeepSeek bot。

## 1. 每个群一个文件夹

```
data\
├─ shared\
│  ├─ personas\            人设库（default.md、turtle.md …）
│  └─ news\                日报素材和存档：抓一次、打分一次，各群共用
└─ guilds\<群ID>\
   ├─ config.yaml          这个群的设置（见下）
   ├─ profile.md           群档案：上面 Kurt 手写，下面 bot 每晚改写
   ├─ interests.md         这个群的口味（日报按它挑）；每个群各写各的
   ├─ recaps\              每晚总结，最近 10 天
   └─ news-seen.json       这个群推过的新闻（各群各自去重）
```

- **读写规则**：bot 在哪个群里说话，只读写那个群的文件夹 + `shared\`。不同群之间不互相读。
- 私信（只回 Kurt）用一个固定的 `guilds\dm-owner\`。
- **待 Kurt 同意的改动**：`bot` 容器要只读挂载 `data\guilds\` 和 `data\shared\`（v2 规定 bot 不挂任何东西）。

## 2. 功能登记表 + 信任等级

每件 bot 会做的事登记成一个功能：名字、最低信任等级、设置项、默认值。

| 功能 | 是什么 | 最低等级 |
|---|---|---|
| `chat` | @ 它会回 | public |
| `interject` | 主动插话（概率、冷却） | public |
| `read_links` | 读群里**有人贴的**链接和附件（v2 已有，代码决定读什么） | public |
| `tools` | **模型自己**搜索、读网页、读视频字幕 | owner |
| `news` | 每日新闻推送（频道、时间、条数） | public |
| `recap` | 每晚总结 | public |
| `topics` | 每天话题 | public |

```yaml
# data\guilds\<群ID>\config.yaml（示例：头脑警察）
name: 头脑警察
trust: owner                # owner = 只有自己人；public = 有外人
consent: true               # 群里的人已同意被读取和总结
persona: turtle
features:
  chat: on
  interject: { on: true, chance: 0.03 }
  read_links: { on: true, allow_domains: any }
  tools: { on: true, allow_domains: any }
  news: { on: true, channel: 1570775686862471301, time: "09:00", items: 5 }
  recap: { on: true, channel: 1570775686862471301, time: "22:00" }
  topics: off
quiet_channels: []
```

- **信任等级是天花板**：功能要求的等级高于群的等级时，配置写 on 也不生效，`/setup` 里显示为灰色。
- **每件事发生前查表**：哪个群 → 读它的配置 → 功能开着且等级够 → 只用这个群的文件夹去做。
- **加新功能**：在登记表加一行，`/setup` 菜单和配置检查自动带上，各群默认关。
- **没走流程的群**：bot 一句话不说，只记日志。

## 3. 两种“读网页”怎么分档

- `read_links`：代码从消息里找链接 → fetcher 抓 → 当资料喂给模型。读什么是人选的。
- `tools`：模型通过 function calling 自己发起搜索、读网页、读视频，最多来回几轮。读什么是模型选的。
- **靠代码执行，不靠提示词**：public 群的 API 请求里**不带工具列表**，模型无法调用；fetcher 每次抓取带上群 ID，再查一次等级和网站白名单（第二道保险）。
- **网站白名单**：`allow_domains` 按群、按功能各一份；白名单外的搜索结果被过滤，读白名单外的链接直接拒绝并告诉模型原因。
- 工具全是只读的：没有写文件、跑命令、改设置。

## 4. `/setup`（只有 Kurt）

- 注册时要求“管理服务器”权限：没有这个权限的人在菜单里看不到。
- 代码检查用户 ID：不是 Kurt 一律拒绝。
- 所有回复用“仅你可见”消息：设置界面、下拉菜单、结果，群里其他人看不到。
- 内容：信任等级、人设、各功能开关和频道/时间、安静频道、隐私确认。

## 5. 新闻推送（`news` 功能）

- 素材：Horizon（已实测，原生支持 DeepSeek），继续在主机上用任务计划程序跑，结果写进 `data\shared\news\`，scheduler 到点读出来按各群口味挑、去重、发。（Horizon 要密钥又要全网，放哪个容器都别扭，所以留在主机，靠文件夹交接。）
- 频道里发短消息版：人设开场一句 + 每条新闻一条短消息；完整版存档在 `data\shared\news\digests\`。
- 以后要 B站、X：加 RSSHub 作来源。

## 6. 工具的实现

- 搜索：SearXNG 自建容器（免费、无 key），和 fetcher 一样只经 egress 出网；不好用再换 Brave Search API。
- 读网页：fetcher 现有能力。
- 读视频：先只拿字幕（yt-dlp + cookies，逻辑见 hub skill `fetch-and-summarize`），放 fetcher。Whisper 转写要用主机显卡，fetcher 现在禁止连本机，要开一个只放行一个端口的口子——**要 Kurt 同意，本轮先不做**。

## 7. 固定流程（写进 `chatbot-ops`）

1. **加群**：给邀请链接 → bot 读出频道列表 → 问 Kurt 信任等级、人设、日报频道、安静频道、要不要总结 → 隐私确认 → 建 `guilds\<群ID>\` → 重新加载 → 在群里发测试消息 → 项目文档群清单加一行（只写群名和等级）。
2. **改设置**：改那个群的 `config.yaml`（或 `/setup`）→ 重新加载。
3. **退群**：bot 离开 → 那个群的文件夹移进 `.trash`，Kurt 确认后再删。
4. **体检**（每周）：bot 在哪些群、配置缺项、记忆超长、“在群里没配置”或“有配置不在群里”。

## 8. 人设库（模块化）

```
data\shared\personas\
├─ _base.md          底线：所有人设共用，只有 Kurt 能改（改前先问 Kurt）
├─ tiredfx\          旧人设（TiredFX Guy）：character.md、examples.md
├─ turtle\           乌龟：有点丧、想出去玩
└─ _styles\          打字风格，任何人设都能配：chatty.md（短句、分几条发）、normal.md
```

- 可选 `persona.yaml`（v3.6）：人设自己的数值，`interject.chance_scale`、`interject.fx_multiplier`、`interject.cooldown_scale`、`interject.burst_scale`、`typing.speed`。缺文件或没写 `interject` 块 = 全部默认（不缩放，FX 话题也没有加成）。数值由代码读、范围夹死（三个倍数 0.2–3，`burst_scale` 0.5–3，打字 0.5–2，插话概率硬上限 100%、冷却上限 1 小时），坏文件不影响运行；内容不进系统提示词。
- 三个倍数各自独立：`chance_scale` 缺省 1.0、始终乘到基础概率上；`fx_multiplier` 写了才在 FX 话题再乘它，没写则 FX 话题没有加成（和普通消息同概率）。`burst_scale` 只缩放刷屏阈值 `burst_count`（`max(1, round(burst_count × burst_scale))`），不动时间窗口。`/rule` 用同一组函数算，显示的数字和实际触发一致。
- v3.6 起群配置里没有单独的 FX 话题概率项了：FX 话题的加成完全由人设的 `fx_multiplier` 决定。旧配置里残留的这个键会被静默忽略（配置模型是严格模式，只在 `interject` 这一块单独丢弃它）。
- 群配置里选：`persona`、`style`、`language`、`nickname`。系统提示拼接顺序：底线 → 人设 → 风格 → 群补充。
- 只读挂进容器，改了重新加载即可，不用重建镜像。聊天、总结、话题、新闻开场共用同一份人设。
- 迁移：`prompts/default.txt` 拆成 `_base.md` + `tiredfx/character.md`（底线部分逐条给 Kurt 过目）；乌龟从 `news-pipeline\prompts\reply.md` 搬来，打字节奏从 `news-pipeline\bot.py` 的 `split_turns`/`typing_delay` 搬来做 `chatty`。

## 9. 信任等级到底信任什么

信任的是**群里所有能发消息的人**。区别只有一条：**模型能不能自己动手找东西**（搜索、打开任意链接、读视频；下一轮还有读本机指定文件夹）。能找东西就可能被群里的人利用（藏指令的网页、烧余额），所以只给 owner 群。

- 抓取永远是代码在做；变的是“抓什么由谁定”：每日新闻 = 配置写死；`read_links` = 群里的人贴了什么；`tools` = 模型提请求、代码按白名单和上限执行。
- 2026-11-14 Kurt 定：owner 群**放开**（能搜、能打开任何链接），靠白名单 + 次数上限把关。只做 owner / public 两档，配置格式预留以后加档。

## 10. 工具走 MCP

```
bot（有密钥，白名单不变）── MCP 内部 HTTP ──▶ tools 容器（无密钥，新增）
                                              ├─ search     → SearXNG 容器（新增）
                                              ├─ read_page  → 复用 fetcher.py
                                              └─ read_video → 复用 fetch_video.py 的字幕路径
tools / SearXNG 出网只经 egress：公网 443，私网和本机照拦
```

- 一次提问：bot 查群配置决定请求带不带工具 → DeepSeek 发起工具调用 → bot 检查次数（≤4 轮）、白名单、累计字数 → 交给 tools 容器 → 结果塞回 → 直到最终回答。每轮工具调用计入当天余额。
- 三道把关：bot 发请求前、bot 每次调用前、tools 容器执行时（带群 ID 再查一次）。
- MCP 服务器自己写一个薄的（官方 Python SDK 的 FastMCP），直接调已有代码；不装三个现成 MCP（各自要桥接 stdio、过代理，容器太多）。接口标准，以后可替换。
- 下一轮读本机文件夹：官方 filesystem MCP，单独容器，只读挂载指定目录。
- **要 Kurt 同意**：新增 tools、SearXNG 两个容器及其 egress 规则；bot/scheduler 白名单不变。

## 11. 现成方案调研（2026-11-14）

- 整套替代：LangBot（约 1.9 万★，Discord、DeepSeek、工具、MCP、插件、访问控制）最接近，但没有定时推送和按群记忆的说明，现有功能要当插件重写，且会失去容器隔离；TomoriBot（按服务器 `/config`、SearXNG、MCP）太小、TypeScript、记忆跨服务器；Red-DiscordBot 要整体改写成 cog。**都不整套替换。**
- 直接复用的零件：MCP 作工具接口、SearXNG、Horizon、discord.py 自带的命令权限和仅你可见消息、Pydantic 校验配置。
- 已做完可复用（2026-11-13）：hub skill `fetch-and-summarize`（30 个测试，字幕/Whisper/网页）、Horizon（已配 DeepSeek）、`news-pipeline\fetch.py` 的解析和去重（改成按群）、打字节奏。退役：`news-pipeline` 的 `bot.py` 主体、`push.py`、`news-agent.ps1`、Kurt News 应用。
- `fetch_video.py` 按 Windows 写（AppData 的 ffmpeg、显卡 DLL），进 Linux 容器只搬字幕路径；Whisper 转写留在主机，下一轮再接（要在 fetcher 的“禁止本机”规则上开一个端口口子，需 Kurt 同意）。

## 12. 实现中追加的决定（2026-11-14）

- **额度两层**：`replies_per_day`/`replies_per_user`、`lookups_per_day`/`lookups_per_user`，哪层先到都拦；用完立刻私信 owner（同一件事一个周期一次）；`/feed` 补全群，`/feed @某人` 只补这个人。简单版：不合并通知、计数不存盘（重启回满）。
- **用户黑名单** `blocked_users`：@ 不回、不插话、不计入插话计数；总结照读。owner 永远不会被拉黑。
- **管理员** `admins`：能用 `/setup`、`/feed`；仍需 Discord“管理服务器”权限才看得到 `/setup`。`trust` 和 `admins` 只有 owner 能改（服务端强制）。owner 永远是管理员。
- **改配置通知**：任何人（含 owner）保存 `/setup` 且有变化，私信 owner，只列改动，名单显示人名不显示 ID。
- **`/setup` 中英切换**：每页一个按钮，按群记 `setup_language`（只管界面语言）。

## 已定

- 头脑警察：`persona: tiredfx`（旧人设），`trust: owner`。
- 信任等级 owner / public 两档；owner 群工具放开。

## 待定（进计划模式 grill）

- 实现分批、并行方式、每批的验收。
