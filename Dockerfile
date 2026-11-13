# One image for bot.py (Discord bot), scheduler.py (daily jobs) and fetcher.py (keyless
# web/file fetcher). docker-compose.yml picks the program per service. Chat only: no tools.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Unprivileged runtime user (no sudo, no login shell).
RUN groupadd --system --gid 10001 bot \
 && useradd --system --uid 10001 --gid bot --home-dir /home/bot --create-home --shell /usr/sbin/nologin bot

WORKDIR /opt/app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY bot.py scheduler.py fetcher.py ./
# v3 modules: per-guild config, persona library, per-guild memory, /setup UI, v2 -> v3 migration.
COPY guildcfg.py persona.py guildmem.py setup_ui.py migrate_v3.py ./
COPY prompts/ ./prompts/

# App code is root-owned and read-only for the runtime user. At runtime the root fs is
# read-only too (compose read_only: true); only /tmp (tmpfs) is writable.
RUN chown -R root:root /opt/app && chmod -R a=rX,u+w /opt/app

USER 10001:10001
WORKDIR /home/bot

ENTRYPOINT ["python", "/opt/app/bot.py"]
