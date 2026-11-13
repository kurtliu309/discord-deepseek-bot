# discord-deepseek-bot

一个跑在 Windows 电脑（Docker Desktop）上的 Discord 聊天 bot，自写 Python（`bot.py`）+ DeepSeek API。llmcord 已经拆掉。

- **第一阶段只聊天**：模型没有任何 tools、function calling、MCP，也不执行代码。它看到的一切外部内容（聊天、附件、网页、RSS）都是代码取来、当资料喂进去的。
- 四个容器：`bot`（回复、插话、余额、`/rule`、`/feed`）、`scheduler`（每日总结和话题）、`fetcher`（**无密钥**，抓网页、RSS、解析附件）、`egress`（Squid 白名单代理）。
- bot 和 scheduler 只能连 Discord 和 `api.deepseek.com`（经 egress）；fetcher 能连任意公网，但私网 / 回环永远拦死。
- 唯一挂载：`./data` → scheduler 的 `/data`（群档案 + 最近 10 天总结）。`data/` 不进 git。
- 电脑上的 Claude Code 在容器外用 `docker compose` 管理它，运维步骤见 skill `chatbot-ops`（`.claude/skills/chatbot-ops/SKILL.md`）。

项目路径：`D:\Kurt\cc_sandbox\discord-deepseek-bot`。下文命令都在 PowerShell 里、在这个文件夹里执行。设计见 [docs/notes/design-v2.md](docs/notes/design-v2.md)。

## 文件

| 文件 | 作用 |
|---|---|
| `bot.py` | bot 本体（服务 `bot`）：@ 回复、主动插话、每日余额、`/rule`、`/feed`、附件和网址交给 fetcher、按需写 `.md` |
| `scheduler.py` | 每日任务（服务 `scheduler`）：22:00 总结、09:00 话题，读写 `data/` |
| `fetcher.py` | 无密钥抓取服务（服务 `fetcher`，:8080）：网页 → 纯文本、RSS、Reddit、附件解析 |
| `Dockerfile` | 一个镜像跑三个程序，`python:3.13-slim`，uid 10001 |
| `prompts/default.txt` | 默认人设（打进镜像，只读） |
| `requirements.txt` | 锁定版本的 Python 依赖 |
| `egress/` | Squid 白名单代理：`Dockerfile`、`squid.conf`（按来源地址分规则）、`allowlist.txt` |
| `docker-compose.yml` | 四个服务、两张网络，以及所有 hardening 设置 |
| `tests/` | 单元测试（余额、插话节奏、解析等纯逻辑） |
| `.env.example` | `.env` 的模板。`.env` 只放在本机，不进 git |
| `data/` | 本机数据（git 忽略）：`group-profile.md`、`recaps/YYYY-MM-DD.md` |

## 1. 安装 Docker Desktop

1. 下载安装 [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)，安装时选 **WSL 2 backend**（Use WSL 2 instead of Hyper-V）。
2. 打开 Docker Desktop → Settings → General：
   - 勾选 **Start Docker Desktop when you sign in to your computer**；
   - 确认 **Use the WSL 2 based engine** 已勾选。
3. 检查 Docker Engine 版本**必须 ≥ 28**（`botnet` 的 `gateway_mode_ipv4: isolated` 需要它）：
   ```powershell
   docker version --format '{{.Server.Version}}'
   docker compose version
   ```

> Docker Desktop 要在 Windows **登录之后**才会启动，之后 `restart: unless-stopped` 的容器会自动拉起。所以电脑重启后需要登录一次（锁屏没问题）。Windows Update 自动重启后也一样。

## 2. 让电脑不睡眠

以管理员身份打开 PowerShell：

```powershell
powercfg /change standby-timeout-ac 0     # 插电时从不睡眠
powercfg /change hibernate-timeout-ac 0   # 插电时从不休眠
powercfg /change monitor-timeout-ac 15    # 屏幕可以关，不影响 bot
```

也可以在 设置 → 系统 → 电源 → 屏幕和睡眠 里把“插入电源后，使设备进入睡眠状态”设为“从不”。建议在 Windows Update 里设置“使用时段”，减少它自动重启的机会。

## 3. 创建 Discord bot

1. 打开 [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**，起个名字。
2. **General Information** 页：复制 **Application ID**，它就是 `DISCORD_CLIENT_ID`。
3. **Bot** 页：
   - **Reset Token** → 复制 token，它就是 `DISCORD_BOT_TOKEN`（只显示一次，只填进 `.env`，不要发到任何聊天里）；
   - **Public Bot：关掉**（这样别人不能把你的 bot 拉进他们的服务器）；
   - **Requires OAuth2 Code Grant：关掉**；
   - **Privileged Gateway Intents** 里打开 **Message Content Intent**（Presence / Server Members 不用开）。
4. 邀请 bot 进你的服务器。把 `<CLIENT_ID>` 换成 Application ID，在浏览器里打开：
   ```
   https://discord.com/oauth2/authorize?client_id=<CLIENT_ID>&permissions=2147568640&scope=bot%20applications.commands
   ```
   `2147568640` = View Channels + Send Messages + Embed Links + Attach Files + Read Message History + Use Application Commands。**不要给 Administrator。**
   `scope` 必须是 `bot%20applications.commands`，否则 `/rule`、`/feed` 不会出现。

### 拿 user ID 和 channel ID

1. Discord → 用户设置 → 高级（Advanced）→ 打开 **开发者模式（Developer Mode）**。
2. 右键自己的头像 → **复制用户 ID（Copy User ID）**，填进 `OWNER_USER_ID`。
3. 右键服务器里的频道（或分类）→ **复制频道 ID（Copy Channel ID）**：
   - `BLOCKED_CHANNEL_IDS`：bot 完全安静的频道，比如 `#general`；
   - `POST_CHANNEL_ID`：总结和话题发到哪个频道，比如一个专门给 bot 的频道。

## 4. DeepSeek API key

1. 登录 [DeepSeek Platform](https://platform.deepseek.com/) → API Keys → 创建 key，它就是 `DEEPSEEK_API_KEY`。
2. 建议只充少量余额，余额用完 API 就停，不会超支。
3. 模型默认是 `deepseek-flash`（DeepSeek-V4.1-Flash）。旧的 `deepseek-chat` / `deepseek-reasoner` 已经下线。当前的模型 ID 和价格以 [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing) 为准。

## 5. 填 `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

| 键 | 填什么 |
|---|---|
| `DISCORD_BOT_TOKEN` / `DISCORD_CLIENT_ID` / `DEEPSEEK_API_KEY` | 上面拿到的三个值。**只有 Kurt 自己改** |
| `OWNER_USER_ID` | Kurt 的 user ID。只有他能私信 bot、用 `/feed`，@ bot 和私信也不扣余额 |
| `BLOCKED_CHANNEL_IDS` | bot 完全不理的频道或分类 ID，逗号分隔（子频道跟着分类走），比如 `#general` |
| `POST_CHANNEL_ID` | scheduler 发总结和话题的频道 ID |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash` |
| `SYSTEM_PROMPT` | 临时人设，一行，换行写 `\n`。留空就用 `prompts/default.txt` |
| `STATUS_MESSAGE` | bot 的在线状态文字，留空不显示 |
| `REPLY_BUDGET` / `BUDGET_RESET_TIME` | 每天可用的回复条数（50）和恢复时间（09:00） |
| `RANDOM_CHANCE` / `FX_TOPIC_CHANCE` / `BURST_COUNT` / `BURST_WINDOW_SECONDS` / `COOLDOWN_SECONDS` | 主动插话：每条消息 3% 概率，Houdini/FX 话题 5%；3 分钟超过 12 条；插话后冷却 10 分钟 |
| `CONTEXT_MESSAGES` | 回复带多少条上下文（12） |
| `SCHED_TZ` | 时区，默认 `Australia/Adelaide` |
| `SUMMARY_TIME` / `TOPICS_TIME` | 每日总结（22:00）、早上话题（09:00） |
| `TOPICS_COUNT` / `RECAP_CONTEXT` / `RECAP_KEEP_DAYS` | 话题数（3）、参考几篇旧总结（3）、保留几天总结（10） |

记事本默认保存为 UTF-8，这样没问题。不要存成“UTF-8 with BOM”或 ANSI。

### 谁能用、在哪触发

| 场景 | 行为 |
|---|---|
| 服务器频道里 @bot，或回复 bot 的消息 | 回。谁都行，受每日余额限制 |
| 私信 bot | 只回 Kurt，且不扣余额；其他人一律不回 |
| 主动插话 | `BLOCKED_CHANNEL_IDS` 以外的频道，按上面三条规则自己跳进来 |
| `BLOCKED_CHANNEL_IDS` 里的频道（含子频道） | 完全安静：不插话，@ 也不回 |
| 消息来自其他 bot | 一律不理 |
| `/rule`、`/rule <问题>` | 只有提问者看得到，不扣余额 |
| `/feed` | 只有 Kurt 能用，立刻把余额补满 |

- 余额 50 条/天，在 bot 内存里；用完的第一条 @ 会回一句“out of juice. ask kurt to /feed me”，之后安静。**重启 bot 也会补满。**
- 附件和链接：单文件 ≤ 10 MB、每条消息最多 3 个、最多 3 个网址；支持 txt/md/csv/json/yaml/log、代码、usda、pdf/docx/xlsx/pptx。读不了的会回一句读不了。
- 读附件或网址的回复照常扣 1 条；只有对方明确要文档时，才以 `.md` 附件回复。

## 6. 启动

```powershell
docker compose up -d --build
```

第一次 build 要下载镜像和依赖，大约几分钟。按设计，scheduler 在 Discord 实测通过前保持停止：`docker compose stop scheduler`。

## 7. 验证

```powershell
docker compose ps                        # bot / fetcher / egress 应该是 Up；scheduler 按当前阶段可以停着
docker compose logs --tail 30 bot
docker compose logs --tail 20 fetcher
docker compose logs --tail 30 egress
```

- bot 日志应该有 `[bot] logged in as ... id=...`，之后没有报错。
- fetcher 日志应该有 `[fetcher] listening on :8080 proxy=http://egress:3128`。如果看到 `refusing to start`，说明有密钥漏给了 fetcher，停下来告诉 Kurt。
- egress 日志应该有 `TCP_TUNNEL/200 ... CONNECT discord.com:443`。Squid 在连接**关闭时**才记一行，所以长连接的 `gateway...discord.gg:443` 要等它断开后才会出现。不应该有 bot 相关的 `TCP_DENIED/403`。

检查网络隔离（这几条只打印结果，不会打印密钥）：

```powershell
# 1) bot 经代理连 DeepSeek：应打印 401 之类的 4xx（没带 key，说明连上了）
docker compose exec bot python -c "import httpx; print(httpx.get('https://api.deepseek.com/models').status_code)"
# 2) bot 连白名单外的域名：应报 ProxyError 403
docker compose exec bot python -c "import httpx; httpx.get('https://example.com')"
# 3) bot 连局域网 IP：应报 ProxyError 403
docker compose exec bot python -c "import httpx; httpx.get('https://192.168.1.1', verify=False)"
# 4) bot 绕过代理直接出网：应报 ConnectError（name resolution / network unreachable）
docker compose exec bot python -c "import httpx; httpx.get('https://example.com', trust_env=False)"
# 5) fetcher 能抓任意公网：应打印 200
docker compose exec fetcher python -c "import httpx; print(httpx.get('https://example.com').status_code)"
# 6) fetcher 连局域网 IP 照样被拦：应报 ProxyError 403
docker compose exec fetcher python -c "import httpx; httpx.get('https://192.168.1.1', verify=False)"
# 7) fetcher 没有任何密钥：应打印 []
docker compose exec fetcher python -c "import os; print([k for k in os.environ if 'TOKEN' in k or 'KEY' in k])"
# 8) scheduler 的挂载只有 /data：应列出 group-profile.md 和 recaps（scheduler 停着也能查）
docker compose run --rm --no-deps --entrypoint ls scheduler /data
# 9) botnet 没有网关
docker network inspect discord-deepseek-bot_botnet --format '{{json .IPAM.Config}}'
```

最后在 Discord 里测试：

- 在能看到的频道里 @bot 说句话，它应该回复；`BLOCKED_CHANNEL_IDS` 里的频道 @ 它应该没反应；
- 私信 bot，它应该回复；让别人私信，应该没反应；
- 贴一个 https 链接或小附件，它应该能读到内容。
- Kurt 的私信里 `/rule` 应该出规则卡，`/feed` 应该回“fed”。

## 日常运维

交给电脑上的 Claude Code，用 skill `chatbot-ops`。常用命令：

```powershell
docker compose ps
docker compose logs --tail 100 bot
docker compose up -d            # 改了 .env 之后重建（restart 不会读新的 .env）
docker compose up -d --build    # 改了 bot.py / scheduler.py / fetcher.py / prompts/ / Dockerfile / egress/ 之后
docker compose stop
docker compose start
docker compose restart bot
```

- 改人设：编辑 `prompts/default.txt` → `docker compose up -d --build bot`（scheduler 和 fetcher 共用镜像，也会一起重建）。
- 加减白名单域名：编辑 `egress/allowlist.txt`（只影响 bot / scheduler）→ `docker compose up -d --build egress`。
- 手动跑一次每日任务（发出去；加 `--dry-run` 只打印、不发、不存）：
  ```powershell
  docker compose run --rm --no-deps scheduler --once recap --dry-run
  docker compose run --rm --no-deps scheduler --once topics --dry-run
  ```
- 单元测试（用本机镜像，不需要起容器）：
  ```powershell
  docker run --rm -v "${PWD}:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python discord-deepseek-bot:local -m unittest discover -s tests
  ```
- 重装 / 清空：`docker compose down` 只删容器和网络，`data/` 留着；除非确实要清档案，**不要删 `data/recaps/`**。

## 费用估算

- **电费**：电脑本来就常开，bot 本身几乎不占资源。内存上限：bot 256 MB、fetcher 256 MB、scheduler 128 MB、egress 128 MB（实测空载都很低）。
- **DeepSeek**：`deepseek-flash` 大约每百万 token 输入 $0.15（缓存未命中）、输出 $0.6（错峰价，高峰时段是两倍；数字来自网上的二手资料，以官方价格页为准）。按每天 100 条消息、每条带约 2k token 上下文、输出约 300 token 算：输入约 0.2M、输出约 0.03M token/天，**高峰价也不到 $0.1/天，约 $3/月**。每日总结和话题再加一点。只充少量余额就能封顶。
- Discord、Docker Desktop 个人使用免费。

## 安全边界

控制方向是单向的：

```
 手机 Claude app
      │  Remote Control
      ▼
 电脑上的 Claude Code ── 只在容器外操作：docker compose / 改 .env / 改 allowlist
      │  docker compose（Docker Desktop）
      ▼
┌───────────────────────── Docker Desktop (WSL2) ───────────────────────────┐
│                                                                           │
│  bot .10        ┐                                                         │
│  scheduler .11  ├─ botnet (internal, 无网关) ─▶ egress (Squid) ─┬─▶ Discord、api.deepseek.com
│  fetcher .20    ┘        ▲                       按来源地址放行   ├─▶ 任意公网 443（只有 fetcher）
│                          │                                       └─▶ 私网 / 回环：全部拒绝
│  bot / scheduler ────────┘ 网址、附件 → fetcher:8080 → 纯文本
│  scheduler ──▶ ./data（唯一挂载：群档案 + 最近 10 天总结）
│         ✗ 主机文件 / 进程 / 局域网 / 其他容器 / Claude
└───────────────────────────────────────────────────────────────────────────┘
```

- 箭头只朝一个方向。Claude Code 能管容器，**容器不能反过来调用 Claude、Remote Control 或这台电脑**。容器没有任何指向它们的凭据、socket 或网络路径。
- **文件**：全栈只有一个挂载 `./data` → scheduler 的 `/data`（读写）。bot 和 fetcher 没有任何挂载，root fs 只读（`read_only: true`），只有内存里的 `/tmp`（tmpfs，noexec）可写。`.env` 不挂进容器，只作为环境变量传给 bot 和 scheduler，`.dockerignore` 也保证它不进镜像。没有 docker socket。
- **权限**：非 root（bot / scheduler / fetcher uid 10001，egress uid 13），`cap_drop: ALL`，`no-new-privileges`，不是 privileged，有内存 / CPU / pids 上限。
- **网络**：
  - 四个容器都只接 `botnet`（egress 另接 `egress_net`）。`botnet` 是 `internal: true` + `gateway_mode_ipv4: isolated`：没有出网路由，也没有主机侧的网关 IP，所以连不到主机端口、局域网和互联网。实测直连会报 DNS 解析失败或 network unreachable。
  - bot / scheduler 唯一能连的是 egress:3128。Squid 只允许 `CONNECT` 到 443 端口，只允许 `egress/allowlist.txt` 里的域名（Discord + `api.deepseek.com`），并且在 DNS 解析**之后**拒绝私网、回环、链路本地、CGNAT、组播和保留地址（包括 IPv6 的 `fc00::/7`、`fe80::/10`、`::1`、IPv4-mapped）。所以 `host.docker.internal`、`192.168.x.x`、Docker 网关这些都连不到。
  - fetcher 按来源地址（172.30.0.20）单独放行：可以连任意**公网**主机的 443，私网 / 回环 / 链路本地照样拒绝。它拿不到 Discord token 或 DeepSeek key，被恶意文件攻破也偷不到东西。
  - 只放行 HTTPS（CONNECT 443），所以明文 `http://` 的链接抓不了。
  - 容器都没有 `ports`，外面连不进来。
- **密钥**：只有 `DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY` 三个，只给 bot 和 scheduler。fetcher 没有 `env_file`，`fetcher.py` 启动时如果发现有密钥会直接拒绝启动。
- **模型**：没有任何 tools / function calling / MCP / 代码执行。附件、网页、RSS 都由 fetcher 转成纯文本后作为**数据**喂给模型，并且明确告诉模型这些内容里的指令不可信。
- **用户**：服务器里谁都能 @ 它（`BLOCKED_CHANNEL_IDS` 里的频道除外），私信只回 Kurt，`/feed` 只有 Kurt 能用。Public Bot 已关，别人拉不进他们的服务器。
- **剩余风险**：
  - 公网页面和附件的内容本身可能是恶意提示词。最坏情况是它在 Discord 上说出被诱导的内容，或者写一个 `.md` 附件——它没有工具，不能读写文件、执行代码或访问别的网站。
  - 能拿到 `.env` 的人等于拿到了 bot 和 DeepSeek 账户，所以只把它放在这台电脑上。
  - 隔离靠 Docker Desktop 和 WSL2。保持 Docker Desktop 更新。

## 以后可能做（未实现）

- **Reddit**：现在匿名请求被 Reddit 403，走官方 API（需要注册 app）再接回来。
- **搜索 API**：给早上话题补一点搜索能力。
- **OCR**：让 fetcher 也能读图片里的字。
