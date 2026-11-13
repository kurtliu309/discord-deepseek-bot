"""Slash commands: synced into a guild the moment the bot joins it; stale globals cleared."""

import unittest
from types import SimpleNamespace
from unittest import mock

import bot


class TestCommandSync(unittest.IsolatedAsyncioTestCase):
    def _bot(self):
        b = object.__new__(bot.Bot)
        b.tree = mock.MagicMock()
        b.tree.sync = mock.AsyncMock()
        b.store = mock.MagicMock()
        b.store.get.return_value = None
        b._notify_unconfigured = mock.AsyncMock(return_value=True)
        return b

    async def test_join_syncs_commands_into_the_new_guild(self):
        b = self._bot()
        guild = SimpleNamespace(id=42)
        await b.on_guild_join(guild)
        b.tree.copy_global_to.assert_called_once_with(guild=guild)
        b.tree.sync.assert_awaited_once_with(guild=guild)
        b._notify_unconfigured.assert_awaited_once_with(guild)

    async def test_sync_failure_does_not_stop_the_unconfigured_notice(self):
        b = self._bot()
        b.tree.sync.side_effect = RuntimeError("discord down")
        await b.on_guild_join(SimpleNamespace(id=42))
        b._notify_unconfigured.assert_awaited_once()

    async def test_clear_global_commands_sends_an_empty_list(self):
        b = self._bot()
        b._connection = SimpleNamespace(application_id=7)
        b.http = mock.MagicMock()
        b.http.bulk_upsert_global_commands = mock.AsyncMock()
        with mock.patch.object(bot.Bot, "application_id", new_callable=mock.PropertyMock, return_value=7):
            await b._clear_global_commands()
        b.http.bulk_upsert_global_commands.assert_awaited_once_with(7, payload=[])
