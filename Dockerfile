# llmcord (Discord <-> DeepSeek chat bot), run via docker compose. Stage 1: chat only.
FROM python:3.13-slim

# llmcord is pinned to an exact commit and verified by sha256.
ARG LLMCORD_COMMIT=a99de6da1639f36972e6f57a4331e8d8b63b74bb
ARG LLMCORD_SHA256=7c4a870c4e8932c0e18763fe603caa174e05961478ba7404f0e7d1e2b31a33c2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LLMCORD_WORKDIR=/tmp/llmcord

# Unprivileged runtime user (no sudo, no login shell).
RUN groupadd --system --gid 10001 bot \
 && useradd --system --uid 10001 --gid bot --home-dir /home/bot --create-home --shell /usr/sbin/nologin bot

WORKDIR /opt/llmcord

COPY requirements.txt ./
RUN pip install -r requirements.txt

RUN python -c "import hashlib, sys, urllib.request; \
url = 'https://raw.githubusercontent.com/jakobdylanc/llmcord/${LLMCORD_COMMIT}/llmcord.py'; \
data = urllib.request.urlopen(url, timeout=60).read(); \
digest = hashlib.sha256(data).hexdigest(); \
sys.exit('llmcord.py sha256 mismatch: ' + digest) if digest != '${LLMCORD_SHA256}' else None; \
open('llmcord.py', 'wb').write(data)" \
 && echo "${LLMCORD_COMMIT}" > LLMCORD_COMMIT

COPY entrypoint.py scheduler.py config.template.yaml ./
COPY prompts/ ./prompts/

# App code is root-owned and read-only for the bot user. At runtime the root fs is
# read-only (compose read_only: true); config.yaml is rendered into the /tmp tmpfs.
RUN chown -R root:root /opt/llmcord && chmod -R a=rX,u+w /opt/llmcord

USER 10001:10001
WORKDIR /home/bot

ENTRYPOINT ["python", "/opt/llmcord/entrypoint.py"]
