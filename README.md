# discord-deepseek-bot

一个跑在 Windows 电脑（Docker Desktop）上的 Discord 聊天 bot：[llmcord](https://github.com/jakobdylanc/llmcord) + DeepSeek API。

- **第一阶段只聊天**：没有 tools、function calling、MCP，也不执行代码。
- bot 关在一个只读、非 root、无挂载、无端口的容器里。它**唯一**的出网路径是一个 Squid 白名单代理（egress），只能连 Discord 和 `api.deepseek.com`。
- 电脑上的 Claude Code 在容器外用 `docker compose` 管理它，运维步骤见 skill `chatbot-ops`（`.claude/skills/chatbot-ops/SKILL.md`）。

项目路径：`D:\Kurt\cc_sandbox\discord-deepseek-bot`。下文命令都在 PowerShell 里、在这个文件夹里执行。

## 文件

| 文件 | 作用 |
|---|---|
| `Dockerfile` | bot 镜像：`python:3.13-slim`，uid 10001。llmcord 固定在 commit `a99de6da1639f36972e6f57a4331e8d8b63b74bb`，并校验 sha256 |
| `entrypoint.py` | 启动时用 `.env` 和 `config.template.yaml` 生成 `config.yaml`（放在 tmpfs 里），给 discord.py 设上代理，再运行 llmcord |
| `config.template.yaml` | llmcord 配置模板（不含密钥） |
| `prompts/default.txt` | 默认人设（打进镜像，只读） |
| `requirements.txt` | 锁定版本的 Python 依赖 |
| `egress/` | Squid 白名单代理：`Dockerfile`（镜像按 digest 固定）、`squid.conf`、`allowlist.txt` |
| `docker-compose.yml` | 两个容器、两张网络，以及所有 hardening 设置 |
| `.env.example` | `.env` 的模板。`.env` 只放在本机，不进 git |

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
   https://discord.com/oauth2/authorize?client_id=<CLIENT_ID>&permissions=84992&scope=bot
   ```
   `84992` = View Channels + Send Messages + Embed Links + Read Message History。**不要给 Administrator。**
   如果要在 thread 里用，换成 `permissions=274877991936`（额外加 Send Messages in Threads）。
   bot 启动时 llmcord 也会在日志里打印一个邀请链接（`permissions=412317191168`），忽略它，用上面这个。

### 拿 user ID 和 channel ID

1. Discord → 用户设置 → 高级（Advanced）→ 打开 **开发者模式（Developer Mode）**。
2. 右键自己或朋友的头像 → **复制用户 ID（Copy User ID）**，填进 `ALLOWED_USER_IDS`。
3. 右键服务器里的频道（或分类）→ **复制频道 ID（Copy Channel ID）**，填进 `ALLOWED_CHANNEL_IDS`。

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
| `DISCORD_BOT_TOKEN` / `DISCORD_CLIENT_ID` / `DEEPSEEK_API_KEY` | 上面拿到的三个值 |
| `ALLOWED_USER_IDS` | 你和朋友的 user ID，用逗号分隔 |
| `ALLOWED_CHANNEL_IDS` | 允许 bot 回复的频道或分类 ID，用逗号分隔 |
| `ALLOW_DMS` | 默认 `true`：白名单里的人可以私信 bot |
| `ADMIN_USER_IDS` | 留空。留空时没人能用 `/model` 切模型 |
| `SYSTEM_PROMPT` | 留空就用 `prompts/default.txt` 的人设 |

记事本默认保存为 UTF-8，这样没问题。不要存成“UTF-8 with BOM”或 ANSI。

### 权限规则

llmcord 的规则是“`allowed_ids` 为空 = 这一类不限制”。下表按 llmcord 的代码整理：

| 场景 | 谁能用 |
|---|---|
| 服务器频道里 @bot | 用户在 `ALLOWED_USER_IDS` 里（为空则不限用户），**并且**频道在 `ALLOWED_CHANNEL_IDS` 里（为空则不限频道；thread 按父频道算，也可以填分类 ID） |
| 私信 bot | 要 `ALLOW_DMS=true`，并且用户在 `ALLOWED_USER_IDS` 里。频道规则不管私信 |
| 两个 `ALLOWED_*` 都为空 | **容器拒绝启动**（否则所有人都能用）。除非设 `ALLOW_PUBLIC=true` |
| `ALLOW_DMS=true` 但 `ALLOWED_USER_IDS` 为空 | **容器拒绝启动**（否则任何 Discord 用户都能私信）。除非设 `ALLOW_PUBLIC=true` |
| `/model` | 只有 admin 能切换；`ADMIN_USER_IDS` 为空，所以没人能切 |

在频道里要 @bot 才会触发，私信不用 @。回复 bot 的消息会接着之前的对话（最多带 12 条，`max_messages: 12`）。

## 6. 启动

```powershell
docker compose up -d --build
```

第一次 build 要下载镜像和依赖，大约几分钟。

## 7. 验证

```powershell
docker compose ps                        # bot 和 egress 都应该是 Up
docker compose logs --tail 30 bot
docker compose logs --tail 30 egress
```

- bot 日志应该有 `[entrypoint] config.yaml rendered: model=deepseek/deepseek-flash allowed_users=N ... proxy=http://egress:3128`，然后是 `logging in using static token`，之后没有报错。
- egress 日志应该有 `TCP_TUNNEL/200 ... CONNECT discord.com:443`。Squid 在连接**关闭时**才记一行，所以长连接的 `gateway...discord.gg:443` 要等它断开后才会出现。不应该有 bot 相关的 `TCP_DENIED/403`。

检查网络隔离（这几条只打印结果，不会打印密钥）：

```powershell
# 1) 经代理连 DeepSeek：应打印 401 之类的 4xx（没带 key，说明连上了）
docker compose exec bot python -c "import httpx; print(httpx.get('https://api.deepseek.com/models').status_code)"
# 2) 白名单外的域名：应报 ProxyError 403
docker compose exec bot python -c "import httpx; httpx.get('https://example.com')"
# 3) 局域网 IP：应报 ProxyError 403
docker compose exec bot python -c "import httpx; httpx.get('https://192.168.1.1', verify=False)"
# 4) 绕过代理直接出网：应报 ConnectError（name resolution / network unreachable）
docker compose exec bot python -c "import httpx; httpx.get('https://example.com', trust_env=False)"
# 5) botnet 没有网关
docker network inspect discord-deepseek-bot_botnet --format '{{json .IPAM.Config}}'
```

最后在 Discord 里测试：

- 在允许的频道里 @bot 说句话，它应该回复；
- 私信 bot，它应该回复；
- 让不在白名单里的人 @bot，它不回复。

## 日常运维

交给电脑上的 Claude Code，用 skill `chatbot-ops`。常用命令：

```powershell
docker compose ps
docker compose logs --tail 100 bot
docker compose up -d            # 改了 .env 之后重建（restart 不会读新的 .env）
docker compose up -d --build    # 改了 prompts/、Dockerfile 或 egress/ 之后
docker compose stop
docker compose start
docker compose restart bot
```

- 改人设：编辑 `prompts/default.txt` → `docker compose up -d --build bot`。
- 加减白名单域名：编辑 `egress/allowlist.txt` → `docker compose up -d --build egress`。
- 升级 llmcord：改 `Dockerfile` 里的 `LLMCORD_COMMIT` 和 `LLMCORD_SHA256`，详细步骤见 skill。

## 费用估算

- **电费**：电脑本来就常开，bot 本身几乎不占资源。bot 内存上限 256 MB（实测空载约 55 MB），egress 上限 128 MB（约 15 MB）。
- **DeepSeek**：`deepseek-flash` 大约每百万 token 输入 $0.15（缓存未命中）、输出 $0.6（错峰价，高峰时段是两倍；数字来自网上的二手资料，以官方价格页为准）。按每天 100 条消息、每条带约 2k token 上下文、输出约 300 token 算：输入约 0.2M、输出约 0.03M token/天，**高峰价也不到 $0.1/天，约 $3/月**。只充少量余额就能封顶。
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
┌──────────────────────── Docker Desktop (WSL2) ────────────────────────┐
│                                                                        │
│  ┌──────────────┐  botnet (internal, 无网关)  ┌───────────────────┐   │
│  │ bot (llmcord)│ ─────── HTTPS_PROXY ──────▶ │ egress (Squid)    │───┼──▶ discord.com / *.discord.gg
│  │ uid 10001    │                             │ 白名单 + 只 443   │   │    cdn.discordapp.com
│  │ 只读、无挂载 │                             │ 拒绝私网 IP       │───┼──▶ api.deepseek.com
│  └──────────────┘                             └───────────────────┘   │
│         ✗ 主机文件 / 进程 / 局域网 / 其他容器 / Claude                 │
└────────────────────────────────────────────────────────────────────────┘
```

- 箭头只朝一个方向。Claude Code 能管容器，**容器不能反过来调用 Claude、Remote Control 或这台电脑**。bot 没有任何指向它们的凭据、socket 或网络路径。
- **文件**：没有 volume 和 bind mount，root fs 只读（`read_only: true`），只有内存里的 `/tmp`（tmpfs，noexec）可写。`.env` 不挂进容器，只作为环境变量传进去，`.dockerignore` 也保证它不进镜像。没有 docker socket。
- **权限**：非 root（bot uid 10001，egress uid 13），`cap_drop: ALL`，`no-new-privileges`，不是 privileged，有内存 / CPU / pids 上限。
- **网络**：
  - bot 只接 `botnet`。这张网络是 `internal: true` + `gateway_mode_ipv4: isolated`：没有出网路由，也没有主机侧的网关 IP，所以连不到主机端口、局域网和互联网。实测直连会报 DNS 解析失败或 network unreachable。
  - bot 唯一能连的是 egress:3128。Squid 只允许 `CONNECT` 到 443 端口，只允许 `egress/allowlist.txt` 里的域名，并且在 DNS 解析**之后**拒绝私网、回环、链路本地、CGNAT、组播和保留地址（包括 IPv6 的 `fc00::/7`、`fe80::/10`、`::1`、IPv4-mapped）。所以 `host.docker.internal`、`192.168.x.x`、Docker 网关这些都连不到。
  - 两个容器都没有 `ports`，外面连不进来。
- **密钥**：只有 `DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY` 三个。生成的 `config.yaml` 里只写 `*_env` 引用，不写值，llmcord 运行时自己从环境变量读。
- **用户**：只有白名单里的用户和频道能触发；没有 admin，所以没人能切模型。Public Bot 已关，别人拉不进他们的服务器。
- **剩余风险**：
  - 白名单里的域名本身是公网服务。如果 bot 被诱导，最坏情况是它在 Discord 上发出 DeepSeek 生成的内容。第一阶段 bot 没有工具，不能读写文件、执行代码或访问别的网站。
  - 能拿到 `.env` 的人等于拿到了 bot 和 DeepSeek 账户，所以只把它放在这台电脑上。
  - 隔离靠 Docker Desktop 和 WSL2。保持 Docker Desktop 更新。

## 第二阶段预告（未实现）

计划在 bot 旁边加一个**没有任何密钥**的 sandbox 容器，用来跑工具（比如执行代码、查资料）：

- 同样的 hardening（只读、非 root、cap_drop ALL、无挂载、无端口），也只接 `botnet`，也只经过同一个 egress 出网；
- egress 白名单另加 PyPI（`pypi.org`、`files.pythonhosted.org`）、npm（`registry.npmjs.org`）、GitHub（`github.com`、`codeload.github.com`、`raw.githubusercontent.com`、`objects.githubusercontent.com`）、Wikipedia（`.wikipedia.org`）；最好按容器分开白名单，让 bot 自己仍然只能连 Discord 和 DeepSeek；
- bot 只把任务交给 sandbox 并拿回结果，sandbox 拿不到 Discord token 或 DeepSeek key。
