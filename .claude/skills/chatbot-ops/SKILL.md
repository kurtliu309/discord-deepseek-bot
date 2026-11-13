---
name: chatbot-ops
description: 运维本机 Docker Desktop 上的 Discord DeepSeek 聊天机器人（llmcord 容器）：查状态、看日志、启停重启、改模型/系统提示词/允许的用户或频道、升级 llmcord 版本并重建。在这台 Windows 电脑上对这个 bot 做任何操作之前先加载。
---

# chatbot-ops

这个 bot 是一个跑在 Docker Desktop 里的 llmcord 容器（compose 服务名 `bot`，容器名 `discord-deepseek-bot`）。
你（Claude Code）在**容器外面**用 `docker compose` 管理它；容器里的 bot 碰不到你，也碰不到这台电脑。

- 所有命令都是 PowerShell，并且**在项目文件夹里执行**（含 `docker-compose.yml` 的那个文件夹）。先 `Set-Location <项目文件夹>`。
- 下文路径都相对项目文件夹：`.env`、`Dockerfile`、`docker-compose.yml`、`config.template.yaml`、`entrypoint.py`。
- 执行前确认 Docker Desktop 在跑：`docker version --format '{{.Server.Version}}'`。报错就让 Kurt 打开 Docker Desktop，不要自己改 Docker 设置。

## 硬规则（违反任何一条都不要做，先问 Kurt）

1. **不加** `volumes`、bind mount、`ports`、`privileged`、`cap_add`、`network_mode: host`、`pid: host`、`ipc: host`、`devices`，**不挂** `/var/run/docker.sock` 或 `//./pipe/docker_engine`。不删 `read_only`、`cap_drop`、`no-new-privileges`、`user`、资源限制。
2. **不打印密钥**。不要 `Get-Content .env`、`type .env`、`cat .env`，不要跑不带 `--quiet` 的 `docker compose config`，不要跑不带 `--format` 的 `docker inspect`，也不要 `docker exec ... env`。这些都会把 `DISCORD_BOT_TOKEN` / `DEEPSEEK_API_KEY` 打到输出里。
3. **不改密钥行**（`DISCORD_BOT_TOKEN`、`DISCORD_CLIENT_ID`、`DEEPSEEK_API_KEY`）。需要换密钥时请 Kurt 自己用记事本改 `.env`。
4. **第一阶段只聊天**：不给模型加 `tools` / `tool_choice` / function calling / MCP / 代码执行，不加别的 provider。`entrypoint.py` 里有检查，不要删掉它。
5. 不 `git push`，不把 `.env` 加进 git。
6. 不用 `ALLOW_PUBLIC=true`，除非 Kurt 明确要求开放给所有人。

## 查状态

```powershell
docker compose ps
docker stats --no-stream discord-deepseek-bot
```

`STATUS` 应该是 `Up ...`。如果是 `Restarting`，看日志找 `[entrypoint] config error`。

只看 `.env` 里有哪些键、非密钥的值是什么（密钥只显示 set/empty）：

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([A-Z_]+)\s*=(.*)$') {
    $k = $matches[1]; $v = $matches[2]
    if ($k -match 'TOKEN|KEY|CLIENT_ID') { $v = $(if ($v.Trim()) { '<set>' } else { '<empty>' }) }
    "$k = $v"
  }
}
```

## 看日志

```powershell
docker compose logs --tail 100 bot
docker compose logs --tail 50 -f bot      # 跟随，Ctrl+C 退出
```

正常启动会看到 `[entrypoint] config.yaml rendered: model=... allowed_users=N ...`，然后 `logging in using static token`、`Shard ID None has connected to Gateway`。
日志里会出现 llmcord 打印的 `BOT INVITE URL`（权限值 412317191168），忽略它，用 README 里的最小权限邀请链接。
日志含用户消息原文，转述给 Kurt 时只给必要部分。

## 启动 / 停止 / 重启

```powershell
docker compose up -d          # 启动（或按当前 .env 重建）
docker compose stop           # 停止（保留容器）
docker compose start          # 停止后再启动
docker compose restart bot    # 重启进程；注意：不会重新读取 .env
docker compose down           # 删除容器和网络（镜像保留）
```

## 改模型 / 系统提示词 / 允许的用户或频道

改的都是 `.env` 里的非密钥键，然后**重建容器**（`restart` 不会读新的 `.env`）。

用下面的函数改单个键，它不打印文件内容，并写成无 BOM 的 UTF-8（Windows PowerShell 5.1 的 `Set-Content -Encoding utf8` 会加 BOM，导致第一行的键读错）：

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

Set-EnvKey DEEPSEEK_MODEL 'deepseek-flash'
Set-EnvKey ALLOWED_USER_IDS '111111111111111111,222222222222222222'
Set-EnvKey ALLOWED_CHANNEL_IDS '333333333333333333'
Set-EnvKey SYSTEM_PROMPT '"你是一个简洁的助手。\n今天是 {date}。"'
docker compose up -d
docker compose logs --tail 20 bot
```

键的含义：

| 键 | 说明 |
|---|---|
| `DEEPSEEK_MODEL` | DeepSeek 模型 ID，默认 `deepseek-flash`。改之前查 https://api-docs.deepseek.com/quick_start/pricing 确认 ID 存在 |
| `SYSTEM_PROMPT` | 一行；多行用双引号包起来并写 `\n`。支持 `{date}` `{time}`。留空用默认中文提示词 |
| `ALLOWED_USER_IDS` | 逗号分隔的用户 ID。空 = 不按用户限制 |
| `ALLOWED_CHANNEL_IDS` | 逗号分隔的频道 / 分类 ID（子线程按父频道算）。空 = 不按频道限制。不影响私信 |
| `ADMIN_USER_IDS` | 管理员：可用 `/model`，总能通过用户检查，`ALLOW_DMS=false` 时也能私信 |
| `ALLOW_DMS` | 默认 `false`。设 `true` 时必须同时设 `ALLOWED_USER_IDS` |

两个 `ALLOWED_*` 都空时容器拒绝启动（llmcord 的语义是"空 = 所有人"，这里改成默认拒绝）。

改完检查：日志里 `allowed_users=` / `allowed_channels=` 的数量对得上，且没有 `config error`。

## 升级 llmcord 版本并重建

llmcord 固定在 `Dockerfile` 的两个 ARG：`LLMCORD_COMMIT`（commit SHA）和 `LLMCORD_SHA256`（`llmcord.py` 的 sha256）。

1. 在 https://github.com/jakobdylanc/llmcord/commits/main 选一个新 commit，记下完整 SHA。
2. 看这期间的改动：`config.yaml`（配置结构）和 `requirements.txt`（依赖）有没有变。配置结构变了要同步改 `config.template.yaml` 和 `entrypoint.py`；新出现的 tool / MCP 功能第一阶段不启用。
3. 算新文件的 sha256：
   ```powershell
   $sha = '<新 commit SHA>'
   $tmp = Join-Path $env:TEMP 'llmcord.py'
   Invoke-WebRequest "https://raw.githubusercontent.com/jakobdylanc/llmcord/$sha/llmcord.py" -OutFile $tmp
   (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
   ```
4. 把两个值写进 `Dockerfile` 的 ARG，需要的话更新 `requirements.txt` 的版本，再更新 `config.template.yaml` 第一行注释里的 SHA。
5. 重建并验证：
   ```powershell
   docker compose build --pull
   docker compose up -d
   docker compose logs --tail 30 bot
   ```
   sha256 不对时 build 会失败并打印 `llmcord.py sha256 mismatch`，这时回去核对，不要删掉校验。
6. 出问题回滚：把 ARG 改回旧值，再 `docker compose build` + `docker compose up -d`。

## 常见问题

- `Restarting` 循环：`docker compose logs --tail 30 bot` 找 `[entrypoint] config error`，按提示改 `.env`。
- 登录失败 `Improper token`：token 错了，请 Kurt 去 Developer Portal 重置 token 并自己改 `.env`。
- 能上线但不回复：确认在频道里 @ 了 bot；确认发消息的人 / 频道在允许列表里；确认 Developer Portal 开了 Message Content Intent。
- 回复是空白的：把 `USE_PLAIN_RESPONSES` 设为 `true`，或请 Kurt 给 bot 加 Embed Links 权限。
- DeepSeek 报 401 / 402：key 无效或余额不足，告诉 Kurt，不要自己改 key。
