#!/usr/bin/env python3
"""``/setup``: the owner-only, ephemeral settings UI for one guild (Chinese / English).

The Discord pieces (View / Select / Button / Modal) live at the bottom; everything
above them is pure and unit-tested:

    permission_error(user_id, owner_id, has_manage_guild, lang) -> None | error text
    default_config(guild_name)                                 -> GuildConfig
    form_from_config(cfg) / apply_form(base, form)             -> editable form / validated config
    list_personas / list_styles / tools_available / tools_status

Every user-facing string lives in ``TEXT``; ``t(lang, key, **kw)`` picks it out.
The language is the guild's ``setup_language`` ("zh" unless switched), which is
independent of ``language`` (the language the bot replies in).

The whitelist (``allow_domains``) is intentionally not in the menu: hand-edit the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import discord

import guildcfg as g
import persona

DEFAULT_LANGUAGE = "zh"

# --------------------------------------------------------------------------- #
# UI strings: one table per language, same keys in both (a test enforces that)
# --------------------------------------------------------------------------- #

TEXT = {
    "zh": {
        "title": "**服务器设置**（仅你可见）",
        "guild_name": "群名：{name}",
        "trust_line": "信任等级：{label}",
        "trust_public": "公共群（有外人）",
        "trust_owner": "自己的群（全部自己人）",
        "persona_line": "人设：{persona}　风格：{style}　语言：{language}",
        "budget_line": "额度：群每天 {replies} 条回复、每人 {replies_per_user} 条"
        "（查询：群 {lookups} 次、每人 {lookups_per_user} 次，v3.1 用）",
        "interject_line": "插话：{state}，概率 {chance}",
        "quiet_line": "安静频道：{channels}",
        "recap_line": "回顾：{state}　{channel}　{time}",
        "topics_line": "话题：{state}　{channel}　{time}",
        "consent_line": "隐私确认：{state}",
        "consent_yes": "已同意",
        "consent_no": "未同意",
        "tools_line": "工具调用：{status}",
        "tools_ok": "可用（owner 群）",
        "tools_unavailable": "不可用（public 群不能开）",
        "page_hint": "第 1 页改基础项；第 2 页改频道、定时和默认语言。改完点「保存」立即生效。",
        "on": "开",
        "off": "关",
        "none": "无",
        "unset": "未设置",
        "gone": "（频道已不存在）",
        "join": "、",
        "select_trust": "信任等级",
        "select_persona": "人设",
        "select_style": "打字风格",
        "select_language": "默认语言",
        "quiet_placeholder": "安静频道（可多选；我不会在这些频道说话）",
        "recap_placeholder": "回顾频道（当前：{channel}）",
        "topics_placeholder": "话题频道（当前：{channel}）",
        "btn_budget": "每日额度",
        "btn_interject": "插话设置",
        "btn_more": "更多设置",
        "btn_save": "保存",
        "btn_cancel": "取消",
        "btn_recap": "回顾设置",
        "btn_topics": "话题设置",
        "btn_consent": "隐私确认",
        "btn_back": "返回",
        "btn_language": "English",
        "modal_budget": "每日额度",
        "modal_interject": "插话设置",
        "modal_recap": "每晚回顾",
        "modal_topics": "每日话题",
        "field_replies": "每天回复条数（群）",
        "field_replies_per_user": "每人每天回复条数",
        "field_lookups": "每天查询次数（群，v3.1 预留）",
        "field_lookups_per_user": "每人每天查询次数（v3.1 预留）",
        "field_switch": "开关（开/关）",
        "field_chance": "概率（百分比，0-100）",
        "field_time": "时间（HH:MM）",
        "err_persona_missing": "人设 {persona} 不存在（缺 character.md），没有保存。",
        "err_save": "保存失败：{error}",
        "err_language": "切换语言失败：{error}",
        "err_budget": "额度必须是非负整数。",
        "err_switch": "开关只能填「开」或「关」",
        "err_percent": "概率要在 0-100 之间（百分比）",
        "err_time": "时间要写成 HH:MM，例如 22:00",
        "perm_no_manage": "你需要「管理服务器」权限才能用 /setup。",
        "perm_not_owner": "只有 bot 的主人能用 /setup。",
        "dm_only": "只能在服务器里用 /setup；私信不用设置。",
        "saved": "已保存，立即生效。",
        "cancelled": "已取消，什么都没改。",
    },
    "en": {
        "title": "**Server setup** (only you can see this)",
        "guild_name": "Server: {name}",
        "trust_line": "Trust level: {label}",
        "trust_public": "Public server (outsiders present)",
        "trust_owner": "Own server (everyone is trusted)",
        "persona_line": "Persona: {persona} · Style: {style} · Language: {language}",
        "budget_line": "Budget: {replies} replies per day for the server, {replies_per_user} "
        "per person (lookups: {lookups} for the server, {lookups_per_user} per person, used in v3.1)",
        "interject_line": "Interjections: {state}, chance {chance}",
        "quiet_line": "Quiet channels: {channels}",
        "recap_line": "Recap: {state} · {channel} · {time}",
        "topics_line": "Topics: {state} · {channel} · {time}",
        "consent_line": "Privacy consent: {state}",
        "consent_yes": "given",
        "consent_no": "not given",
        "tools_line": "Tool calls: {status}",
        "tools_ok": "available (owner server)",
        "tools_unavailable": "unavailable (public servers cannot enable it)",
        "page_hint": "Page 1: basics. Page 2: channels, schedules and the language. "
        "Press Save to apply.",
        "on": "on",
        "off": "off",
        "none": "none",
        "unset": "not set",
        "gone": "(channel no longer exists)",
        "join": ", ",
        "select_trust": "Trust level",
        "select_persona": "Persona",
        "select_style": "Writing style",
        "select_language": "Bot language",
        "quiet_placeholder": "Quiet channels (pick any; I won't talk in them)",
        "recap_placeholder": "Recap channel (now: {channel})",
        "topics_placeholder": "Topics channel (now: {channel})",
        "btn_budget": "Daily budget",
        "btn_interject": "Interjections",
        "btn_more": "More settings",
        "btn_save": "Save",
        "btn_cancel": "Cancel",
        "btn_recap": "Recap settings",
        "btn_topics": "Topics settings",
        "btn_consent": "Privacy consent",
        "btn_back": "Back",
        "btn_language": "中文",
        "modal_budget": "Daily budget",
        "modal_interject": "Interjections",
        "modal_recap": "Nightly recap",
        "modal_topics": "Daily topics",
        "field_replies": "Replies per day (server)",
        "field_replies_per_user": "Replies per person per day",
        "field_lookups": "Lookups per day (server, reserved for v3.1)",
        "field_lookups_per_user": "Lookups per person per day (reserved for v3.1)",
        "field_switch": "On/off",
        "field_chance": "Chance (percent, 0-100)",
        "field_time": "Time (HH:MM)",
        "err_persona_missing": "Persona {persona} not found (no character.md); nothing was saved.",
        "err_save": "Save failed: {error}",
        "err_language": "Could not switch language: {error}",
        "err_budget": "The budget must be a non-negative whole number.",
        "err_switch": "On/off must be either 开 or 关 (on/off).",
        "err_percent": "The chance must be between 0 and 100 (percent).",
        "err_time": "Time must be HH:MM, e.g. 22:00.",
        "perm_no_manage": "You need the Manage Server permission to use /setup.",
        "perm_not_owner": "Only the bot's owner can use /setup.",
        "dm_only": "/setup only works inside a server; DMs need no setup.",
        "saved": "Saved; it takes effect now.",
        "cancelled": "Cancelled; nothing was changed.",
    },
}

TRUST_KEYS = {
    g.Trust.PUBLIC.value: "trust_public",
    g.Trust.OWNER.value: "trust_owner",
}

LANGUAGE_CHOICES = [
    ("English", "en"),
    ("中文", "zh"),
    ("日本語", "ja"),
    ("한국어", "ko"),
    ("Deutsch", "de"),
    ("Français", "fr"),
    ("Español", "es"),
]

TRUE_WORDS = {"开", "开启", "on", "true", "yes", "y", "1", "是"}
FALSE_WORDS = {"关", "关闭", "off", "false", "no", "n", "0", "否"}


def t(lang: str, key: str, **kw) -> str:
    """One UI string; an unknown language falls back to Chinese (old configs)."""
    text = TEXT.get(lang, TEXT[DEFAULT_LANGUAGE])[key]
    return text.format(**kw) if kw else text


def ui_language(lang) -> str:
    """Normalise a stored value: only "zh" and "en" exist, anything else means zh."""
    return lang if lang in TEXT else DEFAULT_LANGUAGE


def other_language(lang) -> str:
    return "en" if ui_language(lang) == DEFAULT_LANGUAGE else DEFAULT_LANGUAGE


def trust_label(lang: str, trust) -> str:
    key = TRUST_KEYS.get(str(trust))
    return t(lang, key) if key else str(trust)


# --------------------------------------------------------------------------- #
# pure logic
# --------------------------------------------------------------------------- #


def permission_error(user_id, owner_id, has_manage_guild: bool, lang: str = DEFAULT_LANGUAGE):
    """Why this user may not use /setup, or ``None`` when they may."""
    if not has_manage_guild:
        return t(lang, "perm_no_manage")
    if int(user_id) != int(owner_id):
        return t(lang, "perm_not_owner")
    return None


def has_manage_guild(interaction) -> bool:
    perms = getattr(interaction, "permissions", None)
    if perms is None:
        return False
    return bool(getattr(perms, "manage_guild", False))


def default_config(guild_name: str) -> g.GuildConfig:
    """First /setup in an unconfigured guild: public, consent off, chat on."""
    return g.GuildConfig(name=guild_name or "")


def list_personas(personas_dir) -> list:
    """Persona folders (``<name>/character.md``), skipping ``_base`` and other ``_`` dirs."""
    root = Path(personas_dir)
    if not root.is_dir():
        return []
    names = []
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        if (path / persona.CHARACTER_FILE).is_file():
            names.append(path.name)
    return names


def list_styles(personas_dir) -> list:
    root = Path(personas_dir) / persona.STYLES_DIR
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.md") if not p.stem.startswith("_"))


def persona_exists(personas_dir, name: str) -> bool:
    return (Path(personas_dir) / (name or "") / persona.CHARACTER_FILE).is_file()


def tools_available(trust) -> bool:
    """Trust ceiling from the feature registry (tools need owner trust)."""
    try:
        level = trust if isinstance(trust, g.Trust) else g.Trust(str(trust).lower())
    except ValueError:
        return False
    return level >= g.FEATURES["tools"].min_trust


def tools_status(trust, lang: str = DEFAULT_LANGUAGE) -> str:
    return t(lang, "tools_ok" if tools_available(trust) else "tools_unavailable")


def parse_switch(value, lang: str = DEFAULT_LANGUAGE) -> bool:
    s = str(value or "").strip().lower()
    if s in TRUE_WORDS:
        return True
    if s in FALSE_WORDS:
        return False
    raise ValueError(t(lang, "err_switch"))


def parse_percent(value, lang: str = DEFAULT_LANGUAGE) -> float:
    """``3`` / ``3%`` -> 0.03 (accepts 0-100)."""
    s = str(value or "").strip().rstrip("%").strip()
    n = float(s)
    if n < 0 or n > 100:
        raise ValueError(t(lang, "err_percent"))
    return n / 100.0


def parse_time(value, lang: str = DEFAULT_LANGUAGE) -> str:
    s = str(value or "").strip()
    if not g.TIME_RE.match(s):
        raise ValueError(t(lang, "err_time"))
    return s


@dataclass
class SetupForm:
    """The editable subset of one guild config shown in the menu."""

    trust: str = g.Trust.PUBLIC.value
    persona: str = "tiredfx"
    style: str = "normal"
    language: str = "en"
    replies_per_day: int = 50
    replies_per_user: int = 30
    lookups_per_day: int = 0
    lookups_per_user: int = 20
    interject_on: bool = True
    interject_chance: float = 0.03
    quiet_channels: list = field(default_factory=list)
    recap_on: bool = False
    recap_channel: int | None = None
    recap_time: str = "22:00"
    topics_on: bool = False
    topics_channel: int | None = None
    topics_time: str = "09:00"
    consent: bool = False


def form_from_config(cfg: g.GuildConfig) -> SetupForm:
    return SetupForm(
        trust=str(cfg.trust),
        persona=cfg.persona,
        style=cfg.style,
        language=cfg.language,
        replies_per_day=cfg.budget.replies_per_day,
        replies_per_user=cfg.budget.replies_per_user,
        lookups_per_day=cfg.budget.lookups_per_day,
        lookups_per_user=cfg.budget.lookups_per_user,
        interject_on=bool(cfg.features.interject.on),
        interject_chance=float(cfg.features.interject.chance),
        quiet_channels=list(cfg.quiet_channels),
        recap_on=bool(cfg.features.recap.on),
        recap_channel=cfg.features.recap.channel,
        recap_time=cfg.features.recap.time,
        topics_on=bool(cfg.features.topics.on),
        topics_channel=cfg.features.topics.channel,
        topics_time=cfg.features.topics.time,
        consent=bool(cfg.consent),
    )


def apply_form(base: g.GuildConfig, form: SetupForm) -> g.GuildConfig:
    """Merge the form into ``base`` (keeping fields the menu doesn't show) and validate.

    Raises ``guildcfg.ConfigError`` on bad values. A guild that is downgraded to
    public gets ``tools`` switched off: the level ceiling would ignore it anyway,
    and the saved file should not claim otherwise.
    """
    data = base.model_dump(mode="json")
    data["trust"] = form.trust
    data["persona"] = form.persona
    data["style"] = form.style
    data["language"] = form.language
    data["consent"] = bool(form.consent)
    data["quiet_channels"] = [int(c) for c in form.quiet_channels]

    data["budget"]["replies_per_day"] = int(form.replies_per_day)
    data["budget"]["replies_per_user"] = int(form.replies_per_user)
    data["budget"]["lookups_per_day"] = int(form.lookups_per_day)
    data["budget"]["lookups_per_user"] = int(form.lookups_per_user)

    interject = data["features"]["interject"]
    interject["on"] = bool(form.interject_on)
    interject["chance"] = float(form.interject_chance)

    for name, on, channel, time_value in (
        ("recap", form.recap_on, form.recap_channel, form.recap_time),
        ("topics", form.topics_on, form.topics_channel, form.topics_time),
    ):
        block = data["features"][name]
        block["on"] = bool(on)
        block["channel"] = int(channel) if channel is not None else None
        block["time"] = time_value

    if not tools_available(form.trust):
        data["features"]["tools"]["on"] = False

    return g.parse_config(data)


def save_form(store, guild_id, base: g.GuildConfig, form: SetupForm) -> g.GuildConfig:
    """Validate + write config.yaml; the store cache updates immediately."""
    cfg = apply_form(base, form)
    store.save(guild_id, cfg)
    return cfg


def save_setup_language(store, guild_id, base: g.GuildConfig, lang: str) -> g.GuildConfig:
    """Persist only the /setup language; choices still sitting in the form stay unsaved."""
    cfg = base.model_copy(update={"setup_language": ui_language(lang)})
    store.save(guild_id, cfg)
    return cfg


# --------------------------------------------------------------------------- #
# Discord UI (thin: edit the form, save with the pure helpers above)
# --------------------------------------------------------------------------- #


class SetupView(discord.ui.View):
    """Two ephemeral pages; every change edits the same message in place.

    Discord allows five action rows per message, so page 1 is three selects plus
    a button row and the language toggle, and page 2 is four selects plus one row
    holding its buttons (the toggle included).
    """

    def __init__(self, *, guild, base, form, store, personas_dir):
        super().__init__(timeout=600)
        self.guild = guild
        self.base = base
        self.form = form
        self.store = store
        self.personas_dir = personas_dir
        self.personas = list_personas(personas_dir) or [form.persona]
        self.styles = list_styles(personas_dir) or [form.style]
        self.setup_language = ui_language(getattr(base, "setup_language", DEFAULT_LANGUAGE))
        self.page = 1
        self._build()

    # -- rendering ---------------------------------------------------------- #

    def _channel_name(self, channel_id) -> str:
        lang = self.setup_language
        if channel_id is None:
            return t(lang, "unset")
        channel = self.guild.get_channel(int(channel_id)) if self.guild is not None else None
        return f"#{channel.name}" if channel is not None else t(lang, "gone")

    def _channel_names(self, ids) -> str:
        lang = self.setup_language
        if not ids:
            return t(lang, "none")
        return t(lang, "join").join(self._channel_name(cid) for cid in ids)

    def render(self) -> str:
        f = self.form
        lang = self.setup_language

        def flag(on):
            return t(lang, "on" if on else "off")

        lines = [
            t(lang, "title"),
            t(lang, "guild_name", name=self.guild.name if self.guild is not None else "?"),
            t(lang, "trust_line", label=trust_label(lang, f.trust)),
            t(lang, "persona_line", persona=f.persona, style=f.style, language=f.language),
            t(lang, "budget_line", replies=f.replies_per_day,
              replies_per_user=f.replies_per_user, lookups=f.lookups_per_day,
              lookups_per_user=f.lookups_per_user),
            t(lang, "interject_line", state=flag(f.interject_on), chance=f"{f.interject_chance:.0%}"),
            t(lang, "quiet_line", channels=self._channel_names(f.quiet_channels)),
            t(lang, "recap_line", state=flag(f.recap_on),
              channel=self._channel_name(f.recap_channel), time=f.recap_time),
            t(lang, "topics_line", state=flag(f.topics_on),
              channel=self._channel_name(f.topics_channel), time=f.topics_time),
            t(lang, "consent_line", state=t(lang, "consent_yes" if f.consent else "consent_no")),
            t(lang, "tools_line", status=tools_status(f.trust, lang)),
            "",
            t(lang, "page_hint"),
        ]
        return "\n".join(lines)

    # -- page building ------------------------------------------------------ #

    def _build(self):
        self.clear_items()
        if self.page == 1:
            self._page_one()
        else:
            self._page_two()

    def _add_buttons(self, row, specs):
        """``specs`` = (text key, callback, primary?)"""
        for key, callback, primary in specs:
            button = discord.ui.Button(
                label=t(self.setup_language, key),
                style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary,
                row=row,
            )
            button.callback = callback
            self.add_item(button)

    def _add_language_button(self, row):
        button = discord.ui.Button(
            label=t(self.setup_language, "btn_language"),
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        button.callback = self._on_switch_language
        self.add_item(button)

    def _page_one(self):
        f = self.form
        lang = self.setup_language
        trust = discord.ui.Select(
            placeholder=t(lang, "select_trust"),
            row=0,
            options=[
                discord.SelectOption(
                    label=trust_label(lang, value), value=value, default=(value == f.trust)
                )
                for value in TRUST_KEYS
            ],
        )
        trust.callback = self._on_trust
        self.add_item(trust)

        persona_select = discord.ui.Select(
            placeholder=t(lang, "select_persona"),
            row=1,
            options=[
                discord.SelectOption(label=name, value=name, default=(name == f.persona))
                for name in self.personas[:25]
            ],
        )
        persona_select.callback = self._on_persona
        self.add_item(persona_select)

        style_select = discord.ui.Select(
            placeholder=t(lang, "select_style"),
            row=2,
            options=[
                discord.SelectOption(label=name, value=name, default=(name == f.style))
                for name in self.styles[:25]
            ],
        )
        style_select.callback = self._on_style
        self.add_item(style_select)

        self._add_buttons(
            3,
            (
                ("btn_budget", self._on_budget, False),
                ("btn_interject", self._on_interject, False),
                ("btn_more", self._on_more, False),
                ("btn_save", self._on_save, True),
                ("btn_cancel", self._on_cancel, False),
            ),
        )
        self._add_language_button(4)

    def _page_two(self):
        f = self.form
        lang = self.setup_language
        quiet = discord.ui.ChannelSelect(
            placeholder=t(lang, "quiet_placeholder"),
            row=0,
            min_values=0,
            max_values=25,
            channel_types=[discord.ChannelType.text, discord.ChannelType.voice,
                           discord.ChannelType.category],
        )
        quiet.callback = self._on_quiet
        self.add_item(quiet)

        recap = discord.ui.ChannelSelect(
            placeholder=t(lang, "recap_placeholder", channel=self._channel_name(f.recap_channel)),
            row=1,
            min_values=0,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )
        recap.callback = self._on_recap_channel
        self.add_item(recap)

        topics = discord.ui.ChannelSelect(
            placeholder=t(lang, "topics_placeholder", channel=self._channel_name(f.topics_channel)),
            row=2,
            min_values=0,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )
        topics.callback = self._on_topics_channel
        self.add_item(topics)

        language = discord.ui.Select(
            placeholder=t(lang, "select_language"),
            row=3,
            options=[
                discord.SelectOption(label=label, value=code, default=(code == f.language))
                for label, code in LANGUAGE_CHOICES
            ],
        )
        language.callback = self._on_language
        self.add_item(language)

        self._add_buttons(
            4,
            (
                ("btn_recap", self._on_recap_settings, False),
                ("btn_topics", self._on_topics_settings, False),
                ("btn_consent", self._on_consent, False),
                ("btn_back", self._on_back, False),
                ("btn_language", self._on_switch_language, False),
            ),
        )

    async def _show(self, interaction):
        self._build()
        await interaction.response.edit_message(content=self.render(), view=self)

    # -- select handlers ---------------------------------------------------- #

    async def _on_trust(self, interaction):
        self.form.trust = interaction.data["values"][0]
        await self._show(interaction)

    async def _on_persona(self, interaction):
        self.form.persona = interaction.data["values"][0]
        await self._show(interaction)

    async def _on_style(self, interaction):
        self.form.style = interaction.data["values"][0]
        await self._show(interaction)

    async def _on_language(self, interaction):
        self.form.language = interaction.data["values"][0]
        await self._show(interaction)

    async def _on_quiet(self, interaction):
        self.form.quiet_channels = [int(v) for v in interaction.data.get("values", [])]
        await self._show(interaction)

    async def _on_recap_channel(self, interaction):
        values = interaction.data.get("values", [])
        self.form.recap_channel = int(values[0]) if values else None
        await self._show(interaction)

    async def _on_topics_channel(self, interaction):
        values = interaction.data.get("values", [])
        self.form.topics_channel = int(values[0]) if values else None
        await self._show(interaction)

    # -- button handlers ---------------------------------------------------- #

    async def _on_switch_language(self, interaction):
        """Flip the guild's /setup language, save just that, redraw the same page."""
        lang = other_language(self.setup_language)
        try:
            self.base = save_setup_language(self.store, self.guild.id, self.base, lang)
        except (OSError, g.ConfigError) as e:
            await interaction.response.send_message(
                t(self.setup_language, "err_language", error=e), ephemeral=True
            )
            return
        self.setup_language = lang
        await self._show(interaction)

    async def _on_budget(self, interaction):
        await interaction.response.send_modal(BudgetModal(self))

    async def _on_interject(self, interaction):
        await interaction.response.send_modal(InterjectModal(self))

    async def _on_more(self, interaction):
        self.page = 2
        await self._show(interaction)

    async def _on_back(self, interaction):
        self.page = 1
        await self._show(interaction)

    async def _on_recap_settings(self, interaction):
        await interaction.response.send_modal(RecapModal(self))

    async def _on_topics_settings(self, interaction):
        await interaction.response.send_modal(TopicsModal(self))

    async def _on_consent(self, interaction):
        self.form.consent = not self.form.consent
        await self._show(interaction)

    async def _on_save(self, interaction):
        lang = self.setup_language
        if not persona_exists(self.personas_dir, self.form.persona):
            await interaction.response.send_message(
                t(lang, "err_persona_missing", persona=self.form.persona), ephemeral=True
            )
            return
        try:
            save_form(self.store, self.guild.id, self.base, self.form)
        except g.ConfigError as e:
            await interaction.response.send_message(t(lang, "err_save", error=e), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=t(lang, "saved") + "\n\n" + self.render(), view=None
        )

    async def _on_cancel(self, interaction):
        await interaction.response.edit_message(
            content=t(self.setup_language, "cancelled"), view=None
        )


class _FormModal(discord.ui.Modal):
    def __init__(self, view: SetupView, title: str):
        super().__init__(title=title)
        self.view = view

    async def _fail(self, interaction, message):
        await interaction.response.send_message(message, ephemeral=True)


class BudgetModal(_FormModal):
    def __init__(self, view):
        lang = view.setup_language
        super().__init__(view, t(lang, "modal_budget"))
        f = view.form
        self.replies = discord.ui.TextInput(
            label=t(lang, "field_replies"), default=str(f.replies_per_day), max_length=5
        )
        self.replies_per_user = discord.ui.TextInput(
            label=t(lang, "field_replies_per_user"),
            default=str(f.replies_per_user), required=False, max_length=5,
        )
        self.lookups = discord.ui.TextInput(
            label=t(lang, "field_lookups"), default=str(f.lookups_per_day),
            required=False, max_length=5,
        )
        self.lookups_per_user = discord.ui.TextInput(
            label=t(lang, "field_lookups_per_user"),
            default=str(f.lookups_per_user), required=False, max_length=5,
        )
        self.add_item(self.replies)
        self.add_item(self.replies_per_user)
        self.add_item(self.lookups)
        self.add_item(self.lookups_per_user)

    async def on_submit(self, interaction):
        lang = self.view.setup_language
        f = self.view.form
        try:
            replies = int(str(self.replies.value).strip())
            replies_per_user = int(str(self.replies_per_user.value).strip() or f.replies_per_user)
            lookups = int(str(self.lookups.value).strip() or 0)
            lookups_per_user = int(
                str(self.lookups_per_user.value).strip() or f.lookups_per_user
            )
            if min(replies, replies_per_user, lookups, lookups_per_user) < 0:
                raise ValueError
        except ValueError:
            await self._fail(interaction, t(lang, "err_budget"))
            return
        f.replies_per_day = replies
        f.replies_per_user = replies_per_user
        f.lookups_per_day = lookups
        f.lookups_per_user = lookups_per_user
        await self.view._show(interaction)


class InterjectModal(_FormModal):
    def __init__(self, view):
        lang = view.setup_language
        super().__init__(view, t(lang, "modal_interject"))
        f = view.form
        self.switch = discord.ui.TextInput(
            label=t(lang, "field_switch"),
            default=t(lang, "on" if f.interject_on else "off"),
            max_length=4,
        )
        self.chance = discord.ui.TextInput(
            label=t(lang, "field_chance"), default=f"{f.interject_chance * 100:g}", max_length=6
        )
        self.add_item(self.switch)
        self.add_item(self.chance)

    async def on_submit(self, interaction):
        lang = self.view.setup_language
        try:
            on = parse_switch(self.switch.value, lang)
            chance = parse_percent(self.chance.value, lang)
        except ValueError as e:
            await self._fail(interaction, str(e))
            return
        self.view.form.interject_on = on
        self.view.form.interject_chance = chance
        await self.view._show(interaction)


class _ScheduleModal(_FormModal):
    feature = ""

    def __init__(self, view, title):
        lang = view.setup_language
        super().__init__(view, title)
        f = view.form
        on = getattr(f, f"{self.feature}_on")
        time_value = getattr(f, f"{self.feature}_time")
        self.switch = discord.ui.TextInput(
            label=t(lang, "field_switch"),
            default=t(lang, "on" if on else "off"),
            max_length=4,
        )
        self.time = discord.ui.TextInput(
            label=t(lang, "field_time"), default=time_value, max_length=5
        )
        self.add_item(self.switch)
        self.add_item(self.time)

    async def on_submit(self, interaction):
        lang = self.view.setup_language
        try:
            on = parse_switch(self.switch.value, lang)
            time_value = parse_time(self.time.value, lang)
        except ValueError as e:
            await self._fail(interaction, str(e))
            return
        setattr(self.view.form, f"{self.feature}_on", on)
        setattr(self.view.form, f"{self.feature}_time", time_value)
        await self.view._show(interaction)


class RecapModal(_ScheduleModal):
    feature = "recap"

    def __init__(self, view):
        super().__init__(view, t(view.setup_language, "modal_recap"))


class TopicsModal(_ScheduleModal):
    feature = "topics"

    def __init__(self, view):
        super().__init__(view, t(view.setup_language, "modal_topics"))


# --------------------------------------------------------------------------- #
# entry point used by bot.py
# --------------------------------------------------------------------------- #


async def start_setup(interaction, *, owner_id, store, personas_dir):
    """Validate /setup and show page 1. Never raises for a normal refusal."""
    if interaction.guild is None:
        await interaction.response.send_message(t(DEFAULT_LANGUAGE, "dm_only"), ephemeral=True)
        return

    store.maybe_reload()  # pick up a hand-edited config.yaml before showing the form
    base = store.get(interaction.guild.id) or default_config(interaction.guild.name)
    # Refusals speak the guild's own /setup language; an unconfigured guild gets zh.
    error = permission_error(
        interaction.user.id, owner_id, has_manage_guild(interaction),
        ui_language(getattr(base, "setup_language", DEFAULT_LANGUAGE)),
    )
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    view = SetupView(
        guild=interaction.guild,
        base=base,
        form=form_from_config(base),
        store=store,
        personas_dir=personas_dir,
    )
    await interaction.response.send_message(view.render(), view=view, ephemeral=True)
