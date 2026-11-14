#!/usr/bin/env python3
"""Keyless fetcher: the only component that reads arbitrary web pages and parses files.

It holds no bot / model keys. bot / scheduler hand it a URL or file bytes over the internal
network and get plain text back. All its outbound traffic goes through the egress proxy,
which lets it reach any public host on 443 but still blocks private / loopback / link-local
ranges. The one credential it has is a throwaway B站 account's cookie file, mounted
read-only and sent to B站's own API only (see fetch_bilibili).
Parsers (pdf, docx, xlsx, pptx) run only here, so a malicious file cannot reach a secret.

Endpoints (JSON in / JSON out, listen on :8080):
  POST /url              {"url": str, "max_chars": int}     -> {"ok", "title", "text", "truncated"} | {"ok": false, "error"}
                         max_chars is optional (default FETCH_MAX_TEXT, clamped to 1000..60000)
  POST /file?name=<fn>   raw bytes (<= 10 MB)               -> same shape
  POST /video            {"url": str, "max_chars": int}     -> same shape (public subtitles only)
                         yt-dlp reads the video metadata and its public subtitle track through
                         the egress proxy; no audio download, no ffmpeg, no ASR, and yt-dlp
                         never gets cookies. B站 links use B站's own API with the throwaway
                         account's cookies when that file is mounted.
  POST /read             {"url": str, "max_chars": int}     -> same shape plus "kind": "video" | "page"
                         Decides by itself: a URL that a site-specific yt-dlp extractor knows
                         is read as a video; anything else as a page. A video whose subtitles
                         can't be read falls back to the page text with a note in front.
                         (/url and /video stay for compatibility and tests.)
  POST /rss              {"feeds": [{"name","url"}], "per_feed": int}   -> {"items": [{"source","title","link","summary"}]}
                         (Reddit uses its public RSS feeds through /rss; requests to one host are spaced out)
  GET  /health                                              -> {"ok": true}
"""

import csv
import html
import io
import json
import os
import re
import sys
import time
import zipfile
from html.parser import HTMLParser
from http.cookiejar import MozillaCookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
import yt_dlp

MAX_BYTES = 10 * 1024 * 1024
MAX_UNZIPPED = 50 * 1024 * 1024
MAX_TEXT = int(os.environ.get("FETCH_MAX_TEXT", "30000"))
# Optional max_chars from a request is clamped to this range (a video subtitle track can be
# long, but the model still gets a bounded amount of text).
MAX_CHARS_MIN, MAX_CHARS_MAX = 1000, 60000
VIDEO_CHARS = 30000
UA = "Mozilla/5.0 (compatible; discord-deepseek-bot-fetcher/1.0)"

TEXT_EXT = {
    "txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "log", "ini", "toml", "xml",
    "py", "vex", "vfl", "h", "c", "cpp", "hpp", "cc", "js", "ts", "jsx", "tsx", "sh", "bat", "ps1",
    "glsl", "hlsl", "osl", "usda", "cmd", "lua", "rs", "go", "java", "cs", "sql", "html", "htm", "css",
}
OFFICE_EXT = {"docx", "xlsx", "pptx"}
UNSUPPORTED_EXT = {"hip", "hipnc", "hiplc", "hda", "otl", "bgeo", "sc", "png", "jpg", "jpeg", "gif", "webp",
                   "zip", "7z", "rar", "mp3", "wav", "mp4", "mov", "exr", "usd", "usdc", "abc"}


def log(msg):
    print(f"[fetcher] {msg}", flush=True)


class TextOnly(HTMLParser):
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
    p = TextOnly()
    p.feed(raw)
    return p.title.strip(), p.text()


def cap(text, max_chars=None):
    limit = MAX_TEXT if max_chars is None else max_chars
    text = text.strip()
    return (text[:limit], True) if len(text) > limit else (text, False)


def clamp_int(value, low, high, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def check_zip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
            raise ValueError("file unpacks too big")


def parse_file(name, data):
    """Bytes -> text. Raises ValueError('unsupported') for formats we don't read."""
    ext = ext_of(name)
    if ext in UNSUPPORTED_EXT:
        raise ValueError("unsupported")
    if ext == "pdf" or data[:5] == b"%PDF-":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for i, page in enumerate(reader.pages):
            parts.append(page.extract_text() or "")
            if sum(map(len, parts)) > MAX_TEXT:
                break
        return "\n\n".join(parts)
    if ext in OFFICE_EXT:
        check_zip(data)
        if ext == "docx":
            import docx
            d = docx.Document(io.BytesIO(data))
            lines = [p.text for p in d.paragraphs]
            for t in d.tables:
                for row in t.rows:
                    lines.append(" | ".join(c.text for c in row.cells))
            return "\n".join(lines)
        if ext == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets:
                out.append(f"## sheet: {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    if any(v is not None for v in row):
                        out.append(",".join("" if v is None else str(v) for v in row))
                    if sum(map(len, out)) > MAX_TEXT:
                        break
            return "\n".join(out)
        if ext == "pptx":
            from pptx import Presentation
            prs = Presentation(io.BytesIO(data))
            out = []
            for n, slide in enumerate(prs.slides, 1):
                out.append(f"## slide {n}")
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                        out.append(shape.text_frame.text)
            return "\n".join(out)
    if ext in TEXT_EXT or not ext:
        text = data.decode("utf-8", errors="replace")
        if ext in ("html", "htm"):
            return html_to_text(text)[1]
        if "\x00" in text[:4096]:
            raise ValueError("unsupported")
        return text
    raise ValueError("unsupported")


def github_raw(url):
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/blob/(.+)$", url)
    return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else url


def client():
    # trust_env: picks up HTTPS_PROXY (the egress) from the environment.
    return httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA}, max_redirects=5)


def https(url):
    # egress only allows HTTPS (CONNECT 443): upgrade plain http links and redirects.
    return "https://" + url[len("http://"):] if url.lower().startswith("http://") else url


def download(url):
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError("only http(s) urls")
    url = https(url)
    with client() as c:
        for _ in range(6):  # follow redirects by hand so each hop is upgraded to https
            r = c.send(c.build_request("GET", url), stream=True, follow_redirects=False)
            if r.is_redirect and r.headers.get("location"):
                r.close()
                url = https(str(r.url.join(r.headers["location"])))
                continue
            break
        else:
            raise ValueError("too many redirects")
        try:
            return _read(r)
        finally:
            r.close()


def _read(r):
    r.raise_for_status()
    if int(r.headers.get("content-length") or 0) > MAX_BYTES:
        raise ValueError("too big (> 10 MB)")
    buf = bytearray()
    for chunk in r.iter_bytes():
        buf += chunk
        if len(buf) > MAX_BYTES:
            raise ValueError("too big (> 10 MB)")
    return bytes(buf), r.headers.get("content-type", ""), str(r.url)


def fetch_url(url, max_chars=None):
    url = github_raw((url or "").strip())
    data, ctype, final = download(url)
    name = urlparse(final).path.rsplit("/", 1)[-1] or "page"
    if "html" in ctype:
        title, text = html_to_text(data.decode("utf-8", errors="replace"))
    else:
        if "pdf" in ctype and not name.lower().endswith(".pdf"):
            name += ".pdf"
        title, text = name, parse_file(name if "." in name else name + ".txt", data)
    text, truncated = cap(text, max_chars)
    return {"ok": True, "title": title[:300], "text": text, "truncated": truncated}


def polite_download(url, last_host):
    """download() that spaces out requests to the same host and honours one 429 Retry-After."""
    host = urlparse(url).hostname
    if host == last_host:
        time.sleep(5)
    try:
        return download(url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 429:
            raise
        wait = min(int(e.response.headers.get("retry-after", "10") or 10), 30)
        log(f"429 from {host}: waiting {wait}s, then one retry")
        time.sleep(wait)
        return download(url)


def fetch_rss(feeds, per_feed):
    import feedparser
    items, last_host = [], None
    for f in feeds:
        try:
            data, _, _ = polite_download(f["url"], last_host)
            last_host = urlparse(f["url"]).hostname
            parsed = feedparser.parse(data)
            for e in parsed.entries[:per_feed]:
                summary = html_to_text(e.get("summary", ""))[1][:300]
                items.append({"source": f["name"], "title": e.get("title", "")[:300],
                              "link": e.get("link", ""), "summary": summary})
        except Exception as ex:
            log(f"rss {f.get('name')}: {type(ex).__name__}: {ex}")
    return items


# --------------------------------------------------------------------------- #
# Video: public subtitles only. Moved here from tools/server.py (which is being
# removed); the guild allowlist it used to check is gone — this fetcher serves one
# bot and blocks private / loopback hosts itself, like the egress proxy does.
# Subtitle helpers came from
# D:\Kurt\hub\global\research\skills\fetch-and-summarize\fetch_video.py (subtitle-only
# path; no ffmpeg / CUDA / Whisper bits).
# --------------------------------------------------------------------------- #
PREFERRED_LANGS = ["zh-Hans", "zh-CN", "ai-zh", "zh", "zh-Hant", "en"]
SUBTITLE_EXT_ORDER = ["json3", "srt", "vtt", "json"]

_PRIVATE_HOST_RE = re.compile(
    r"^(localhost|.*\.localhost|.*\.local|.*\.internal|"
    r"0\..*|10\..*|127\..*|169\.254\..*|"
    r"172\.(1[6-9]|2[0-9]|3[01])\..*|192\.168\..*|"
    r"100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\..*|"
    r"\[?::1\]?|\[?f[cd][0-9a-f]{2}:.*)$", re.IGNORECASE)


def check_video_url(url):
    """Trim, default to https, upgrade http -> https, and refuse loopback / private hosts."""
    url = (url or "").strip()
    if not url:
        raise ValueError("url 不能为空")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = "https://" + url
    url = https(url)
    if not url.lower().startswith("https://"):
        raise ValueError("只允许 https 链接")
    host = urlparse(url).hostname or ""
    if not host:
        raise ValueError("链接里没有域名")
    if _PRIVATE_HOST_RE.match(host):
        raise ValueError(f"不读内网/本机地址：{host}")
    return url


def fmt_ts(seconds):
    s = max(0, int(seconds or 0))
    return f"{s // 60:02d}:{s % 60:02d}"


def fmt_duration(seconds):
    s = max(0, int(seconds or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _is_cjk(ch):
    return "\u3400" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef"


def _join_parts(parts):
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and not (_is_cjk(out[-1]) and _is_cjk(part[0])):
            out += " "
        out += part
    return re.sub(r"\s+", " ", out).strip()


def _norm_lang(lang):
    return (lang or "").strip().lower().replace("_", "-")


def _find_lang(mapping, lang):
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


def pick_subtitle(subtitles, automatic_captions):
    manual = {k: v for k, v in (subtitles or {}).items()
              if not _norm_lang(k).startswith("danmaku")}
    auto = {k: v for k, v in (automatic_captions or {}).items()
            if not _norm_lang(k).startswith("danmaku")}

    # Order (v4.0 fix): any manual track beats any automatic one, and the video's own
    # automatic captions beat YouTube's machine translations of them. Translated tracks
    # (``tlang=`` in the URL) are generated on demand and are what YouTube answers with
    # 429 most often; the model can translate by itself, so they are the last resort.
    original = {k: v for k, v in auto.items() if not _is_translated(v)}
    translated = {k: v for k, v in auto.items() if _is_translated(v)}
    for kind, pool in (("manual", manual), ("auto", original), ("auto", translated)):
        for lang in PREFERRED_LANGS:
            entry = _choose_entry(_find_lang(pool, lang))
            if entry:
                return kind, lang, entry
        for lang, entries in pool.items():
            entry = _choose_entry(entries)
            if entry:
                return kind, lang, entry
    return None


def _is_translated(entries):
    """True for a YouTube auto-translated caption track (its URLs carry ``tlang=``)."""
    return any("tlang=" in str((e or {}).get("url") or "") for e in entries or [])


_SRT_TIME = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_VTT_TIME = re.compile(
    r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{1,3})")


def _time_from_match(groups):
    h, m, s, ms = groups
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text):
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


def parse_json3(text):
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


def parse_subtitle(text, ext=""):
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return parse_json3(text)
        except (ValueError, TypeError):
            pass
    if "-->" in text:
        return parse_srt(text)  # parse_srt skips the WEBVTT header block
    return []


def merge_cues(cues, min_span=15.0, max_span=30.0, max_gap=2.0):
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


def _single_entry(info):
    """If yt-dlp handed back a playlist, take the first entry."""
    if isinstance(info, dict) and info.get("_type") == "playlist" and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        if entries:
            return entries[0]
    return info


def video_opts():
    """yt-dlp options. No cookies, no ffmpeg: metadata and subtitles only.

    yt-dlp has no trust_env, so the egress proxy has to be passed explicitly.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "noprogress": True,
        "cachedir": False,  # root fs is read-only; nothing to cache between calls anyway
    }
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        opts["proxy"] = proxy
    return opts


def download_subtitle(entry, opts):
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


def explain_video_error(err):
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


def format_video(title, url, uploader, duration, lang, kind, cues):
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


def fetch_video(url, max_chars=VIDEO_CHARS):
    """A video's public subtitles as timestamped text. Raises ValueError with a
    model-readable reason when it cannot be read.

    B站 links go through B站's own API first (it has the AI subtitles), falling back to
    yt-dlp without cookies; every other site goes straight to yt-dlp.
    """
    url = check_video_url(url)
    limit = clamp_int(max_chars, MAX_CHARS_MIN, MAX_CHARS_MAX, VIDEO_CHARS)
    bili_error = None
    if is_bilibili(url) and bilibili_cookie_file():
        try:
            return fetch_bilibili(url, limit)
        except Exception as e:
            bili_error = str(e).strip() or type(e).__name__
            log(f"bilibili api: {bili_error[:200]}")
    try:
        return _fetch_video(url, limit, video_opts())
    except ValueError as e:
        if bili_error:
            raise ValueError(f"{e}（B站接口也不行：{bili_error}）") from e
        raise


def _fetch_video(url, limit, opts):
    try:
        with yt_dlp.YoutubeDL({**opts, "writesubtitles": True}) as ydl:
            info = _single_entry(ydl.extract_info(url, download=False))
    except Exception as e:
        raise ValueError(f"读取视频失败：{explain_video_error(e)}") from e
    if not isinstance(info, dict):
        raise ValueError("读取视频失败：没有拿到视频信息")

    chosen = pick_subtitle(info.get("subtitles"), info.get("automatic_captions"))
    if not chosen:
        raise ValueError("这个视频没有公开字幕")
    kind, lang, entry = chosen

    try:
        raw = download_subtitle(entry, opts)
        cues = parse_subtitle(raw or "", entry.get("ext") or "")
    except Exception as e:
        detail = str(e).strip().splitlines()
        raise ValueError(
            f"字幕下载失败：{type(e).__name__}: {detail[-1][:200] if detail else ''}") from e
    if not cues:
        raise ValueError("这个视频没有公开字幕")

    title = info.get("title") or ""
    uploader = info.get("uploader") or info.get("channel") or info.get("creator") or ""
    final_url = info.get("webpage_url") or url
    duration = info.get("duration") or cues[-1][1]
    text, truncated = cap(format_video(title, final_url, uploader, duration, lang, kind, cues), limit)
    return {"ok": True, "title": title[:300], "text": text, "truncated": truncated}


# --------------------------------------------------------------------------- #
# B站: subtitles from B站's own API, with the cookies of a throwaway account.
#
# yt-dlp with these cookies gets 403 from B站 and without them only sees danmaku, so B站
# links use its web API instead. The cookies (mounted read-only at BILIBILI_COOKIES) are
# sent to api.bilibili.com and *.hdslb.com (subtitle files) only — never to b23.tv or any
# other host — and their values are never logged.
# --------------------------------------------------------------------------- #
BILIBILI_HOSTS = ("bilibili.com", "b23.tv")
BILIBILI_API = "https://api.bilibili.com"
BILIBILI_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
}
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
BILIBILI_SUB_TRIES = 6
BILIBILI_SUB_WAIT = 0.5  # seconds between subtitle-list attempts


def is_bilibili(url) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in BILIBILI_HOSTS)


def bilibili_cookie_file():
    """Path of the mounted cookie file, or None when it isn't configured."""
    path = os.environ.get("BILIBILI_COOKIES", "").strip()
    return path if path and os.path.isfile(path) else None


def bilibili_client():
    # trust_env: picks up HTTPS_PROXY (the egress), like client().
    return httpx.Client(timeout=30, follow_redirects=False, headers=BILIBILI_HEADERS,
                        trust_env=True)


def _bilibili_cookies(jar, url):
    """The jar's cookies, but only for the hosts they are meant for."""
    host = (urlparse(url or "").hostname or "").lower()
    if host == "api.bilibili.com" or host.endswith(".hdslb.com"):
        return {c.name: c.value for c in jar}
    return {}


def _bili_json(response):
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as e:
        raise ValueError("B站接口返回的不是 JSON") from e
    if not isinstance(data, dict):
        raise ValueError("B站接口返回的不是 JSON")
    return data


def _bili_get(c, jar, url, params=None):
    return _bili_json(c.get(url, params=params, cookies=_bilibili_cookies(jar, url)))


def _bili_data(payload, what):
    if payload.get("code") not in (0, "0"):
        detail = str(payload.get("message") or "")[:100]
        raise ValueError(f"B站接口报错（{what}，code {payload.get('code')}）：{detail or '未知原因'}")
    return payload.get("data") or {}


def bilibili_page(url):
    """The ``?p=N`` page number of a B站 link (1 by default)."""
    raw = (parse_qs(urlparse(url).query).get("p") or ["1"])[0]
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def pick_bilibili_subtitle(subtitles):
    """Manual Chinese > ai-zh > any other manual > any other ai-zh variant > other ai."""
    def rank(item):
        lan = (item.get("lan") or "").strip().lower()
        if lan.startswith("zh") and not lan.startswith("ai-"):
            return 0
        if lan.startswith("ai-zh"):
            return 1
        if not lan.startswith("ai-"):
            return 2
        return 3

    usable = [s for s in subtitles or [] if isinstance(s, dict) and (s.get("subtitle_url") or "")]
    return min(usable, key=rank) if usable else None


def resolve_b23(c, url):
    """The real URL behind a b23.tv short link, resolved without cookies: b23.tv never
    gets them."""
    host = (urlparse(url or "").hostname or "").lower()
    if host != "b23.tv" and not host.endswith(".b23.tv"):
        return url
    r = c.get(url, follow_redirects=True)  # no cookies on purpose
    r.raise_for_status()
    return https(str(r.url))


def fetch_bilibili(url, limit):
    """A B站 video's subtitles through B站's own API. Raises ValueError with a
    model-readable reason when it cannot be read; fetch_video then tries yt-dlp."""
    path = bilibili_cookie_file()
    if not path:
        raise ValueError("没有配置 B 站 cookies")
    jar = MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)  # read-only: never written back
    except OSError as e:
        raise ValueError(f"读不了 B 站 cookies 文件：{type(e).__name__}") from e

    with bilibili_client() as c:
        final_url = resolve_b23(c, url)
        if not is_bilibili(final_url):
            raise ValueError(f"短链跳到了不是 B 站的网址：{urlparse(final_url).hostname or '?'}")
        m = BV_RE.search(urlparse(final_url).path)
        if not m:
            raise ValueError("这个链接里没有 BV 号（只支持 BV 号视频）")
        bvid = m.group(0)

        nav = _bili_json(c.get(f"{BILIBILI_API}/x/web-interface/nav",
                               cookies=_bilibili_cookies(jar, BILIBILI_API)))
        if not (nav.get("data") or {}).get("isLogin"):
            log("bilibili cookies: not logged in")
            raise ValueError("B站登录已过期，需要重新导出 cookies")

        view = _bili_data(_bili_get(c, jar, f"{BILIBILI_API}/x/web-interface/view",
                                    {"bvid": bvid}), "视频信息")
        pages = view.get("pages") or []
        page = bilibili_page(final_url)
        if pages:
            if page > len(pages):
                raise ValueError(f"这个视频没有第 {page} P")
            cid = (pages[page - 1] or {}).get("cid")
        elif page > 1:
            raise ValueError(f"这个视频没有第 {page} P")
        else:
            cid = view.get("cid")
        if not cid:
            raise ValueError("B站接口没有给出 cid")

        # B站 hands back an empty subtitle list on a good share of requests for a video
        # that does have one (seen 2026-11-15: 1 in 3 calls had ai-zh), so ask again a
        # few times before calling it "no subtitles".
        chosen = None
        for attempt in range(BILIBILI_SUB_TRIES):
            if attempt:
                time.sleep(BILIBILI_SUB_WAIT)
            player = _bili_data(_bili_get(c, jar, f"{BILIBILI_API}/x/player/wbi/v2",
                                          {"bvid": bvid, "cid": cid}), "字幕列表")
            chosen = pick_bilibili_subtitle((player.get("subtitle") or {}).get("subtitles"))
            if chosen:
                break
        if not chosen:
            raise ValueError("这个视频没有字幕")
        lang = (chosen.get("lan") or "").strip()
        sub_url = chosen.get("subtitle_url") or ""
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        body = _bili_json(c.get(sub_url, cookies=_bilibili_cookies(jar, sub_url)))

    cues = []
    for item in body.get("body") or []:
        text = _join_parts([str(item.get("content") or "")])
        if not text:
            continue
        start = float(item.get("from") or 0)
        cues.append((start, max(float(item.get("to") or start), start), text))
    if not cues:
        raise ValueError("这个视频没有字幕")

    kind = "auto" if lang.lower().startswith("ai-") else "manual"
    title = view.get("title") or ""
    uploader = (view.get("owner") or {}).get("name") or ""
    duration = view.get("duration") or cues[-1][1]
    text, truncated = cap(format_video(title, final_url, uploader, duration, lang, kind, cues), limit)
    return {"ok": True, "title": title[:300], "text": text, "truncated": truncated}


# --------------------------------------------------------------------------- #
# POST /read: video or page, decided by yt-dlp's own extractors
# --------------------------------------------------------------------------- #
_VIDEO_EXTRACTORS = None


def video_extractors():
    """yt-dlp's site-specific extractors, built once and cached.

    ``GenericIE`` matches every URL and is left out: it says nothing about the link
    being a video.
    """
    global _VIDEO_EXTRACTORS
    if _VIDEO_EXTRACTORS is None:
        classes = []
        for ie in yt_dlp.extractor.gen_extractor_classes():
            if ie.__name__ == "GenericIE" or ie.ie_key() == "Generic":
                continue
            classes.append(ie)
        _VIDEO_EXTRACTORS = classes
    return _VIDEO_EXTRACTORS


def looks_like_video(url) -> bool:
    """True when a site-specific extractor recognises the URL. No network involved."""
    url = (url or "").strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = "https://" + url
    url = https(url)
    for ie in video_extractors():
        try:
            if ie.suitable(url):
                return True
        except Exception:  # an extractor choking on a URL just doesn't match it
            continue
    return False


VIDEO_FALLBACK_NOTE = (
    "[This link looks like a video, but its subtitles could not be read: {reason}. "
    "Below is the page text instead.]"
)


def fetch_read(url, max_chars=None):
    """A link as whatever it is: video subtitles when yt-dlp knows the site, page otherwise.

    Output has the shape of ``/url`` plus ``kind``: ``"video"`` for subtitles, ``"page"``
    for page text — including a video link whose subtitles could not be read, whose page
    text is then prefixed with a line saying why.
    """
    url = (url or "").strip()
    if not looks_like_video(url):
        return {**fetch_url(url, max_chars), "kind": "page"}

    try:
        return {**fetch_video(url, max_chars), "kind": "video"}
    except Exception as e:
        video_error = str(e).strip() or type(e).__name__

    try:
        page = fetch_url(url, max_chars)
    except Exception as e:
        page_error = str(e).strip() or type(e).__name__
        return {"ok": False,
                "error": f"视频读取失败：{video_error}；网页读取也失败：{page_error}"}

    limit = clamp_int(max_chars, MAX_CHARS_MIN, MAX_CHARS_MAX, MAX_TEXT)
    note = VIDEO_FALLBACK_NOTE.format(reason=video_error)
    text, cut_by_note = cap(note + "\n\n" + (page.get("text") or ""), limit)
    return {
        "ok": True,
        "title": page.get("title") or "",
        "text": text,
        "truncated": bool(page.get("truncated") or cut_by_note),
        "kind": "page",
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # no access log: URLs can be personal

    def do_GET(self):
        self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BYTES:
                return self._send(413, {"ok": False, "error": "too big (> 10 MB)"})
            body = self.rfile.read(length)
            u = urlparse(self.path)
            if u.path == "/url":
                req = json.loads(body)
                max_chars = clamp_int(req.get("max_chars"), MAX_CHARS_MIN, MAX_CHARS_MAX, MAX_TEXT)
                return self._send(200, fetch_url(req.get("url") or "", max_chars))
            if u.path == "/video":
                req = json.loads(body)
                res = fetch_video(req.get("url") or "", req.get("max_chars"))
                if not res.get("ok"):
                    # Host only (no path / query): enough to spot 429s without logging links.
                    host = urlparse(req.get("url") or "").hostname or "?"
                    log(f"video {host}: {str(res.get('error'))[:200]}")
                return self._send(200, res)
            if u.path == "/read":
                req = json.loads(body)
                max_chars = clamp_int(req.get("max_chars"), MAX_CHARS_MIN, MAX_CHARS_MAX, MAX_TEXT)
                res = fetch_read(req.get("url") or "", max_chars)
                if not res.get("ok"):
                    # Host only (no path / query): enough to spot 429s without logging links.
                    host = urlparse(req.get("url") or "").hostname or "?"
                    log(f"read {host}: {str(res.get('error'))[:200]}")
                return self._send(200, res)
            if u.path == "/file":
                name = (parse_qs(u.query).get("name") or ["file"])[0][:200]
                text, truncated = cap(parse_file(name, body))
                return self._send(200, {"ok": True, "title": name, "text": text, "truncated": truncated})
            if u.path == "/rss":
                req = json.loads(body)
                return self._send(200, {"items": fetch_rss(req.get("feeds", []), int(req.get("per_feed", 5)))})
            self._send(404, {"ok": False, "error": "not found"})
        except ValueError as e:
            self._send(200, {"ok": False, "error": str(e) or "bad input"})
        except httpx.HTTPError as e:
            self._send(200, {"ok": False, "error": f"fetch failed: {type(e).__name__}"})
        except Exception as e:
            log(f"{type(e).__name__}: {e}")
            self._send(200, {"ok": False, "error": "could not read it"})


def main():
    if any(os.environ.get(k) for k in ("DISCORD_BOT_TOKEN", "DEEPSEEK_API_KEY")):
        sys.exit("[fetcher] refusing to start: secrets must not be passed to the fetcher")
    srv = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    srv.daemon_threads = True
    log(f"listening on :8080 proxy={os.environ.get('HTTPS_PROXY', '')}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
