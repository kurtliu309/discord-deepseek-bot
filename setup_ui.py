#!/usr/bin/env python3
"""``/setup``: the owner-only, ephemeral settings UI for one guild (Chinese UI).

The Discord pieces (View / Select / Button / Modal) live at the bottom; everything
above them is pure and unit-tested:

    permission_error(user_id, owner_id, has_manage_guild) -> None | error text
    default_config(guild_name)                            -> GuildConfig
    form_from_config(cfg) / apply_form(base, form)        -> editable form / validated config
    list_personas / list_styles / tools_available / tools_status

The whitelist (``allow_domains``) is intentionally not in the menu: hand-edit the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import discord

import guildcfg as g
import persona

TRUST_LABELS = {
    g.Trust.PUBLIC.value: "公共群（有外人）",
    g.Trust.OWNER.value: "自己的群（全部自己人）",
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


# --------------------------------------------------------------------------- #
# pure logic
# --------------------------------------------------------------------------- #


def permission_error(user_id, owner_id, has_manage_guild: bool):
    """Why this user may not use /setup, or ``None`` when they may."""
    if not has_manage_guild:
        return "你需要「管理服务器」权限才能用 /setup。"
    if int(user_id) != int(owner_id):
        return "只有 bot 的主人能用 /setup。"
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


def tools_status(trust) -> str:
    return "可用（owner 群）" if tools_available(trust) else "不可用（public 群不能开）"


def parse_switch(value) -> bool:
    s = str(value or "").strip().lower()
    if s in TRUE_WORDS:
        return True
    if s in FALSE_WORDS:
        return False
    raise ValueError("开关只能填「开」或「关」")


def parse_percent(value) -> float:
    """``3`` / ``3%`` -> 0.03 (accepts 0-100)."""
    s = str(value or "").strip().rstrip("%").strip()
    n = float(s)
    if n < 0 or n > 100:
        raise ValueError("概率要在 0-100 之间（百分比）")
    return n / 100.0


def parse_time(value) -> str:
    s = str(value or "").strip()
    if not g.TIME_RE.match(s):
        raise ValueError("时间要写成 HH:MM，例如 22:00")
    return s


@dataclass
class SetupForm:
    """The editable subset of one guild config shown in the menu."""

    trust: str = g.Trust.PUBLIC.value
    persona: str = "tiredfx"
    style: str = "normal"
    language: str = "en"
    replies_per_day: int = 50
    lookups_per_day: int = 0
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
        lookups_per_day=cfg.budget.lookups_per_day,
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
    data["budget"]["lookups_per_day"] = int(form.lookups_per_day)

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


# --------------------------------------------------------------------------- #
# Discord UI (thin: edit the form, save with the pure helpers above)
# --------------------------------------------------------------------------- #


class SetupView(discord.ui.View):
    """Two ephemeral pages; every change edits the same message in place."""

    def __init__(self, *, guild, base, form, store, personas_dir):
        super().__init__(timeout=600)
        self.guild = guild
        self.base = base
        self.form = form
        self.store = store
        self.personas_dir = personas_dir
        self.personas = list_personas(personas_dir) or [form.persona]
        self.styles = list_styles(personas_dir) or [form.style]
        self.page = 1
        self._build()

    # -- rendering ---------------------------------------------------------- #

    def _channel_name(self, channel_id) -> str:
        if channel_id is None:
            return "未设置"
        channel = self.guild.get_channel(int(channel_id)) if self.guild is not None else None
        return f"#{channel.name}" if channel is not None else "（频道已不存在）"

    def _channel_names(self, ids) -> str:
        return "、".join(self._channel_name(cid) for cid in ids) if ids else "无"

    def render(self) -> str:
        f = self.form
        lines = [
            "**服务器设置**（仅你可见）",
            f"群名：{self.guild.name if self.guild is not None else '?'}",
            f"信任等级：{TRUST_LABELS.get(f.trust, f.trust)}",
            f"人设：{f.persona}　风格：{f.style}　语言：{f.language}",
            f"额度：每天 {f.replies_per_day} 条回复（查询预算 {f.lookups_per_day} 次，v3.1 用）",
            f"插话：{'开' if f.interject_on else '关'}，概率 {f.interject_chance:.0%}",
            f"安静频道：{self._channel_names(f.quiet_channels)}",
            f"回顾：{'开' if f.recap_on else '关'}　{self._channel_name(f.recap_channel)}　{f.recap_time}",
            f"话题：{'开' if f.topics_on else '关'}　{self._channel_name(f.topics_channel)}　{f.topics_time}",
            f"隐私确认：{'已同意' if f.consent else '未同意'}",
            f"工具调用：{tools_status(f.trust)}",
            "",
            "第 1 页改基础项；第 2 页改频道和定时。改完点「保存」立即生效。",
        ]
        return "\n".join(lines)

    # -- page building ------------------------------------------------------ #

    def _build(self):
        self.clear_items()
        if self.page == 1:
            self._page_one()
        else:
            self._page_two()

    def _page_one(self):
        f = self.form
        trust = discord.ui.Select(
            placeholder="信任等级",
            row=0,
            options=[
                discord.SelectOption(
                    label=label, value=value, default=(value == f.trust)
                )
                for value, label in TRUST_LABELS.items()
            ],
        )
        trust.callback = self._on_trust
        self.add_item(trust)

        persona_select = discord.ui.Select(
            placeholder="人设",
            row=1,
            options=[
                discord.SelectOption(label=name, value=name, default=(name == f.persona))
                for name in self.personas[:25]
            ],
        )
        persona_select.callback = self._on_persona
        self.add_item(persona_select)

        style_select = discord.ui.Select(
            placeholder="打字风格",
            row=2,
            options=[
                discord.SelectOption(label=name, value=name, default=(name == f.style))
                for name in self.styles[:25]
            ],
        )
        style_select.callback = self._on_style
        self.add_item(style_select)

        language = discord.ui.Select(
            placeholder="默认语言",
            row=3,
            options=[
                discord.SelectOption(label=label, value=code, default=(code == f.language))
                for label, code in LANGUAGE_CHOICES
            ],
        )
        language.callback = self._on_language
        self.add_item(language)

        for label, callback in (
            ("每日额度", self._on_budget),
            ("插话设置", self._on_interject),
            ("更多设置", self._on_more),
            ("保存", self._on_save),
            ("取消", self._on_cancel),
        ):
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary if label == "保存" else discord.ButtonStyle.secondary,
                row=4,
            )
            button.callback = callback
            self.add_item(button)

    def _page_two(self):
        f = self.form
        quiet = discord.ui.ChannelSelect(
            placeholder="安静频道（可多选；我不会在这些频道说话）",
            row=0,
            min_values=0,
            max_values=25,
            channel_types=[discord.ChannelType.text, discord.ChannelType.voice,
                           discord.ChannelType.category],
        )
        quiet.callback = self._on_quiet
        self.add_item(quiet)

        recap = discord.ui.ChannelSelect(
            placeholder=f"回顾频道（当前：{self._channel_name(f.recap_channel)}）",
            row=1,
            min_values=0,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )
        recap.callback = self._on_recap_channel
        self.add_item(recap)

        topics = discord.ui.ChannelSelect(
            placeholder=f"话题频道（当前：{self._channel_name(f.topics_channel)}）",
            row=2,
            min_values=0,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )
        topics.callback = self._on_topics_channel
        self.add_item(topics)

        for label, callback in (
            ("回顾 开关/时间", self._on_recap_settings),
            ("话题 开关/时间", self._on_topics_settings),
            ("隐私确认（consent）", self._on_consent),
            ("返回", self._on_back),
        ):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=3)
            button.callback = callback
            self.add_item(button)

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
        if not persona_exists(self.personas_dir, self.form.persona):
            await interaction.response.send_message(
                f"人设 {self.form.persona} 不存在（缺 character.md），没有保存。", ephemeral=True
            )
            return
        try:
            save_form(self.store, self.guild.id, self.base, self.form)
        except g.ConfigError as e:
            await interaction.response.send_message(f"保存失败：{e}", ephemeral=True)
            return
        await interaction.response.edit_message(
            content="已保存，立即生效。\n\n" + self.render(), view=None
        )

    async def _on_cancel(self, interaction):
        await interaction.response.edit_message(content="已取消，什么都没改。", view=None)


class _FormModal(discord.ui.Modal):
    def __init__(self, view: SetupView, title: str):
        super().__init__(title=title)
        self.view = view

    async def _fail(self, interaction, message):
        await interaction.response.send_message(message, ephemeral=True)


class BudgetModal(_FormModal):
    def __init__(self, view):
        super().__init__(view, "每日额度")
        f = view.form
        self.replies = discord.ui.TextInput(
            label="每天回复条数", default=str(f.replies_per_day), max_length=5
        )
        self.lookups = discord.ui.TextInput(
            label="每天查询次数（v3.1 预留）", default=str(f.lookups_per_day),
            required=False, max_length=5,
        )
        self.add_item(self.replies)
        self.add_item(self.lookups)

    async def on_submit(self, interaction):
        try:
            replies = int(str(self.replies.value).strip())
            lookups = int(str(self.lookups.value).strip() or 0)
            if replies < 0 or lookups < 0:
                raise ValueError
        except ValueError:
            await self._fail(interaction, "额度必须是非负整数。")
            return
        self.view.form.replies_per_day = replies
        self.view.form.lookups_per_day = lookups
        await self.view._show(interaction)


class InterjectModal(_FormModal):
    def __init__(self, view):
        super().__init__(view, "插话设置")
        f = view.form
        self.switch = discord.ui.TextInput(
            label="开关（开/关）", default="开" if f.interject_on else "关", max_length=4
        )
        self.chance = discord.ui.TextInput(
            label="概率（百分比，0-100）", default=f"{f.interject_chance * 100:g}", max_length=6
        )
        self.add_item(self.switch)
        self.add_item(self.chance)

    async def on_submit(self, interaction):
        try:
            on = parse_switch(self.switch.value)
            chance = parse_percent(self.chance.value)
        except ValueError as e:
            await self._fail(interaction, str(e))
            return
        self.view.form.interject_on = on
        self.view.form.interject_chance = chance
        await self.view._show(interaction)


class _ScheduleModal(_FormModal):
    feature = ""

    def __init__(self, view, title):
        super().__init__(view, title)
        f = view.form
        on = getattr(f, f"{self.feature}_on")
        time_value = getattr(f, f"{self.feature}_time")
        self.switch = discord.ui.TextInput(
            label="开关（开/关）", default="开" if on else "关", max_length=4
        )
        self.time = discord.ui.TextInput(label="时间（HH:MM）", default=time_value, max_length=5)
        self.add_item(self.switch)
        self.add_item(self.time)

    async def on_submit(self, interaction):
        try:
            on = parse_switch(self.switch.value)
            time_value = parse_time(self.time.value)
        except ValueError as e:
            await self._fail(interaction, str(e))
            return
        setattr(self.view.form, f"{self.feature}_on", on)
        setattr(self.view.form, f"{self.feature}_time", time_value)
        await self.view._show(interaction)


class RecapModal(_ScheduleModal):
    feature = "recap"

    def __init__(self, view):
        super().__init__(view, "每晚回顾")


class TopicsModal(_ScheduleModal):
    feature = "topics"

    def __init__(self, view):
        super().__init__(view, "每日话题")


# --------------------------------------------------------------------------- #
# entry point used by bot.py
# --------------------------------------------------------------------------- #


async def start_setup(interaction, *, owner_id, store, personas_dir):
    """Validate /setup and show page 1. Never raises for a normal refusal."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "只能在服务器里用 /setup；私信不用设置。", ephemeral=True
        )
        return
    error = permission_error(interaction.user.id, owner_id, has_manage_guild(interaction))
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    store.maybe_reload()  # pick up a hand-edited config.yaml before showing the form
    base = store.get(interaction.guild.id) or default_config(interaction.guild.name)
    view = SetupView(
        guild=interaction.guild,
        base=base,
        form=form_from_config(base),
        store=store,
        personas_dir=personas_dir,
    )
    await interaction.response.send_message(view.render(), view=view, ephemeral=True)
