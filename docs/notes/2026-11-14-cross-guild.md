---
name: cross-guild-2026-11-14
description: 2026-11-14 排查：正式 bot 早上 9 点把頭腦警察的 topics 发进 FX NEVER SLEEPS；原因是 v2 数据迁错了群。已做的临时修复、Kurt 已定的事、还没讨论的待办
status: 有效
updated: 2026-11-14
---

# 跨群发帖与记忆串群（2026-11-14）

结论：**9 点那条是頭腦警察（`1317847113559965736`）的 topics 发的，不是 news。** 頭腦警察的配置和记忆其实是 FX NEVER SLEEPS（`1537280083830775820`）的 v2 数据，因为迁移时用错了群 ID。recap 和 topics 已临时关掉；其余的修法还在和 Kurt 一条一条讨论，**没定的不要动**。

## 现象

Kurt 在 FX NEVER SLEEPS 的 `/setup` 里关了 news，但早上 9 点群里还是收到了“新闻”。

## 原因

- v2 的 bot 一直跑在 FX NEVER SLEEPS。v3 迁移时，`migrate_v3.py` 用的是 `--guild-id 1317847113559965736`（頭腦警察），把 v2 的设置和记忆都写进了頭腦警察的文件夹：
  - recap/topics 频道 `1570694030440796280`，来自 v2 的 `POST_CHANNEL_ID`。这个频道在 FX NEVER SLEEPS，Kurt 在那边 `/setup` 里选的也是它，`ChannelSelect` 只会列出本群的频道。
  - 安静频道 `1537280085717950559`，是 FX NEVER SLEEPS 的 #general（v2 的 `BLOCKED_CHANNEL_IDS`）。
  - `consent: true`，还有 `profile/` 六块、`recaps/2026-11-13.md`。recap 里提到的都是 FX NEVER SLEEPS 的人，包括该群 admins 里的两个 ID。
- `scheduler.py` 直接 `POST /channels/<ID>/messages`，**不检查频道属不属于当前群**，所以跨群发出去了。
- 证据：
  - 頭腦警察 `state.json` 在 09:00:32 记了 `topics`。
  - FX NEVER SLEEPS 的 `state.json` 里只有 prefetch/refetch/topics，没有 news。它自己的 topics 在 10:30 也发了一次。
  - news 开关本身正常，`effective(cfg,"news")` 是关的。

## 附带发现的问题

- **隐私**：頭腦警察的 recap 如果开着，会读頭腦警察的聊天，再把总结发到 FX NEVER SLEEPS 的频道（已关，见下）。頭腦警察 `consent: true` 加上 FX 的群档案，正式 bot 在頭腦警察聊天时可能提到 FX NEVER SLEEPS 的人和梗。**chat 和 interject 仍开着，这个风险还在。**
- FX NEVER SLEEPS 自己的文件夹只有 `config.yaml` 和 `state.json`，没有 profile，等于正式 bot 在那个群“失忆”了。
- 测试栈 `data-staging/guilds/1317847113559965736/` 的 profile、recaps、interests 与正式那份逐文件 sha1 相同，是同一份迁移数据。两边文件夹分开，不共享、不互相写，但内容都是 FX NEVER SLEEPS 的。
- 正式和测试的 `DEEPSEEK_API_KEY` 相同（比的是哈希），余额共用。Discord token 不同。
- 两个应用都在頭腦警察注册了 `/setup`、`/feed`、`/rule`，菜单里有两份。

## 已做（2026-11-14 约 19:45）

- `data/guilds/1317847113559965736/config.yaml`：`recap.on` 和 `topics.on` 改成 `false`，其他没动，热重载生效。改前备份为同目录的 `config.yaml.bak-20261114-recapfix`。
- 改完用 yaml 读过一遍：`chat/interject/read_links` 开着，`tools/news/recap/topics` 关着。

## Kurt 已定

1. **测试 bot（Kurt News / news-turtle）退役**：
   - Kurt 自己把它踢出頭腦警察。
   - 测试栈容器只 **stop，不删**：`docker compose -f docker-compose.staging.yml -p ddb-staging stop`。`restart: unless-stopped` 保证重启后不会自己起来。
   - **不要**在 Developer Portal 删应用或重置 token，否则恢复不了原样。
   - 恢复方法：用 HANDOFF 里的测试 bot 邀请链接拉回群，然后 `... -p ddb-staging start`（如果当初用的是 `down`，改成 `up -d`）。
   - 已完成：Kurt 已踢出；6 个 `-staging` 容器都是 Exited。日志也对上了：`guild 1317847113559965736: posted topics (744 chars)`。
2. **记忆挪回去（已做）**：先备份到 `data-backup-20261114-memmove\`，再把 `profile/`（六块）和 `recaps/` 从頭腦警察挪到 `1537280083830775820/`。頭腦警察从空白开始，`consent` 保持开，`quiet_channels` 清空，news/recap/topics 的 `channel` 改成 `null`，开关仍关。之后 Kurt 在頭腦警察用 `/setup` 选本群频道再开。`interests.md` 留在頭腦警察（写的是 Kurt 自己的兴趣）。

3. **频道归属检查：只加在 scheduler**（Kurt 定）。/setup 的频道下拉框只列本群频道，不用加。**已上线（2026-11-14）**：`Jobs.safe_post` 在 recap、topics、news 发帖前查 `GET /channels/<ID>` 的 `guild_id`。不对就不发，私信 owner（同一频道每个进程只私信一次），state、news-seen 和 digest 都不记。单测 352 个全过。`:local` 已重建，只重启了 scheduler（bot 容器没重启，代码相同）。拿真 token 查 FX 频道，返回的是 FX 的群 ID，结果正确。回滚用 `:v3.0`。
4. FX NEVER SLEEPS 的 topics（10:30）和 recap（22:00）**都留着**。
5. 测试栈的 DeepSeek key **先不换**（测试栈已停）。
6. `HorizonNewsMaterial` **已改成写 `data/shared/news`**（2026-11-14）。素材是公开新闻、不调模型、不分群，各群自己挑，不会串群。

## 还没讨论（按顺序，一条一条和 Kurt 过，定了再做）

（全部过完）

7. **已做**：HANDOFF 已更新；`chatbot-ops` 加了“加群 / 迁移数据”一节。

## 备注

- 这次排查是在 Claude App 里做的，连不到 Docker，没看容器日志。接手时可以先 `docker logs discord-deepseek-bot-scheduler --since 12h`，核对 09:00 那条 `guild 1317847113559965736: posted topics`。
