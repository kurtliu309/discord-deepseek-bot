# Keyless MCP tools server (search / read_page / read_video). Design: docs/notes/design-v3.md §10.
# Hardened like the fetcher: non-root, read-only root fs, no capabilities, no secrets, no mounts.
# No ffmpeg and no Whisper on purpose: read_video only fetches public subtitles.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Unprivileged runtime user (no sudo, no login shell), same uid/gid as the other containers.
RUN groupadd --system --gid 10001 tools \
 && useradd --system --uid 10001 --gid tools --home-dir /home/tools --create-home --shell /usr/sbin/nologin tools

WORKDIR /opt/app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY server.py ./
# Offline unit tests ship with the image so they run the same everywhere:
#   docker run --rm ddb-tools:test python -m unittest discover -s tests
COPY tests/ ./tests/

# App code is root-owned and read-only for the runtime user; the root fs is read-only at
# runtime too and only /tmp (tmpfs) is writable.
RUN chown -R root:root /opt/app && chmod -R a=rX,u+w /opt/app

USER 10001:10001

# CMD (not ENTRYPOINT) so `docker run <image> python -m unittest ...` works as-is.
CMD ["python", "/opt/app/server.py"]
