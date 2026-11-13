---
name: chatbot-ops
description: 运维本机 Docker Desktop 上的 Discord DeepSeek 聊天 bot v2（自写 bot + scheduler 每日任务 + 无密钥 fetcher + Squid egress）：查状态、看日志、启停、改人设和 .env 设置、余额、每日总结和话题、抓取与文件读取、出网白名单、排查不回复。在这台 Windows 电脑上对这个 bot 做任何操作之前先加载。
---

# chatbot-ops（v2）

项目文件夹 `D:\Kurt\cc_sandbox\discord-deepseek-bot`。所有命令都是 PowerShell，先 `Set-Location` 到这里。设计见 `docs/notes/design-v2.md`，能调的开关见 `docs/notes/control-panel.md`。

| compose 服务 | 容器名 | 作用 | 出网 | 挂载 |
|---|---|---|---|---|
| `bot` | `discord-deepseek-bot` | `bot.py`：@ 回复、主动插话、余额、`/rule` `/feed`、附件/网址交给 fetcher、发 `.md` 文档 | 只 Discord + `api.deepseek.com` | 无 |
| `scheduler` | `discord-deepseek-bot-scheduler` | `scheduler.py`：22:00 总结、09:00 话题、群档案 | 同 bot | `./data` → `/data` |
| `fetcher` | `discord-deepseek-bot-fetcher` | `fetcher.py`：抓网页 / RSS / Reddit，解析附件。**无密钥** | 任意公网 443 | 无 |
| `egress` | `discord-deepseek-bot-egress` | Squid，按来源地址分规则（bot .10、scheduler .11、fetcher .20） | — | 无 |

先确认 Docker 在跑：`docker version --format '{{.Server.Version}}'`（Engine ≥ 28）。报错就让 Kurt 打开 Docker Desktop，不要改 Docker 设置。

## 硬规则（违反任何一条都不要做，先问 Kurt）

1. bot 和 scheduler 只接 `botnet`，只经 egress 出网，只能到 `egress/allowlist.txt`。不删 `botnet` 的 `internal: true`、`gateway_mode_ipv4: isolated`，不改固定地址（squid 规则依赖它）。
2. fetcher 永远不给 `env_file`、不给任何密钥。
3. 全栈唯一的挂载是 `./data` → scheduler。不加别的 volume / bind mount、`ports`、`privileged`、`cap_add`、host 网络，不挂 docker socket；不删 `read_only`、`cap_drop`、`no-new-privileges`、非 root、资源上限。
4. 不打印密钥：不 `Get-Content .env`，不跑不带 `--quiet` 的 `docker compose config`，不跑不带 `--format` 的 `docker inspect`，不 `docker exec ... env`。
5. 不改密钥行（`DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY`），请 Kurt 自己用记事本改。
6. 不给模型加 tools / function calling / MCP / 代码执行。外部内容只由代码取来当数据传。
7. 给 bot / scheduler 加白名单域名、改人设底线段落、加挂载：先问 Kurt。
8. 不伪装 User-Agent 去绕过网站的反爬（Reddit 现在 403 就是这样，正规办法是官方 API）。

## 查状态、看日志

```powershell
docker compose ps
docker stats --no-stream discord-deepseek-bot discord-deepseek-bot-scheduler discord-deepseek-bot-fetcher discord-deepseek-bot-egress
docker compose logs --tail 50 bot          # [bot] logged in as ... 之后无报错
docker compose logs --tail 50 scheduler    # [scheduler] next: recap at ...
docker compose logs --tail 50 fetcher
docker compose logs --tail 50 egress       # TCP_TUNNEL/200 放行，TCP_DENIED/403 拒绝
```

日志可能含用户消息原文和 ID，转述给 Kurt 时只给必要部分。

只看 `.env` 有哪些键（密钥只显示 set/empty）：

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([A-Z_]+)\s*=(.*)$') {
    $k = $matches[1]; $v = $matches[2]
    if ($k -match 'TOKEN|KEY|CLIENT_ID') { $v = $(if ($v.Trim()) { '<set>' } else { '<empty>' }) }
    "$k = $v"
  }
}
```

## 启动 / 停止

```powershell
docker compose up -d                 # 全部启动，或按新 .env 重建
docker compose build bot; docker compose up -d   # 改了 *.py、prompts/、Dockerfile 之后（scheduler 和 fetcher 复用 bot 的镜像，`up -d --build fetcher` 不会重建它）
docker compose up -d --build egress  # 改了 egress/ 之后
docker compose stop scheduler        # 只停每日任务
docker compose down                  # 删容器和网络（data\ 保留）
```

`docker compose restart` 不会重新读 `.env`，改了 `.env` 一定要 `up -d`。改了 `docker-compose.yml` 的网络段要先 `down` 再 `up -d`。

## 改 `.env` 里的非密钥设置

用这个函数改单个键（不打印文件，写成无 BOM 的 UTF-8），然后 `docker compose up -d`：

```powershell
function Set-EnvKey([string]$Key, [string]$Value) {
  if ($Key -match 'TOKEN|KEY|CLIENT_ID') { throw "密钥行请 Kurt 自己改" }
  $path = (Resolve-Path .env).Path
  $lines = [IO.File]::ReadAllLines($path)
  $found = $false
  $lines = $lines | ForEach-Object { if ($_ -match "^\s*$Key\s*=") { $found = $true; "$Key=$Value" } else { $_ } }
  if (-not $found) { $lines += "$Key=$Value" }
  [IO.File]::WriteAllLines($path, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
}
```

| 键 | 说明 |
|---|---|
| `OWNER_USER_ID` | Kurt：唯一能私信、能 `/feed`、不扣余额 |
| `BLOCKED_CHANNEL_IDS` | 完全静默的频道（现在是 `#general`），逗号分隔 |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash` |
| `SYSTEM_PROMPT` | 临时人设（一行，`\n` 换行）；空 = `prompts/default.txt` |
| `REPLY_BUDGET` / `BUDGET_RESET_TIME` | 每天 50 条 / 09:00 恢复 |
| `RANDOM_CHANCE` / `BURST_COUNT` / `BURST_WINDOW_SECONDS` / `COOLDOWN_SECONDS` | 插话：3% / 12 条 / 180 秒 / 冷却 600 秒 |
| `CONTEXT_MESSAGES` | 每次带的上下文条数，12 |
| `POST_CHANNEL_ID` | 总结和话题发到哪个频道 |
| `SCHED_TZ` / `SUMMARY_TIME` / `TOPICS_TIME` | Australia/Adelaide / 22:00 / 09:00 |
| `TOPICS_COUNT` / `RECAP_CONTEXT` / `RECAP_KEEP_DAYS` | 3 / 3 篇 / 10 天 |

改完看 `docker compose logs --tail 20 bot`（或 scheduler）有没有 `config error`。

## `/rule` 的知识库

`/rule <问题>` 和 `/dev` 回答时会读 `prompts/about.md`（英文完整说明：更新日志、规则、机制、能输出的格式、架构、设计原因）。`about.md` 里不能出现 ID 或密钥（有单元测试检查）。

**每次改功能或数字，都按这个顺序做（固定规则）：**
1. 改代码，跑单元测试。
2. 在 `about.md` 最上面的 `## Changelog` 加一条新版本（版本号 +0.1，写日期和要点，最新的放最前），同时改正文里受影响的说明。
3. 在说明页源文件的 “What's new” 公告里换成新版本，旧版本挪进 “Earlier versions”，并改正文；再重新生成 `docs/index.html`，让两份内容一致。
4. `docker compose build bot; docker compose up -d`，然后推送到 `main`（GitHub Pages 原地址会自动更新），并重新发布 Artifact。

## 人设

改 `prompts/default.txt`（保留最后的底线段落），然后 `docker compose up -d --build bot scheduler`。

## 余额

- 频道里的 @ 回复、插话、读文件/网址的回复共用；Kurt 和 `/rule` 不扣。
- Kurt 在 Discord 里 `/feed` 立刻补满；或者 `docker compose restart bot`（余额在内存里，重启即满）。

## 每日任务

- 手动跑一次：`docker compose run --rm --no-deps scheduler --once recap`（或 `topics`）；加 `--dry-run` 只打印，不发、不存。
- 本地记忆在 `data\`：`group-profile.md`（顶部 Kurt 区 bot 不改）、`recaps\YYYY-MM-DD.md`（只留 10 天）。不要删，删了它就忘了群的方向。
- 话题素材提前抓：08:00 全抓、08:30 补抓空的源，存 `data\material.json`，09:00 发完即删；没缓存就 09:00 当场抓。手动：`--once prefetch` / `--once refetch`。
- Reddit 走公开 RSS，两组合并订阅（`reddit-weird`、`reddit`），同站请求间隔 5 秒、429 按 Retry-After 等一次。
- 错过的时间点不补。
- RSS 源列表在 `scheduler.py` 的 `FEEDS`、`WEIRD_SUBS`、`OTHER_SUBS`；改完 `docker compose up -d --build scheduler`。
- 写法（几条要点、语气）在 `scheduler.py` 的 `recap()` / `topics()` 提示词里。

## 抓取和文件（fetcher）

- 读：`txt md csv json yaml log`、代码、`usda`、`pdf docx xlsx pptx`；单文件 ≤ 10 MB，每条消息 ≤ 3 个；文字单文件约 3 万字、合计约 6 万字。
- 网址：只支持 `https://`（Squid 只放行 CONNECT 443）；GitHub `blob` 链接自动转 raw。
- 不读：`hip hda bgeo`、图片、压缩包、音视频。

## 出网白名单

`egress/allowlist.txt` 只管 bot 和 scheduler（fetcher 本来就能上公网）。先问 Kurt，再改，然后 `docker compose up -d --build egress`，看 egress 日志确认。不要改 `squid.conf` 里的私网拒绝和 CONNECT 443 规则。

## 单元测试

```powershell
docker run --rm -v "${PWD}:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python discord-deepseek-bot:local -m unittest discover -s tests
```

## 常见问题

- **bot 不回**：是不是在 `BLOCKED_CHANNEL_IDS` 的频道；余额是不是用完（`/rule` 能看到剩余）；@ 的是不是 bot 本人（不是同名身份组）；私信只回 Kurt。
- **模型回空**：日志里 `finish_reason=length` 就是 `max_tokens` 太小（模型会先思考），调高对应调用的上限。
- **发 `.md` 失败**：bot 身份组缺“附加文件”权限。
- **斜杠命令不出现**：邀请时没带 `applications.commands`；用 README 里的邀请链接重新邀请。
- **Reddit 0 条**：Reddit 拦了匿名请求（403）。不要伪装 UA，等 Kurt 决定用官方 API 还是换源。
- **egress 被 OOM 杀**：`squid.conf` 里必须有 `max_filedescriptors 1024`。
- **DeepSeek 401 / 402**：key 无效或余额不足，告诉 Kurt，不要自己改 key。
