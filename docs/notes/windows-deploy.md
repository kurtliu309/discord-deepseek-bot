---
name: windows-deploy
description: 这台 Windows 电脑上的部署方式、当前状态，以及首次部署踩过的坑（WSL 没装、Squid 内存超限、@ 选成身份组）
status: 有效
updated: 2026-11-13
---

# 在 Kurt 电脑上的部署

结论：2026-11-13 已在 Kurt 的 Windows 11 电脑上部署成功，隔离自检全部通过，bot 名 `deepseekbot-kurt`，在服务器 `FX NEVER SLEEPS` 的 `#deepseek-go-cray` 频道。完整部署步骤和安全边界以根目录 `README.md` 为准，这里只记这台电脑的情况和坑。

## 部署方式（一句话版）

Docker Desktop（WSL2 后端）里跑两个容器：`bot`（llmcord）只接内部网络 `botnet`，唯一出口是 `egress`（Squid 白名单代理）。Claude Code 在容器外用 `docker compose` 管理，控制方向单向：手机 → 电脑上的 Claude → `docker compose` → 容器。

## 这台电脑的状态

- Docker Desktop 用 winget 装；Docker Engine 29.9.3（要求 ≥ 28）。
- WSL 3.0.5，用 `wsl --install --no-distribution`（管理员）装的。
- 插电不睡眠、不休眠（`powercfg`）。
- Docker Desktop“登录时启动”要在 Settings → General 打开；电脑重启后需要登录一次，容器才会自动拉起。

## 坑

1. **装了 Docker Desktop 但 WSL 没装**：Docker 的安装包不会顺带装 WSL 程序本身，重启后提示没有 WSL。`winget install Microsoft.WSL` 不会弹提权窗口，会以 0x80073d28 失败；要用管理员身份跑 `wsl --install --no-distribution`。
2. **egress 一启动就被 OOM 杀掉（退出码 137）**：Squid 按系统允许打开的最大文件数分配内存，Docker Desktop 上约一百万，超过 128 MB 上限。已在 `egress/squid.conf` 里写死 `max_filedescriptors 1024`，之后 egress 约占 14 MB。云端 Linux 上没出现，是因为那边上限小。
3. **bot 不回复，日志里什么都没有**：llmcord 对不符合条件的消息会直接忽略，不记日志。排查顺序：是不是 @ 到了 bot 本人（不是同名身份组）→ 用户 ID 在不在名单 → 频道 ID 对不对。可以用 bot 的 token 在容器里调 Discord API，读频道最近几条消息的作者和 mentions，只看元数据、不打印内容和 token。
4. Squid 配置里 `::ffff:0:0/96` 会被当成 `0.0.0.0/0`，启动时会打 warning 并忽略它。不影响拦截，私网段都有单独列出。
