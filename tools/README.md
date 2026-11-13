# tools — 无密钥的 MCP 只读工具容器 + SearXNG

给 bot 的模型提供三个只读工具（设计见 [../docs/notes/design-v3.md](../docs/notes/design-v3.md) §10）：

| 工具 | 做什么 | 返回 |
|---|---|---|
| `search(query, guild_id, allow_domains, max_results=8)` | 调 SearXNG 的 JSON API | JSON 列表 `[{title,url,snippet}]`，`allow_domains` 不是 `any` 时过滤 |
| `read_page(url, guild_id, allow_domains, max_chars=20000)` | 只读 https 网页（http 自动升级），HTML 转文字 | 标题 + 正文文本 |
| `read_video(url, guild_id, allow_domains, max_chars=30000)` | 只拿**公开字幕**（不下载音频、不转写、不用 cookies） | 带时间戳的字幕文本；没有字幕就返回「这个视频没有公开字幕」 |

`guild_id` 和 `allow_domains` 由 **bot 注入**，不是模型填。服务端每次调用都校验：
`guild_id` 必须是合法 ID；`allow_domains` 为 `"any"`（或 `None`）表示不限，否则是域名
（逗号分隔字符串或列表，`example.com` 含所有子域，`.example.com` / `*.example.com` 等价）。
域名不在名单 → 直接返回错误说明，不抓。重定向每一跳也重新查白名单。
**信任等级（owner/public）和次数额度查在 bot 里**——tools 容器不挂任何文件，读不到群配置。

## 文件

```
tools/
├─ server.py                 FastMCP（官方 mcp SDK），streamable HTTP，监听 0.0.0.0:8090，路径 /mcp
├─ requirements.txt          mcp==1.30.0（FastMCP 在 1.x；2.x 改名 MCPServer）、httpx、yt-dlp
├─ Dockerfile                非 root 10001:10001，无 ffmpeg/Whisper（只取字幕）
├─ docker-compose.test.yml   仅供本地功能测试的独立栈（项目名 ddb-toolstest）
├─ tests/
│  ├─ test_server.py         离线单测（unittest，不联网）
│  ├─ live_probe.py          真调三个工具的冒烟脚本（用 mcp 客户端）
│  └─ searxng-settings.test.yml  测试栈用的 SearXNG 配置（比正式少 outgoing.proxies）
└─ README.md

../searxng/settings.yml            正式栈 SearXNG 配置（JSON 输出、5 个引擎、走 egress）
../docker-compose.tools.yml        正式栈的 services 片段（tools 172.30.0.21、searxng 172.30.0.22）
../egress/squid.tools.snippet.conf 正式栈 squid 的 ACL 片段（两个新地址同 fetcher 规则）
```

## 跑离线单测

不要在主机上 pip install；全部在镜像里跑：

```bash
docker build -t ddb-tools:test tools
docker run --rm ddb-tools:test python -m unittest discover -s tests
```

期望最后一行：`OK`（29 个用例）。

## 跑本地功能测试（独立栈，不碰正式栈）

```bash
docker compose -f tools/docker-compose.test.yml -p ddb-toolstest up -d --build

# 冒烟：连 MCP、真调 search / read_page / read_video 各一次
docker run --rm --network ddb-toolstest_testnet \
    -e TOOLS_MCP_URL=http://tools:8090/mcp ddb-tools:test python tests/live_probe.py
# 或者走测试栈映射到本机的端口（127.0.0.1:18090）：
docker run --rm --network host \
    -e TOOLS_MCP_URL=http://localhost:18090/mcp ddb-tools:test python tests/live_probe.py

docker compose -f tools/docker-compose.test.yml -p ddb-toolstest down
```

测试栈和正式栈的差别只有：自己的网络 `ddb-toolstest_testnet`、直连外网（无 egress、无代理
环境变量）、SearXNG 用 `tests/searxng-settings.test.yml`、tools 映射 `127.0.0.1:18090`。
加固项（read_only、cap_drop ALL、no-new-privileges、非 root、tmpfs、资源上限）和正式栈一致。

主机上端口 18090 只在这套测试栈里开；正式栈**不加任何 ports**。

## 合并进正式栈时要做的事

1. **squid**：把 `egress/squid.tools.snippet.conf` 里两行贴进 `egress/squid.conf`——
   `acl tools_client src 172.30.0.21 172.30.0.22` 放在 `acl fetcher_client ...` 旁边，
   `http_access allow tools_client` 放在 `http_access allow fetcher_client` 之后、
   `http_access deny all` 之前。然后 `docker compose up -d --build egress`。
   不改 `egress/allowlist.txt`（bot/scheduler 的白名单不变）。
2. **SearXNG 的 secret_key**：`searxng/settings.yml` 里是占位符 `REPLACE_ME_AT_DEPLOY`。
   部署时生成一个真的填进去（别提交进 git）：
   ```bash
   py -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   改完 `docker compose up -d --force-recreate searxng`。
3. **compose**：把 `docker-compose.tools.yml` 的两个 service 合并进 `docker-compose.yml`
   （或直接用 `docker compose -f docker-compose.yml -f docker-compose.tools.yml up -d --build`）。
   片段里的加固项是照抄的（YAML 锚点不跨文件），合并后可以换成 `<<: *hardening`。
   两个新服务都**不给 env_file**、不挂 data、不加 ports。
4. **bot 侧连接**：MCP 地址 `http://172.30.0.21:8090/mcp`（streamable HTTP，路径 `/mcp`，
   无鉴权，只靠 botnet 内部网络隔离）。bot 的镜像要装 `mcp` 客户端（`mcp>=1.30,<2`），
   每次工具调用把该群的 `guild_id` 和 `allow_domains` 填进去；public 群根本不给模型工具列表。
   bot 出网白名单不用改（它只访问 botnet 内的 172.30.0.21）。
5. **启动顺序**：tools 依赖 egress 和 searxng；searxng 只依赖 egress。

## 注意 / 坑

- **只有 https 能出网**：squid 只放行 `CONNECT 443`。只用 http 的搜索源会被拦（引擎会显示为
  unresponsive），这是有意的。`outgoing.proxies` 已指向 `http://egress:3128`。
- **DuckDuckGo 可能返回 CAPTCHA**（换 IP/被限流时），它只是几个引擎之一；实测 bing、brave、
  github 都能出结果。SearXNG 的 `unresponsive_engines` 只记在响应里，不写日志。
- **wikipedia 引擎必须有 `general.contact_url`**，否则不加载（已填占位 `.invalid`）。
- **read_video 不带 cookies**：需要登录/会员的视频、以及没有字幕的视频都会明确返回原因；
  不下载音频、不转写（Whisper 要显卡，按设计留在主机，本轮不做）。
- **Subtitle/HTML 解析是复制来的**：`server.py` 里 `TextOnly/html_to_text/github_raw/https/
  download/_read` 抄自 `fetcher.py`；字幕选择与 srt/json3 解析抄自
  `D:\Kurt\hub\global\research\skills\fetch-and-summarize\fetch_video.py`（去掉了 Windows 的
  ffmpeg/CUDA 部分）。函数上标了出处，改一边时记得对齐另一边。
- **MCP 1.x**：`mcp` 2.x 把 FastMCP 改名成 `MCPServer`，所以 requirements 钉 `mcp==1.30.0`。
  升级时先看迁移文档。
- `stateless_http=True`：每次 POST 独立，没有服务端会话；FastMCP 的 DNS-rebinding 保护在
  `host=0.0.0.0` 时默认关闭（容器只在 botnet 内可达）。
