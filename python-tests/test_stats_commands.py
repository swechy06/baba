"""Validate real command payloads and lifecycle while replacing Discord HTTP only."""
import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import discord


class CommandRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        path = Path(__file__).resolve().parents[1] / 'ticket_system_main_v42.py'
        spec = importlib.util.spec_from_file_location('ticket_commands_test', path)
        self.module = importlib.util.module_from_spec(spec)
        with mock.patch('dotenv.load_dotenv', return_value=False):
            spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.module.STATS_FILE = str(Path(self.tmp.name) / 'ticket_stats.json')
        self.module.DATA_FILE = str(Path(self.tmp.name) / 'ticket_data.json')
        self.bot = self.module.bot
        self.bot._connection.application_id = 999
        self.guild = discord.Object(id=10)
        self.other_guild = discord.Object(id=20)
        self.expected_names = {
            'biletsisteminikur', 'ticketsistemdogrulama',
            'biletsistemisifirla', 'yetkiliistatistik',
        }

    @staticmethod
    def response(application_id, guild_id=None, *, payload):
        return [dict(item, id=str(100 + index), application_id=str(application_id),
                     **({'guild_id': str(guild_id)} if guild_id else {}))
                for index, item in enumerate(payload)]

    @staticmethod
    def error():
        return discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'),
                                 'Missing applications.commands scope')

    async def test_real_guild_payload_contains_statistics_and_optional_member(self):
        with mock.patch.object(self.bot.http, 'bulk_upsert_guild_commands',
                               mock.AsyncMock(side_effect=self.response)) as api, \
                mock.patch('builtins.print') as output:
            await self.bot.sync_guild_commands(self.guild)
        api.assert_awaited_once()
        self.assertEqual(api.call_args.args, (999, 10))
        payload = api.call_args.kwargs['payload']
        self.assertEqual({cmd['name'] for cmd in payload}, self.expected_names)
        statistics = next(cmd for cmd in payload if cmd['name'] == 'yetkiliistatistik')
        member = next(option for option in statistics['options'] if option['name'] == 'yetkili')
        self.assertEqual(member['type'], 6)
        self.assertFalse(member.get('required', False))
        self.assertIn('/yetkiliistatistik', output.call_args.args[0])
        self.assertEqual({cmd.name for cmd in self.bot.tree.get_commands()}, self.expected_names)

    async def test_concurrent_calls_register_same_guild_only_once(self):
        with mock.patch.object(self.bot.http, 'bulk_upsert_guild_commands',
                               mock.AsyncMock(side_effect=self.response)) as api, \
                mock.patch('builtins.print'):
            await asyncio.gather(*(self.bot.sync_guild_commands(self.guild) for _ in range(3)))
        api.assert_awaited_once()

    async def test_failed_registration_can_retry_and_does_not_poison_other_guild(self):
        with mock.patch.object(self.bot.http, 'bulk_upsert_guild_commands',
                               mock.AsyncMock(side_effect=self.error())):
            with self.assertRaises(discord.Forbidden):
                await self.bot.sync_guild_commands(self.guild)
        self.assertNotIn(10, self.bot._synced_command_guilds)
        with mock.patch.object(self.bot.http, 'bulk_upsert_guild_commands',
                               mock.AsyncMock(side_effect=self.response)) as api, \
                mock.patch('builtins.print'):
            await self.bot.sync_guild_commands(self.other_guild)
            await self.bot.sync_guild_commands(self.guild)
        self.assertEqual(api.await_count, 2)
        self.assertEqual(self.bot._synced_command_guilds, {10, 20})

    async def test_ready_continues_other_guild_and_panel_restore_then_retries_failed_guild(self):
        attempts = []

        def api_response(app, guild, *, payload):
            attempts.append(guild)
            if attempts == [10]:
                raise self.error()
            return self.response(app, guild, payload=payload)

        with mock.patch.object(type(self.bot), 'guilds', new_callable=mock.PropertyMock,
                               return_value=[self.guild, self.other_guild]), \
                mock.patch.object(self.bot.http, 'bulk_upsert_guild_commands',
                                  mock.AsyncMock(side_effect=api_response)), \
                mock.patch.object(self.module, 'restore_existing_ticket_panels', mock.AsyncMock()) as restore, \
                mock.patch('builtins.print'), self.assertLogs('ticket.commands', level='ERROR'):
            await self.module.on_ready()
            await self.module.on_ready()
        self.assertEqual(attempts, [10, 20, 10])
        self.assertEqual(restore.await_count, 2)
        self.assertEqual(self.bot._synced_command_guilds, {10, 20})

    async def test_new_guild_and_rejoin_are_registered(self):
        with mock.patch.object(self.bot.http, 'bulk_upsert_guild_commands',
                               mock.AsyncMock(side_effect=self.response)) as api, \
                mock.patch('builtins.print'):
            await self.module.on_guild_join(self.guild)
            await self.module.on_guild_remove(self.guild)
            await self.module.on_guild_join(self.guild)
        self.assertEqual(api.await_count, 2)
        self.assertIn(10, self.bot._synced_command_guilds)

    async def test_failed_join_reports_permission_problem_without_crashing_bot(self):
        with mock.patch.object(self.bot.http, 'bulk_upsert_guild_commands',
                               mock.AsyncMock(side_effect=self.error())), \
                self.assertLogs('ticket.commands', level='ERROR') as logs:
            await self.module.on_guild_join(self.guild)
        self.assertTrue(any('applications.commands' in message for message in logs.output))
        self.assertNotIn(10, self.bot._synced_command_guilds)

    async def test_global_http_failure_preserves_views_and_allows_guild_registration(self):
        with mock.patch.object(self.bot.http, 'bulk_upsert_global_commands',
                               mock.AsyncMock(side_effect=self.error())), \
                mock.patch.object(self.bot, 'add_view') as views, \
                self.assertLogs('ticket.commands', level='ERROR'):
            await self.bot.setup_hook()
        self.assertEqual(views.call_count, 2)
        with mock.patch.object(self.bot.http, 'bulk_upsert_guild_commands',
                               mock.AsyncMock(side_effect=self.response)) as api, \
                mock.patch('builtins.print'):
            await self.bot.sync_guild_commands(self.guild)
        api.assert_awaited_once()
        self.assertEqual({cmd['name'] for cmd in api.call_args.kwargs['payload']}, self.expected_names)


if __name__ == '__main__':
    unittest.main()
