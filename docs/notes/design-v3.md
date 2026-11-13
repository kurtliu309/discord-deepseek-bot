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

## 待定

- 头脑警察的人设：乌龟（有点丧、想出去玩、像在打字、说不完就分几条）还是旧人设。
- 信任等级 owner / public 两档够不够。
- 先查有没有现成框架能替代（多群配置、权限、`/setup`、LLM 工具调用），再出实现计划。
- news-pipeline 的去留：Horizon、`fetch-and-summarize` 保留；`bot.py`、`push.py`、子代理、Kurt News 应用预计退役。
