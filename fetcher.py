#!/usr/bin/env python3
"""Keyless fetcher: the only component that reads arbitrary web pages and parses files.

It holds NO secrets. bot / scheduler hand it a URL or file bytes over the internal network
and get plain text back. All its outbound traffic goes through the egress proxy, which lets
it reach any public host on 443 but still blocks private / loopback / link-local ranges.
Parsers (pdf, docx, xlsx, pptx) run only here, so a malicious file cannot reach a secret.

Endpoints (JSON in / JSON out, listen on :8080):
  POST /url              {"url": str}                      -> {"ok", "title", "text", "truncated"} | {"ok": false, "error"}
  POST /file?name=<fn>   raw bytes (<= 10 MB)               -> same shape
  POST /rss              {"feeds": [{"name","url"}], "per_feed": int}   -> {"items": [{"source","title","link","summary"}]}
  POST /reddit           {"subs": [str], "per_sub": int}    -> {"items": [...]}  (NSFW posts skipped)
  GET  /health                                              -> {"ok": true}
"""

import csv
import io
import json
import os
import re
import sys
import zipfile
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

MAX_BYTES = 10 * 1024 * 1024
MAX_UNZIPPED = 50 * 1024 * 1024
MAX_TEXT = int(os.environ.get("FETCH_MAX_TEXT", "30000"))
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


def cap(text):
    text = text.strip()
    return (text[:MAX_TEXT], True) if len(text) > MAX_TEXT else (text, False)


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


def download(url):
    if not re.match(r"^https?://", url):
        raise ValueError("only http(s) urls")
    with client() as c, c.stream("GET", url) as r:
        r.raise_for_status()
        if int(r.headers.get("content-length") or 0) > MAX_BYTES:
            raise ValueError("too big (> 10 MB)")
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > MAX_BYTES:
                raise ValueError("too big (> 10 MB)")
        return bytes(buf), r.headers.get("content-type", ""), str(r.url)


def fetch_url(url):
    url = github_raw(url.strip())
    data, ctype, final = download(url)
    name = urlparse(final).path.rsplit("/", 1)[-1] or "page"
    if "html" in ctype:
        title, text = html_to_text(data.decode("utf-8", errors="replace"))
    else:
        if "pdf" in ctype and not name.lower().endswith(".pdf"):
            name += ".pdf"
        title, text = name, parse_file(name if "." in name else name + ".txt", data)
    text, truncated = cap(text)
    return {"ok": True, "title": title[:300], "text": text, "truncated": truncated}


def fetch_rss(feeds, per_feed):
    import feedparser
    items = []
    for f in feeds:
        try:
            data, _, _ = download(f["url"])
            parsed = feedparser.parse(data)
            for e in parsed.entries[:per_feed]:
                summary = html_to_text(e.get("summary", ""))[1][:300]
                items.append({"source": f["name"], "title": e.get("title", "")[:300],
                              "link": e.get("link", ""), "summary": summary})
        except Exception as ex:
            log(f"rss {f.get('name')}: {type(ex).__name__}: {ex}")
    return items


def fetch_reddit(subs, per_sub):
    items = []
    for sub in subs:
        if not re.match(r"^[A-Za-z0-9_]{2,30}$", sub):
            continue
        try:
            data, _, _ = download(f"https://www.reddit.com/r/{sub}/hot.json?limit={per_sub + 5}&raw_json=1")
            for child in json.loads(data)["data"]["children"]:
                d = child["data"]
                if d.get("over_18") or d.get("stickied"):
                    continue
                items.append({"source": f"r/{sub}", "title": d.get("title", "")[:300],
                              "link": "https://www.reddit.com" + d.get("permalink", ""),
                              "summary": (d.get("selftext") or "")[:300]})
                if sum(1 for i in items if i["source"] == f"r/{sub}") >= per_sub:
                    break
        except Exception as ex:
            log(f"reddit {sub}: {type(ex).__name__}: {ex}")
    return items


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
                return self._send(200, fetch_url(json.loads(body)["url"]))
            if u.path == "/file":
                name = (parse_qs(u.query).get("name") or ["file"])[0][:200]
                text, truncated = cap(parse_file(name, body))
                return self._send(200, {"ok": True, "title": name, "text": text, "truncated": truncated})
            if u.path == "/rss":
                req = json.loads(body)
                return self._send(200, {"items": fetch_rss(req.get("feeds", []), int(req.get("per_feed", 5)))})
            if u.path == "/reddit":
                req = json.loads(body)
                return self._send(200, {"items": fetch_reddit(req.get("subs", []), int(req.get("per_sub", 5)))})
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
