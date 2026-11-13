#!/usr/bin/env python3
"""Per-guild configuration for the v3 bot (see docs/notes/design-v3.md).

One YAML file per guild: ``DATA/guilds/<guild id>/config.yaml``. Everything the bot
or scheduler does in a guild is gated by that file: a per-feature switch, plus a
trust ceiling. A feature written ``on`` still does nothing when the guild's trust
level is below the feature's ``min_trust`` (``effective()``).

Pure config logic lives here; Discord wiring stays in bot.py / scheduler.py.

    store = GuildConfigStore("/data/guilds")   # scans + hot reloads
    store.maybe_reload()                       # call before handling a message
    cfg = store.get(guild_id)                  # None = not configured
    if effective(cfg, "chat"): ...
"""

from __future__ import annotations

import enum
import os
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

DM_GUILD_ID = "dm-owner"  # private messages get their own fixed "guild"

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")  # persona / style folder names


# --------------------------------------------------------------------------- #
# trust levels (ordered: add a level in _TRUST_ORDER, never special-case names)
# --------------------------------------------------------------------------- #


class Trust(str, enum.Enum):
    PUBLIC = "public"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return _TRUST_ORDER.index(self)

    def __lt__(self, other):
        return self.rank < _rank(other)

    def __le__(self, other):
        return self.rank <= _rank(other)

    def __gt__(self, other):
        return self.rank > _rank(other)

    def __ge__(self, other):
        return self.rank >= _rank(other)

    def __str__(self):
        return self.value


_TRUST_ORDER = (Trust.PUBLIC, Trust.OWNER)


def _rank(value) -> int:
    return value.rank if isinstance(value, Trust) else Trust(str(value).lower()).rank


# --------------------------------------------------------------------------- #
# small validated field types
# --------------------------------------------------------------------------- #

_TRUTHY = {"on", "true", "yes", "y", "1"}
_FALSY = {"off", "false", "no", "n", "0"}


def as_bool(value):
    """Accept YAML 1.2-ish spellings for switches: true/false, on/off, yes/no, 1/0."""
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _TRUTHY:
            return True
        if s in _FALSY:
            return False
    return value


def as_int(value):
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def check_hhmm(value):
    if not isinstance(value, str) or not TIME_RE.match(value.strip()):
        raise ValueError(f"must be HH:MM, got {value!r}")
    return value.strip()


OnOff = Annotated[bool, BeforeValidator(as_bool)]
ChannelId = Annotated[int, BeforeValidator(as_int)]
HHMM = Annotated[str, AfterValidator(check_hhmm)]
AllowDomains = Union[Literal["any"], list[str]]


# --------------------------------------------------------------------------- #
# feature settings
# --------------------------------------------------------------------------- #


class FeatureSettings(BaseModel):
    """Base for every feature block; ``chat: off`` is shorthand for ``chat: {on: false}``."""

    model_config = ConfigDict(extra="forbid")

    on: OnOff = True

    @model_validator(mode="before")
    @classmethod
    def _allow_shorthand(cls, value):
        if isinstance(value, (bool, str)):
            return {"on": value}
        return value


class ChatSettings(FeatureSettings):
    pass


class InterjectSettings(FeatureSettings):
    chance: float = Field(0.03, ge=0.0, le=1.0)
    fx_chance: float = Field(0.05, ge=0.0, le=1.0)
    burst_count: int = Field(12, ge=1)
    burst_window: int = Field(180, ge=1)  # seconds
    cooldown: int = Field(600, ge=0)  # seconds


class ReadLinksSettings(FeatureSettings):
    allow_domains: AllowDomains = "any"


class ToolsSettings(FeatureSettings):
    """v3.1, owner guilds only. The public-guild request carries no tool list at all."""

    on: OnOff = False
    allow_domains: AllowDomains = "any"


class NewsSettings(FeatureSettings):
    """v3.2. ``channel``/``time`` here; material comes from data/shared/news/."""

    on: OnOff = False
    channel: Optional[ChannelId] = None
    time: HHMM = "09:00"
    items: int = Field(5, ge=1, le=50)


class RecapSettings(FeatureSettings):
    on: OnOff = False
    channel: Optional[ChannelId] = None
    time: HHMM = "22:00"


class Feed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str


# Same sources scheduler.py used in v2 (RSS feeds + two combined Reddit feeds).
DEFAULT_FEEDS = (
    Feed(name="befores & afters", url="https://beforesandafters.com/feed/"),
    Feed(name="CG Channel", url="https://www.cgchannel.com/feed/"),
    Feed(name="80.lv", url="https://80.lv/feed/"),
    Feed(name="Simon Willison", url="https://simonwillison.net/atom/everything/"),
    Feed(name="The Verge AI", url="https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    Feed(name="Hacker News", url="https://hnrss.org/frontpage"),
    Feed(name="Show HN", url="https://hnrss.org/show"),
    Feed(name="BBC World", url="https://feeds.bbci.co.uk/news/world/rss.xml"),
    Feed(name="Guardian World", url="https://www.theguardian.com/world/rss"),
    Feed(name="ABC News AU", url="https://www.abc.net.au/news/feed/51120/rss.xml"),
    Feed(name="Sydney Morning Herald", url="https://www.smh.com.au/rss/feed.xml"),
    Feed(name="GitHub Trending", url="https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml"),
    Feed(
        name="reddit-weird",
        url="https://www.reddit.com/r/nottheonion+Showerthoughts+mildlyinfuriating"
        "+oddlyterrifying+AskReddit+NoStupidQuestions/hot/.rss?limit=40",
    ),
    Feed(
        name="reddit",
        url="https://www.reddit.com/r/popular+Houdini+vfx+LocalLLaMA/hot/.rss?limit=30",
    ),
)


class TopicsSettings(FeatureSettings):
    on: OnOff = False
    channel: Optional[ChannelId] = None
    time: HHMM = "09:00"
    count: int = Field(3, ge=1, le=10)
    feeds: list[Feed] = Field(default_factory=lambda: list(DEFAULT_FEEDS))


class Features(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat: ChatSettings = Field(default_factory=ChatSettings)
    interject: InterjectSettings = Field(default_factory=InterjectSettings)
    read_links: ReadLinksSettings = Field(default_factory=ReadLinksSettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    recap: RecapSettings = Field(default_factory=RecapSettings)
    topics: TopicsSettings = Field(default_factory=TopicsSettings)

    def get(self, name: str) -> Optional[FeatureSettings]:
        return getattr(self, name, None)


class BudgetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replies_per_day: int = Field(50, ge=0)
    lookups_per_day: int = Field(0, ge=0)  # v3.1 tool lookups; stored, not used yet
    reset_time: HHMM = "09:00"


# --------------------------------------------------------------------------- #
# guild config
# --------------------------------------------------------------------------- #


class GuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    trust: Trust = Trust.PUBLIC
    consent: OnOff = False  # the people in the guild agreed to be read and summarised
    persona: str = "tiredfx"  # data/shared/personas/<persona>/
    style: str = "normal"  # data/shared/personas/_styles/<style>.md
    language: str = "en"
    extra_prompt: str = ""
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    allowed_channels: Optional[list[ChannelId]] = None  # None = every channel
    quiet_channels: list[ChannelId] = Field(default_factory=list)  # old BLOCKED_CHANNEL_IDS
    recap_skip_channels: list[ChannelId] = Field(default_factory=list)
    features: Features = Field(default_factory=Features)

    @model_validator(mode="before")
    @classmethod
    def _allow_blank(cls, value):
        return {} if value is None else value

    @model_validator(mode="after")
    def _safe_names(self):
        for field in ("persona", "style"):
            name = getattr(self, field)
            if not NAME_RE.match(name or ""):
                raise ValueError(f"{field} must be a simple name (letters, digits, _ or -)")
        return self


# --------------------------------------------------------------------------- #
# feature registry: one row per thing the bot can do
# --------------------------------------------------------------------------- #


class FeatureSpec(tuple):
    """name, min_trust, settings model, profile blocks it reads, token budget."""

    def __new__(cls, name, min_trust, settings, profile_parts, token_budget, recent_recaps=0):
        return tuple.__new__(cls, (name, min_trust, settings, profile_parts, token_budget, recent_recaps))

    name = property(lambda self: self[0])
    min_trust = property(lambda self: self[1])
    settings = property(lambda self: self[2])
    profile_parts = property(lambda self: self[3])
    token_budget = property(lambda self: self[4])
    recent_recaps = property(lambda self: self[5])


# Ordered like the design table. Adding a feature = one row here + a settings model
# + a field on Features; /setup and validation pick it up from this dict.
FEATURES = {
    "chat": FeatureSpec("chat", Trust.PUBLIC, ChatSettings, ("kurt", "people", "jokes"), 500),
    "interject": FeatureSpec("interject", Trust.PUBLIC, InterjectSettings, ("kurt", "jokes"), 500),
    "read_links": FeatureSpec("read_links", Trust.PUBLIC, ReadLinksSettings, (), 0),
    "tools": FeatureSpec("tools", Trust.OWNER, ToolsSettings, (), 0),
    "news": FeatureSpec("news", Trust.PUBLIC, NewsSettings, ("kurt", "threads", "unfinished"), 500),
    "recap": FeatureSpec(
        "recap",
        Trust.PUBLIC,
        RecapSettings,
        ("kurt", "threads", "people", "jokes", "unfinished", "drift"),
        1500,
        3,
    ),
    "topics": FeatureSpec("topics", Trust.PUBLIC, TopicsSettings, ("kurt", "threads", "unfinished"), 500),
}

FEATURE_NAMES = tuple(FEATURES)


def settings_for(cfg: Optional[GuildConfig], feature: str) -> Optional[FeatureSettings]:
    """The feature's settings object, or None when the guild isn't configured."""
    if cfg is None or feature not in FEATURES:
        return None
    return cfg.features.get(feature)


def effective(cfg: Optional[GuildConfig], feature: str) -> bool:
    """On *and* allowed by the guild's trust level. Unconfigured guild = everything off."""
    spec = FEATURES[feature]  # KeyError on a typo: features come from the registry
    if cfg is None or cfg.trust < spec.min_trust:
        return False
    settings = cfg.features.get(feature)
    return bool(settings is not None and settings.on)


def channel_allowed(cfg: Optional[GuildConfig], channel_id) -> bool:
    """``allowed_channels: null`` means every channel."""
    if cfg is None:
        return False
    return cfg.allowed_channels is None or int(channel_id) in cfg.allowed_channels


def channel_quiet(cfg: Optional[GuildConfig], channel_id) -> bool:
    """Quiet channels (and their parent category; the caller walks up)."""
    return cfg is not None and int(channel_id) in cfg.quiet_channels


def dm_config(name: str = "direct messages") -> GuildConfig:
    """Config for the fixed ``dm-owner`` guild: owner trust, chat only, no DMs to others."""
    return GuildConfig(
        name=name,
        trust=Trust.OWNER,
        consent=True,
        features=Features(interject=InterjectSettings(on=False)),
    )


# --------------------------------------------------------------------------- #
# YAML (PyYAML is YAML 1.1: "on"/"off" are its own keys, so only true/false are bools)
# --------------------------------------------------------------------------- #


class _Loader(yaml.SafeLoader):
    pass


_Loader.yaml_implicit_resolvers = {
    first: [
        (tag, rx)
        for tag, rx in resolvers
        if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:int")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
# Only true/false are booleans ("on"/"off"/"yes"/"no" stay strings and are coerced
# by the models), so `on:` is a plain key like any other.
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)
# Plain decimal ints only: PyYAML's YAML 1.1 resolver would read "22:00" as base-60
# 1320 and "09" as octal, turning config times into numbers.
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:int", re.compile(r"^[-+]?(?:0|[1-9][0-9_]*)$"), list("-+0123456789")
)


class ConfigError(Exception):
    """config.yaml missing, unreadable, not YAML, or failed validation."""


def _log_default(message: str) -> None:
    print(f"[guildcfg] {message}", flush=True)


def parse_config(data: Any, source: str = "<config>") -> GuildConfig:
    """Validate already-parsed YAML. Raises ConfigError with the field errors."""
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: expected a mapping at the top level")
    try:
        return GuildConfig.model_validate(data)
    except ValidationError as e:
        details = "; ".join(
            f"{'.'.join(map(str, err['loc'])) or '<root>'}: {err['msg']}" for err in e.errors()
        )
        raise ConfigError(f"{source}: {details}") from None


def load_config_file(path) -> GuildConfig:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"{path}: cannot read ({e.__class__.__name__}: {e.strerror or e})") from None
    try:
        data = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML ({e})") from None
    return parse_config(data, str(path))


def dump_config(cfg: GuildConfig) -> str:
    return yaml.safe_dump(
        cfg.model_dump(mode="json"), sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def write_config(path, cfg: GuildConfig) -> Path:
    """Atomic save: write a temp file in the same folder, then rename over config.yaml."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(dump_config(cfg))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# loader with mtime hot reload
# --------------------------------------------------------------------------- #


class GuildConfigStore:
    """Scans ``guilds_dir/*/config.yaml`` and reloads changed files.

    A guild whose file fails validation is logged and treated as unconfigured
    (present in ``invalid``, absent from ``get()``). ``maybe_reload()`` is cheap:
    it only touches the disk once per ``interval`` seconds.
    """

    def __init__(self, guilds_dir, interval: float = 30.0, clock=time.monotonic, logger=None):
        self.guilds_dir = Path(guilds_dir)
        self.interval = interval
        self.clock = clock
        self.log = logger or _log_default
        self._configs: dict[str, GuildConfig] = {}
        self._invalid: dict[str, str] = {}
        self._mtimes: dict[str, int] = {}
        self._last_check = float("-inf")
        self.reload()

    # -- reads -------------------------------------------------------------- #

    @property
    def configs(self) -> dict[str, GuildConfig]:
        return dict(self._configs)

    @property
    def invalid(self) -> dict[str, str]:
        """Guild id -> validation error, for guilds that have a broken config.yaml."""
        return dict(self._invalid)

    def get(self, guild_id) -> Optional[GuildConfig]:
        return self._configs.get(str(guild_id))

    def configured(self, guild_id) -> bool:
        return str(guild_id) in self._configs

    # -- refresh ------------------------------------------------------------ #

    def maybe_reload(self) -> bool:
        """Reload at most once per ``interval`` seconds; True when the cache was re-read."""
        if self.clock() - self._last_check < self.interval:
            return False
        return self.reload()

    def reload(self) -> bool:
        self._last_check = self.clock()
        changed = False
        seen = set()
        try:
            paths = sorted(self.guilds_dir.glob("*/config.yaml"))
        except OSError:
            paths = []
        for path in paths:
            guild_id = path.parent.name
            seen.add(guild_id)
            try:
                mtime = path.stat().st_mtime_ns
            except OSError:
                continue
            if guild_id in self._configs and self._mtimes.get(guild_id) == mtime:
                continue
            if guild_id in self._invalid and self._mtimes.get(guild_id) == mtime:
                continue
            changed = True
            self._mtimes[guild_id] = mtime
            try:
                cfg = load_config_file(path)
            except ConfigError as e:
                self._configs.pop(guild_id, None)
                self._invalid[guild_id] = str(e)
                self.log(f"guild {guild_id}: config.yaml rejected, guild treated as not set up: {e}")
                continue
            self._invalid.pop(guild_id, None)
            self._configs[guild_id] = cfg
        for guild_id in list(self._configs):
            if guild_id not in seen:
                self._configs.pop(guild_id, None)
                self._mtimes.pop(guild_id, None)
                self._invalid.pop(guild_id, None)
                changed = True
        for guild_id in list(self._invalid):
            if guild_id not in seen:
                self._invalid.pop(guild_id, None)
                self._mtimes.pop(guild_id, None)
        return changed

    # -- writes ------------------------------------------------------------- #

    def save(self, guild_id, cfg: GuildConfig) -> Path:
        """Save a guild's config atomically and put it in the cache right away."""
        guild_id = str(guild_id)
        path = write_config(self.guilds_dir / guild_id / "config.yaml", cfg)
        self._configs[guild_id] = cfg
        self._invalid.pop(guild_id, None)
        try:
            self._mtimes[guild_id] = path.stat().st_mtime_ns
        except OSError:
            self._mtimes.pop(guild_id, None)
        return path
