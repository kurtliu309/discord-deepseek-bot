#!/usr/bin/env python3
"""Keyless MCP tools server: search (SearXNG), read a public web page, read a video's
public subtitles. Design: docs/notes/design-v3.md §10.

The bot holds the secrets and decides *whether* the model may use tools; this container
holds NO secrets and only ever reads public web content. It is reachable on `botnet` only,
and its outbound traffic goes through the egress proxy (HTTPS_PROXY), which allows any
public host on 443 and blocks private / loopback / link-local ranges.

Every tool takes `guild_id` and `allow_domains`; the bot injects them, the model never
fills them in. This server validates them on every call and enforces the domain allowlist
itself. The trust level (owner / public) and the per-group quota stay in the bot — the only
component allowed to read group configs; the tools container has no mounts.

Transport: MCP streamable HTTP on 0.0.0.0:8090, path /mcp (http://<host>:8090/mcp).

Copied code is marked with "copied from" and the source path; it is kept byte-for-byte
where possible so it stays easy to compare with the original.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import yt_dlp
from mcp.server.fastmcp import FastMCP

HOST = os.environ.get("TOOLS_HOST", "0.0.0.0")
PORT = int(os.environ.get("TOOLS_PORT", "8090"))
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080").rstrip("/")

# Same limits as fetcher.py: 10 MB per response, at most 6 redirect hops.
MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 6
UA = "Mozilla/5.0 (compatible; discord-deepseek-bot-tools/1.0)"
MAX_CHARS_LIMIT = 50000

SECRET_VARS = ("DISCORD_BOT_TOKEN", "DEEPSEEK_API_KEY")
_GUILD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# github.com/<u>/<r>/blob/... is rewritten to raw.githubusercontent.com by github_raw();
# a group that allows github.com therefore also allows that rewrite (and its redirects).
DOMAIN_ALIASES = {"raw.githubusercontent.com": "github.com"}


def log(msg: str) -> None:
    print(f"[tools] {msg}", flush=True)


class ToolInputError(ValueError):
    """Bad input or a request outside the group's allowlist. Message is shown to the model."""


# --------------------------------------------------------------------------- #
# Input checks (every call: guild id present, allowlist well formed)
# --------------------------------------------------------------------------- #
def check_guild_id(guild_id) -> None:
    """The bot always injects the group id; anything else means a broken/injected call."""
    if not isinstance(guild_id, str) or not _GUILD_RE.match(guild_id):
        raise ToolInputError("guild_id 缺失或格式不对（应由 bot 注入，不是模型填）")


def normalize_allow_domains(value) -> list[str] | None:
    """None = no filtering (group config says `allow_domains: any`); [] = nothing allowed.

    Accepted: "any", "example.com, foo.org", ["example.com", "foo.org"], ".example.com",
    "*.example.com" (a leading dot / star means the same thing here: domain + subdomains).
    """
    if value is None:
        return None
    if isinstance(value, str):
        raw = [p for p in re.split(r"[,\s]+", value.strip()) if p]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(p) for p in value]
    else:
        raise ToolInputError("allow_domains 必须是 'any' 或域名列表")
    if not raw:
        return []
    out = []
    for part in raw:
        p = part.strip().lower().rstrip(".")
        if p in ("any", "*", ""):
            return None
        p = p[2:] if p.startswith("*.") else p.lstrip(".")
        if not _DOMAIN_RE.match(p):
            raise ToolInputError(f"allow_domains 里有非法域名：{part!r}")
        if p not in out:
            out.append(p)
    return out


def host_allowed(host: str, allow: list[str] | None) -> bool:
    """`allow is None` = any host. Otherwise an entry matches itself and all its subdomains."""
    if allow is None:
        return True
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    candidates = [host]
    if host in DOMAIN_ALIASES:
        candidates.append(DOMAIN_ALIASES[host])
    for cand in candidates:
        for entry in allow:
            if cand == entry or cand.endswith("." + entry):
                return True
    return False


_PRIVATE_HOST_RE = re.compile(
    r"^(localhost|.*\.localhost|.*\.local|.*\.internal|"
    r"0\..*|10\..*|127\..*|169\.254\..*|"
    r"172\.(1[6-9]|2[0-9]|3[01])\..*|192\.168\..*|"
    r"100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\..*|"
    r"\[?::1\]?|\[?f[cd][0-9a-f]{2}:.*)$", re.IGNORECASE)


def check_url(url: str, allow: list[str] | None) -> None:
    """Raise ToolInputError with a human-readable reason when `url` may not be read."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ToolInputError("只允许 https 链接")
    host = parsed.hostname or ""
    if not host:
        raise ToolInputError("链接里没有域名")
    if _PRIVATE_HOST_RE.match(host):
        raise ToolInputError(f"不读内网/本机地址：{host}")
    if not host_allowed(host, allow):
        raise ToolInputError(f"域名 {host} 不在这个群的工具白名单里，拒绝读取")


def prepare_url(url: str) -> str:
    """Trim, default to https, upgrade http -> https, and rewrite GitHub blob URLs."""
    url = (url or "").strip()
    if not url:
        raise ToolInputError("url 不能为空")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = "https://" + url
    if url.lower().startswith("http://"):
        url = https(url)
    if not url.lower().startswith("https://"):
        raise ToolInputError("只允许 https 链接")
    return github_raw(url)


def clamp_int(value, low: int, high: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def clip(text: str, max_chars: int) -> str:
    """Cut to max_chars characters and say so; always ends with a newline."""
    text = (text or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[内容太长，已截断到 {max_chars} 字]"
    return text + "\n"


# --------------------------------------------------------------------------- #
# Small helpers: copied from fetcher.py (same project) so the bot's
# read_links path and this tool behave identically.
# --------------------------------------------------------------------------- #
def https(url):
    # copied from fetcher.py
    # egress only allows HTTPS (CONNECT 443): upgrade plain http links and redirects.
    return "https://" + url[len("http://"):] if url.lower().startswith("http://") else url


def github_raw(url):
    # copied from fetcher.py
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/blob/(.+)$", url)
    return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else url


class TextOnly(HTMLParser):
    # copied from fetcher.py
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "pre", "article", "section"}

    def __init__(self):
        super().__init__()
        self.out, self.title, self._skip, self._in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(data)

    def text(self):
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.out))
        return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def html_to_text(raw):
    # copied from fetcher.py
    p = TextOnly()
    p.feed(raw)
    return p.title.strip(), p.text()


def client():
    # copied from fetcher.py
    # trust_env: picks up HTTPS_PROXY (the egress) from the environment.
    return httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA}, max_redirects=5)


def searx_client():
    # SearXNG sits next to us on botnet: it must never go through the egress proxy, so this
    # client ignores the proxy environment entirely (NO_PROXY also lists it; belt and braces).
    return httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": UA},
                        trust_env=False)


def download(url, allow: list[str] | None = None):
    # copied from fetcher.py, with one addition: every redirect hop is checked against the
    # group's allowlist, so a public page cannot bounce us onto a forbidden host.
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ToolInputError("只接受 http(s) 链接")
    url = https(url)
    with client() as c:
        for _ in range(MAX_REDIRECTS):
            r = c.send(c.build_request("GET", url), stream=True, follow_redirects=False)
            if r.is_redirect and r.headers.get("location"):
                r.close()
                url = https(str(r.url.join(r.headers["location"])))
                check_url(url, allow)
                continue
            break
        else:
            raise ToolInputError("重定向次数过多")
        try:
            return _read(r)
        finally:
            r.close()


def _read(r):
    # copied from fetcher.py
    r.raise_for_status()
    if int(r.headers.get("content-length") or 0) > MAX_BYTES:
        raise ValueError("too big (> 10 MB)")
    buf = bytearray()
    for chunk in r.iter_bytes():
        buf += chunk
        if len(buf) > MAX_BYTES:
            raise ValueError("too big (> 10 MB)")
    return bytes(buf), r.headers.get("content-type", ""), str(r.url)


# --------------------------------------------------------------------------- #
# Tool 1: search (SearXNG JSON API)
# --------------------------------------------------------------------------- #
def filter_search_results(items, allow: list[str] | None, limit: int) -> list[dict]:
    """Drop results outside the allowlist and keep at most `limit` of them."""
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        try:
            host = urlparse(url).hostname or ""
        except ValueError:
            continue
        if not host_allowed(host, allow):
            continue
        out.append({
            "title": (item.get("title") or "").strip()[:300],
            "url": url,
            "snippet": (item.get("content") or "").strip()[:500],
        })
        if len(out) >= limit:
            break
    return out


def do_search(query: str, guild_id, allow_domains, max_results: int = 8) -> str:
    check_guild_id(guild_id)
    allow = normalize_allow_domains(allow_domains)
    q = (query or "").strip()
    if not q:
        return "错误：query 不能为空"
    limit = clamp_int(max_results, 1, 20, 8)
    try:
        with searx_client() as c:
            r = c.get(f"{SEARXNG_URL}/search", params={"q": q, "format": "json"}, timeout=20)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"搜索失败：{type(e).__name__}: {e}"

    out = filter_search_results(data.get("results") or [], allow, limit)
    if not out:
        suffix = "（白名单过滤后没有结果）" if allow is not None else ""
        return f"没有搜到结果{suffix}"
    return json.dumps(out, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
# Tool 2: read_page
# --------------------------------------------------------------------------- #
def do_read_page(url: str, guild_id, allow_domains, max_chars: int = 20000) -> str:
    check_guild_id(guild_id)
    allow = normalize_allow_domains(allow_domains)
    try:
        url = prepare_url(url)
        check_url(url, allow)
    except ToolInputError as e:
        return f"不能读这个链接：{e}"
    limit = clamp_int(max_chars, 500, MAX_CHARS_LIMIT, 20000)

    try:
        data, ctype, final = download(url, allow)
    except ToolInputError as e:
        return f"不能读这个链接：{e}"
    except httpx.HTTPError as e:
        return f"抓取失败：{type(e).__name__}"
    except ValueError as e:
        return f"抓取失败：{e}"

    ctype = (ctype or "").lower()
    if "pdf" in ctype:
        return "这是一个 PDF/文件链接，read_page 只读网页（附件由 bot 的 fetcher 处理）"
    if "html" in ctype or urlparse(final).path.lower().endswith((".html", ".htm")):
        title, text = html_to_text(data.decode("utf-8", errors="replace"))
    elif "text" in ctype or "json" in ctype or "xml" in ctype or not ctype:
        if b"\x00" in data[:4096]:
            return "这个链接返回的是二进制内容，不是网页"
        title, text = "", data.decode("utf-8", errors="replace")
    else:
        return f"这个链接的内容类型是 {ctype}，read_page 只读网页"

    title = title.strip() or urlparse(final).path.rsplit("/", 1)[-1] or "(无标题)"
    body = f"# {title}\n来源：{final}\n\n{text.strip()}"
    return clip(body, limit)


# --------------------------------------------------------------------------- #
# Subtitle helpers: copied from
# D:\Kurt\hub\global\research\skills\fetch-and-summarize\fetch_video.py
# (the subtitle-only path; ffmpeg / CUDA / Whisper bits are intentionally left out).
# --------------------------------------------------------------------------- #
PREFERRED_LANGS = ["zh-Hans", "zh-CN", "ai-zh", "zh", "zh-Hant", "en"]
SUBTITLE_EXT_ORDER = ["json3", "srt", "vtt", "json"]


def fmt_ts(seconds: float) -> str:
    # copied from fetch_video.py
    s = max(0, int(seconds or 0))
    return f"{s // 60:02d}:{s % 60:02d}"


def fmt_duration(seconds) -> str:
    # copied from fetch_video.py
    s = max(0, int(seconds or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _join_parts(parts: list[str]) -> str:
    # copied from fetch_video.py
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and not (_is_cjk(out[-1]) and _is_cjk(part[0])):
            out += " "
        out += part
    return re.sub(r"\s+", " ", out).strip()


def _is_cjk(ch: str) -> bool:
    # copied from fetch_video.py
    return "\u3400" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef"


def _norm_lang(lang: str) -> str:
    # copied from fetch_video.py
    return (lang or "").strip().lower().replace("_", "-")


def _find_lang(mapping: dict, lang: str):
    # copied from fetch_video.py
    if not mapping:
        return None
    if lang in mapping:
        return mapping[lang]
    want = _norm_lang(lang)
    for key, value in mapping.items():
        if _norm_lang(key) == want:
            return value
    for key, value in mapping.items():
        if _norm_lang(key).startswith(want + "-"):
            return value
    return None


def _choose_entry(entries):
    # copied from fetch_video.py
    if not entries:
        return None
    if isinstance(entries, dict):
        entries = [entries]
    ranked = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        ext = (item.get("ext") or "").lower()
        if ext == "xml":  # danmaku XML is not a subtitle
            continue
        rank = SUBTITLE_EXT_ORDER.index(ext) if ext in SUBTITLE_EXT_ORDER else len(SUBTITLE_EXT_ORDER)
        ranked.append((rank, item))
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0])
    return ranked[0][1]


def pick_subtitle(subtitles: dict, automatic_captions: dict):
    # copied from fetch_video.py
    manual = {k: v for k, v in (subtitles or {}).items()
              if not _norm_lang(k).startswith("danmaku")}
    auto = {k: v for k, v in (automatic_captions or {}).items()
            if not _norm_lang(k).startswith("danmaku")}

    for lang in PREFERRED_LANGS:
        entry = _choose_entry(_find_lang(manual, lang))
        if entry:
            return "manual", lang, entry
        entry = _choose_entry(_find_lang(auto, lang))
        if entry:
            return "auto", lang, entry
    for lang, entries in manual.items():
        entry = _choose_entry(entries)
        if entry:
            return "manual", lang, entry
    for lang, entries in auto.items():
        entry = _choose_entry(entries)
        if entry:
            return "auto", lang, entry
    return None


_SRT_TIME = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_VTT_TIME = re.compile(
    r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{1,3})")


def _time_from_match(groups) -> float:
    # copied from fetch_video.py
    h, m, s, ms = groups
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    # copied from fetch_video.py
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n{2,}", text.strip()):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines:
            continue
        m = _SRT_TIME.search(lines[0])
        if m:
            start, end = _time_from_match(m.groups()[:4]), _time_from_match(m.groups()[4:])
        else:
            m = _VTT_TIME.search(lines[0])
            if not m:
                continue
            g = m.groups()
            start = _time_from_match([g[0], g[1], g[2], g[3]])
            end = _time_from_match([g[4], g[5], g[6], g[7]])
        body = " ".join(lines[1:])
        body = html.unescape(re.sub(r"<[^>]+>", "", body))
        body = _join_parts(body.split())
        if body:
            cues.append((start, end, body))
    return cues


def parse_json3(text: str) -> list[tuple[float, float, str]]:
    # copied from fetch_video.py
    data = json.loads(text)
    cues = []
    events = data.get("events") if isinstance(data, dict) else None
    if isinstance(events, list):
        for ev in events:
            segs = ev.get("segs") or []
            body = _join_parts([s.get("utf8", "") for s in segs])
            body = re.sub(r"\s+", " ", body.replace("\n", " ")).strip()
            if not body:
                continue
            start = float(ev.get("tStartMs") or 0) / 1000.0
            dur = float(ev.get("dDurationMs") or 0) / 1000.0
            cues.append((start, start + dur, body))
        return cues
    body = data.get("body") if isinstance(data, dict) else None
    if isinstance(body, list):
        for item in body:
            if not isinstance(item, dict):
                continue
            content = _join_parts([str(item.get("content") or "")])
            if not content:
                continue
            start = float(item.get("from") or 0)
            end = float(item.get("to") or start)
            cues.append((start, end, content))
    return cues


def parse_subtitle(text: str, ext: str = "") -> list[tuple[float, float, str]]:
    # copied from fetch_video.py
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return parse_json3(text)
        except (ValueError, TypeError):
            pass
    if "-->" in text:
        return parse_srt(text)  # parse_srt skips the WEBVTT header block
    return []


def merge_cues(cues, min_span: float = 15.0, max_span: float = 30.0,
               max_gap: float = 2.0) -> list[tuple[float, str]]:
    # copied from fetch_video.py
    groups = []
    cur = None  # [start, end, [texts]]
    for start, end, text in sorted(cues, key=lambda c: (c[0], c[1])):
        text = (text or "").strip()
        if not text:
            continue
        end = max(end, start)
        if cur is None:
            cur = [start, end, [text]]
        elif start - cur[1] <= max_gap and start - cur[0] <= max_span:
            cur[1] = max(cur[1], end)
            cur[2].append(text)
        else:
            groups.append(cur)
            cur = [start, end, [text]]
        if cur[1] - cur[0] >= min_span:
            groups.append(cur)
            cur = None
    if cur is not None:
        groups.append(cur)
    return [(g[0], _join_parts(g[2])) for g in groups]


def _single_entry(info: dict) -> dict:
    # copied from fetch_video.py: if yt-dlp handed back a playlist, take the first entry.
    if info.get("_type") == "playlist" and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        if entries:
            return entries[0]
    return info


# --------------------------------------------------------------------------- #
# Tool 3: read_video (public subtitles only — no audio download, no ASR, no cookies)
# --------------------------------------------------------------------------- #
def video_opts() -> dict:
    """yt-dlp options. No cookies, no ffmpeg: metadata and subtitles only."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "noprogress": True,
    }
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        opts["proxy"] = proxy
    return opts


def download_subtitle(entry: dict, opts: dict) -> str | None:
    # copied from fetch_video.py
    data = entry.get("data")
    if isinstance(data, str) and data.strip():
        return data
    url = entry.get("url")
    if not url:
        return None
    with yt_dlp.YoutubeDL(opts) as ydl:
        resp = ydl.urlopen(url)
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def explain_video_error(err: Exception) -> str:
    # trimmed from fetch_video.py: the cookie / ASR branches do not apply here.
    low = str(err).lower()
    if "unsupported url" in low:
        return "不支持的链接：yt-dlp 无法识别这个站点"
    if any(k in low for k in ("login", "sign in", "members-only", "private video",
                              "subscription", "登录", "会员")):
        return "这个视频需要登录或会员权限；只读工具不带 cookies"
    if any(k in low for k in ("timed out", "timeout", "connection", "network",
                              "getaddrinfo", "unable to download webpage", "reset")):
        return "网络错误：连接失败或超时"
    if "404" in low or "deleted" in low:
        return "视频不存在或已被删除"
    detail = [ln for ln in str(err).splitlines() if ln.strip()]
    return f"{type(err).__name__}: {detail[-1][:200] if detail else '未知错误'}"


def format_video(title: str, url: str, uploader: str, duration, lang: str,
                 kind: str, cues) -> str:
    merged = merge_cues(cues)
    lines = [
        f"# {title or '(无标题)'}",
        f"链接：{url}",
        f"频道：{uploader or '未知'}",
        f"时长：{fmt_duration(duration)}",
        f"字幕：{lang}（{'人工' if kind == 'manual' else '自动'}）",
        "",
    ]
    for start, text in merged:
        lines.append(f"[{fmt_ts(start)}] {text}")
    return "\n".join(lines).rstrip()


def do_read_video(url: str, guild_id, allow_domains, max_chars: int = 30000) -> str:
    check_guild_id(guild_id)
    allow = normalize_allow_domains(allow_domains)
    try:
        url = prepare_url(url)
        check_url(url, allow)
    except ToolInputError as e:
        return f"不能读这个视频：{e}"
    limit = clamp_int(max_chars, 500, MAX_CHARS_LIMIT, 30000)
    opts = video_opts()

    try:
        with yt_dlp.YoutubeDL({**opts, "writesubtitles": True}) as ydl:
            info = _single_entry(ydl.extract_info(url, download=False))
    except Exception as e:
        return f"读取视频失败：{explain_video_error(e)}"

    chosen = pick_subtitle(info.get("subtitles"), info.get("automatic_captions"))
    if not chosen:
        return "这个视频没有公开字幕"
    kind, lang, entry = chosen

    try:
        raw = download_subtitle(entry, opts)
        cues = parse_subtitle(raw or "", entry.get("ext") or "")
    except Exception as e:
        return f"字幕下载失败：{type(e).__name__}: {str(e).splitlines()[-1][:200] if str(e).strip() else ''}"
    if not cues:
        return "这个视频没有公开字幕"

    title = info.get("title") or ""
    uploader = info.get("uploader") or info.get("channel") or info.get("creator") or ""
    final_url = info.get("webpage_url") or url
    duration = info.get("duration") or cues[-1][1]
    return clip(format_video(title, final_url, uploader, duration, lang, kind, cues), limit)


# --------------------------------------------------------------------------- #
# MCP wrapper
# --------------------------------------------------------------------------- #
mcp = FastMCP(
    "ddb-tools",
    instructions=(
        "Read-only web tools for one Discord group. `guild_id` and `allow_domains` are "
        "filled in by the bot; do not invent them. search returns JSON [{title,url,snippet}]; "
        "read_page and read_video return plain text."
    ),
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    # No server -> client messages, no per-client state: each POST stands alone.
    stateless_http=True,
)


@mcp.tool()
def search(query: str, guild_id: str, allow_domains: str | list[str] = "any",
           max_results: int = 8) -> str:
    """Search the web. Returns a JSON list of {title, url, snippet}, filtered by allow_domains."""
    return do_search(query, guild_id, allow_domains, max_results)


@mcp.tool()
def read_page(url: str, guild_id: str, allow_domains: str | list[str] = "any",
              max_chars: int = 20000) -> str:
    """Read one public https page and return its text (title + body, truncated to max_chars)."""
    return do_read_page(url, guild_id, allow_domains, max_chars)


@mcp.tool()
def read_video(url: str, guild_id: str, allow_domains: str | list[str] = "any",
               max_chars: int = 30000) -> str:
    """Read a video's PUBLIC subtitles as timestamped text. No audio, no transcription."""
    return do_read_video(url, guild_id, allow_domains, max_chars)


def assert_no_secrets(environ=None) -> None:
    """Same rule as fetcher.py: this container must never see a secret."""
    env = os.environ if environ is None else environ
    if any(env.get(k) for k in SECRET_VARS):
        sys.exit("[tools] refusing to start: secrets must not be passed to the tools container")


def main() -> None:
    assert_no_secrets()
    log(f"listening on {HOST}:{PORT}/mcp searxng={SEARXNG_URL} "
        f"proxy={os.environ.get('HTTPS_PROXY', '')}")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
