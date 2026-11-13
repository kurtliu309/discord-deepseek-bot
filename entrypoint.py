#!/usr/bin/env python3
"""Render llmcord's config.yaml from config.template.yaml + env vars, then run llmcord.

Secrets (DISCORD_BOT_TOKEN, DISCORD_CLIENT_ID, DEEPSEEK_API_KEY) are only checked
for presence here; they are never written into config.yaml. The rendered config
keeps "*_env" references and llmcord resolves them from the environment itself.

Egress: all outbound traffic must go through the allowlist proxy in $HTTPS_PROXY.
openai/httpx pick the proxy up from the environment (trust_env); discord.py (aiohttp)
does not, so before running the unmodified, sha256-pinned llmcord.py we patch
discord.Client.__init__ to default proxy=$HTTPS_PROXY (REST + gateway websocket).

Usage:
  entrypoint.py                      render to $LLMCORD_WORKDIR/config.yaml and run llmcord
  entrypoint.py --render-only [PATH] render (default: stdout) and exit; no secrets required
"""

import os
import re
import runpy
import sys

import yaml

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.environ.get("LLMCORD_TEMPLATE", os.path.join(APP_DIR, "config.template.yaml"))
LLMCORD_PATH = os.environ.get("LLMCORD_PATH", os.path.join(APP_DIR, "llmcord.py"))
WORKDIR = os.environ.get("LLMCORD_WORKDIR", os.path.expanduser("~"))

REQUIRED_SECRETS = ("DISCORD_BOT_TOKEN", "DISCORD_CLIENT_ID", "DEEPSEEK_API_KEY")
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_SYSTEM_PROMPT_FILE = os.path.join(APP_DIR, "prompts", "default.txt")
FALLBACK_SYSTEM_PROMPT = (
    "You are a casual, funny Discord chat buddy. Today's date is {date}. "
    "User messages are prefixed with their Discord ID as <@ID>."
)
FORBIDDEN_MODEL_KEYS = ("tools", "tool_choice", "functions", "function_call", "parallel_tool_calls")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


class ConfigError(Exception):
    pass


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool) -> bool:
    raw = env(name)
    if raw == "":
        return default
    if raw.lower() in ("1", "true", "yes", "on"):
        return True
    if raw.lower() in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true/false, got {raw!r}")


def env_ids(name: str) -> list[int]:
    """Comma/space separated Discord snowflake IDs -> list[int] (llmcord compares ints)."""
    ids = []
    for part in re.split(r"[,\s]+", env(name)):
        if not part:
            continue
        if not part.isdigit() or not 15 <= len(part) <= 21:
            raise ConfigError(f"{name}: {part!r} is not a valid Discord ID (digits only)")
        ids.append(int(part))
    return ids


def system_prompt() -> str:
    """SYSTEM_PROMPT (env, "\\n" -> newline) > SYSTEM_PROMPT_FILE > prompts/default.txt."""
    inline = os.environ.get("SYSTEM_PROMPT", "").strip()
    if inline:
        return inline.replace("\\n", "\n")
    path = env("SYSTEM_PROMPT_FILE") or DEFAULT_SYSTEM_PROMPT_FILE
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError as e:
        if env("SYSTEM_PROMPT_FILE"):
            raise ConfigError(f"SYSTEM_PROMPT_FILE unreadable: {e}")
        text = ""
    return text or FALLBACK_SYSTEM_PROMPT


def proxy_url(require: bool) -> str:
    url = env("HTTPS_PROXY") or env("https_proxy")
    if require and not url:
        raise ConfigError("HTTPS_PROXY is not set: the bot may only reach the internet through the egress proxy")
    if url and not re.match(r"^http://[A-Za-z0-9.-]+:\d+/?$", url):
        raise ConfigError(f"HTTPS_PROXY must look like http://egress:3128, got {url!r}")
    return url


def run_llmcord(proxy: str) -> None:
    import discord

    original_init = discord.Client.__init__

    def init_with_proxy(self, *args, **kwargs):
        kwargs.setdefault("proxy", proxy)
        original_init(self, *args, **kwargs)

    discord.Client.__init__ = init_with_proxy  # commands.Bot subclasses Client
    sys.argv = [LLMCORD_PATH]
    runpy.run_path(LLMCORD_PATH, run_name="__main__")


def render(require_secrets: bool = True) -> dict:
    if require_secrets:
        missing = [name for name in REQUIRED_SECRETS if not env(name)]
        if missing:
            raise ConfigError("missing required secrets: " + ", ".join(missing) + " (fill them in .env)")
        if not env("DISCORD_CLIENT_ID").isdigit():
            raise ConfigError("DISCORD_CLIENT_ID must be numeric (the Application ID)")

    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    allowed_users = env_ids("ALLOWED_USER_IDS")
    allowed_channels = env_ids("ALLOWED_CHANNEL_IDS")
    admins = env_ids("ADMIN_USER_IDS")
    allow_dms = env_bool("ALLOW_DMS", False)
    allow_public = env_bool("ALLOW_PUBLIC", False)

    # Fail closed. In llmcord an empty allowed_ids list means "allow everyone".
    if not allow_public:
        if not allowed_users and not allowed_channels:
            raise ConfigError(
                "ALLOWED_USER_IDS and ALLOWED_CHANNEL_IDS are both empty, which llmcord treats as "
                "'anyone in any server/channel may use the bot'. Set at least one of them, "
                "or set ALLOW_PUBLIC=true if you really want an open bot."
            )
        if allow_dms and not allowed_users:
            raise ConfigError(
                "ALLOW_DMS=true with empty ALLOWED_USER_IDS would let ANY Discord user DM the bot "
                "(channel restrictions do not apply to DMs). Set ALLOWED_USER_IDS or ALLOW_PUBLIC=true."
            )

    perms = config["permissions"]
    perms["users"]["allowed_ids"] = allowed_users
    perms["users"]["admin_ids"] = admins
    perms["channels"]["allowed_ids"] = allowed_channels

    model = env("DEEPSEEK_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    if not MODEL_RE.match(model):
        raise ConfigError(f"DEEPSEEK_MODEL has invalid characters: {model!r}")
    config["models"] = {f"deepseek/{model}": None}

    config["system_prompt"] = system_prompt()
    config["allow_dms"] = allow_dms
    config["use_plain_responses"] = env_bool("USE_PLAIN_RESPONSES", bool(config.get("use_plain_responses", False)))
    config["status_message"] = env("STATUS_MESSAGE") or None

    # Stage 1 guard: chat only, no tool / function calling anywhere.
    for name, params in (config.get("models") or {}).items():
        bad = [k for k in FORBIDDEN_MODEL_KEYS if k in (params or {})]
        if bad:
            raise ConfigError(f"model {name} has forbidden keys {bad} (stage 1 is chat only)")
    for name, provider in (config.get("providers") or {}).items():
        body = provider.get("extra_body") or {}
        if any(k in body for k in FORBIDDEN_MODEL_KEYS):
            raise ConfigError(f"provider {name} extra_body contains tool settings (stage 1 is chat only)")
        if any(k in provider for k in ("api_key", "bot_token")):
            raise ConfigError(f"provider {name}: put secrets in env and reference them with *_env keys")
    if "bot_token" in config:
        raise ConfigError("bot_token must not be written in the template; use bot_token_env")

    return config


def dump(config: dict) -> str:
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False, default_flow_style=False)


def summary(config: dict) -> str:
    p = config["permissions"]
    return (
        f"model={next(iter(config['models']))} "
        f"allowed_users={len(p['users']['allowed_ids'])} admins={len(p['users']['admin_ids'])} "
        f"allowed_channels={len(p['channels']['allowed_ids'])} allow_dms={config['allow_dms']}"
    )


def main(argv: list[str]) -> int:
    render_only = "--render-only" in argv
    try:
        config = render(require_secrets=not render_only)
        proxy = proxy_url(require=not render_only)
    except (ConfigError, OSError, yaml.YAMLError, KeyError, TypeError) as e:
        print(f"[entrypoint] config error: {e}", file=sys.stderr)
        return 2

    text = dump(config)

    if render_only:
        rest = [a for a in argv if a != "--render-only"]
        if rest:
            with open(rest[0], "w", encoding="utf-8") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
        return 0

    os.makedirs(WORKDIR, exist_ok=True)
    os.chdir(WORKDIR)  # llmcord reads ./config.yaml (and re-reads it on every message)
    fd = os.open("config.yaml", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"[entrypoint] config.yaml rendered: {summary(config)} proxy={proxy}", flush=True)
    run_llmcord(proxy)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
