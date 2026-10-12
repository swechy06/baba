"""Exercise ticket creation limits with real storage and mocked Discord I/O."""
import asyncio
import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import discord


MODULE_PATH = Path(__file__).resolve().parents[1] / "ticket_system_main_v42.py"


async def history(items):
    for item in items:
        yield item


class TicketCreationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.module = self.import_bot()
        self.guild = mock.MagicMock(spec=discord.Guild)
        self.guild.id = 10
        self.guild.default_role = mock.Mock(id=10)
        self.guild.me = mock.Mock(id=999)
        self.guild.text_channels = []
        self.channels = {}
        self.created = {}
        self.category = mock.MagicMock(spec=discord.CategoryChannel)
        self.category.id = 50
        self.channels[50] = self.category
        self.guild.get_channel.side_effect = self.channels.get
        self.guild.get_role.return_value = None
        self.guild.fetch_channel = mock.AsyncMock(side_effect=self.fetch_channel)
        self.guild.create_text_channel = mock.AsyncMock(side_effect=self.create_channel)
        self.user = mock.Mock(spec=discord.Member)
        self.user.id = 30
        self.user.mention = "<@30>"
        self.module.save_data({"10": {"support_types": {
            kind: {"open_category": 50, "closed_category": None, "roles": []}
            for kind in ("oyun_destek", "moderator")
        }}})

    def import_bot(self):
        spec = importlib.util.spec_from_file_location("ticket_creation_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        with mock.patch("dotenv.load_dotenv", return_value=False):
            spec.loader.exec_module(module)
        module.STATS_FILE = str(Path(self.tmp.name) / "ticket_stats.json")
        module.DATA_FILE = str(Path(self.tmp.name) / "ticket_data.json")
        module.bot._connection.user = SimpleNamespace(id=999, bot=True)
        return module

    def interaction(self, user=None):
        return SimpleNamespace(
            guild=self.guild, user=user or self.user,
            response=SimpleNamespace(defer=mock.AsyncMock()),
            followup=SimpleNamespace(send=mock.AsyncMock()),
        )

    def channel(self, channel_id, topic="oyun_destek|Yardım gerekiyor"):
        channel = mock.MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.guild = self.guild
        channel.name = "oyun-destek-0001"
        channel.mention = f"<#{channel_id}>"
        channel.topic = topic
        channel.created_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
        channel.send = mock.AsyncMock(return_value=SimpleNamespace(id=channel_id + 5000))
        channel.history.side_effect = lambda **kwargs: history([])
        channel.overwrites_for.return_value = discord.PermissionOverwrite()
        return channel

    async def create_channel(self, **kwargs):
        # Yield just as a real API request would, exposing check/create races.
        await asyncio.sleep(0)
        channel = self.channel(1000 + len(self.created), kwargs["topic"])
        self.created[channel.id] = channel
        self.channels[channel.id] = channel
        self.guild.text_channels.append(channel)
        return channel

    async def fetch_channel(self, channel_id):
        if channel_id in self.created:
            return self.created[channel_id]
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Deleted channel")

    async def open_first(self):
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "oyun_destek", "Yardım gerekiyor")
        self.assertIn("✅ Ticket oluşturuldu", interaction.followup.send.call_args.args[0])
        return next(iter(self.created.values()))

    async def test_first_ticket_is_created_and_persisted_with_owner(self):
        channel = await self.open_first()
        record = self.module.stats_get_ticket(10, channel.id)
        self.assertEqual(record["owner_id"], 30)
        self.assertIsNone(record["closed_at"])
        self.assertEqual(record["panel_message_id"], channel.id + 5000)
        self.guild.create_text_channel.assert_awaited_once()

    async def test_open_ticket_blocks_every_other_support_type(self):
        channel = await self.open_first()
        for kind in ("oyun_destek", "moderator"):
            interaction = self.interaction()
            await self.module.create_ticket(interaction, kind, "Başka bir sebep")
            self.assertIn(channel.mention, interaction.followup.send.call_args.args[0])
            self.assertIn("kapatılmasını beklemelisin", interaction.followup.send.call_args.args[0])
            self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        self.guild.create_text_channel.assert_awaited_once()

    async def test_simultaneous_submissions_create_only_one_channel(self):
        interactions = [self.interaction() for _ in range(4)]
        await asyncio.gather(*(
            self.module.create_ticket(item, kind, "Talep")
            for item, kind in zip(interactions, ("oyun_destek", "moderator") * 2)
        ))
        self.guild.create_text_channel.assert_awaited_once()
        replies = [item.followup.send.call_args.args[0] for item in interactions]
        self.assertEqual(sum("✅ Ticket oluşturuldu" in reply for reply in replies), 1)
        self.assertEqual(sum("Zaten açık" in reply for reply in replies), 3)

    async def test_restart_retains_open_ticket_limit(self):
        await self.open_first()
        self.module = self.import_bot()
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "moderator")
        self.assertIn("Zaten açık", interaction.followup.send.call_args.args[0])
        self.guild.create_text_channel.assert_awaited_once()

    async def test_successful_close_allows_another_ticket_even_if_old_channel_remains(self):
        channel = await self.open_first()
        self.module.stats_close_ticket(10, channel.id, 40)
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "moderator")
        self.assertEqual(self.guild.create_text_channel.await_count, 2)
        self.assertIn("✅ Ticket oluşturuldu", interaction.followup.send.call_args.args[0])

    async def test_category_move_does_not_release_open_ticket(self):
        channel = await self.open_first()
        channel.category_id = 999
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "moderator")
        self.guild.create_text_channel.assert_awaited_once()
        self.assertIn("Zaten açık", interaction.followup.send.call_args.args[0])

    async def test_solved_status_does_not_release_ticket_until_closed(self):
        channel = await self.open_first()
        panel = SimpleNamespace(
            id=channel.id + 5000, author=SimpleNamespace(id=999),
            embeds=[discord.Embed(title="🎫 Destek Talebi #0001")], edit=mock.AsyncMock(),
        )
        panel.embeds[0].add_field(
            name="📌 Ticket Bilgileri",
            value="**📊 Destek Durumu:** 🟢 İnceleniyor\n**👤 Biletle İlgilenen Yetkili:** N/A",
        )
        channel.fetch_message = mock.AsyncMock(return_value=panel)
        await self.module.update_ticket_panel(channel, status="✅ Çözüldü")
        self.assertIn("✅ Çözüldü", panel.edit.call_args.kwargs["embed"].fields[0].value)
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "moderator")
        self.guild.create_text_channel.assert_awaited_once()
        self.assertIn("Zaten açık", interaction.followup.send.call_args.args[0])

    async def test_different_users_can_each_open_a_ticket(self):
        await self.open_first()
        other_user = mock.Mock(spec=discord.Member)
        other_user.id = 31
        other_user.mention = "<@31>"
        interaction = self.interaction(other_user)
        await self.module.create_ticket(interaction, "moderator")
        self.assertEqual(self.guild.create_text_channel.await_count, 2)
        self.assertIn("✅ Ticket oluşturuldu", interaction.followup.send.call_args.args[0])

    async def test_other_guilds_records_do_not_block_current_guild(self):
        self.module.stats_register_ticket(
            20, 2000, ticket_type="oyun_destek", ticket_number=1,
            owner_id=30, opened_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
        )
        await self.open_first()

    async def test_deleted_ticket_channel_does_not_permanently_block_owner(self):
        channel = await self.open_first()
        self.channels.pop(channel.id)
        self.created.pop(channel.id)
        self.guild.text_channels.remove(channel)
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "moderator")
        self.guild.fetch_channel.assert_awaited_once_with(channel.id)
        self.assertEqual(self.guild.create_text_channel.await_count, 2)
        self.assertIn("✅ Ticket oluşturuldu", interaction.followup.send.call_args.args[0])

    async def test_gateway_cache_lag_does_not_allow_second_ticket(self):
        channel = await self.open_first()
        self.channels.pop(channel.id)
        self.guild.text_channels.clear()
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "moderator")
        self.guild.fetch_channel.assert_awaited_once_with(channel.id)
        self.guild.create_text_channel.assert_awaited_once()
        self.assertIn("Zaten açık", interaction.followup.send.call_args.args[0])

    async def test_unverifiable_channel_fails_closed_without_creating_ticket(self):
        channel = await self.open_first()
        self.channels.pop(channel.id)
        self.guild.fetch_channel.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Access",
        )
        interaction = self.interaction()
        with self.assertLogs("ticket.statistics", level="ERROR"):
            await self.module.create_ticket(interaction, "moderator")
        self.guild.create_text_channel.assert_awaited_once()
        self.assertIn("doğrulanamadı", interaction.followup.send.call_args.args[0])

    def legacy_channel(self, owner_id, *, accessible=True):
        channel = self.channel(2000)
        channel.overwrites_for.return_value = discord.PermissionOverwrite(view_channel=accessible)
        panel = SimpleNamespace(
            id=2010, author=SimpleNamespace(id=999),
            embeds=[discord.Embed(title="🎫 Destek Talebi #0001", description=f"👤 **<@!{owner_id}>**")],
        )
        channel.history.side_effect = lambda **kwargs: history([panel])
        self.channels[channel.id] = channel
        self.guild.text_channels.append(channel)
        return channel

    async def test_legacy_open_ticket_without_statistics_blocks_actual_owner(self):
        channel = self.legacy_channel(30)
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "moderator")
        self.guild.create_text_channel.assert_not_awaited()
        self.assertIn(channel.mention, interaction.followup.send.call_args.args[0])

    async def test_legacy_incomplete_owner_record_still_checks_original_card(self):
        channel = self.legacy_channel(30)
        self.module.stats_register_ticket(
            10, channel.id, ticket_type="oyun_destek", ticket_number=1,
            owner_id=None, opened_at=channel.created_at,
        )
        interaction = self.interaction()
        await self.module.create_ticket(interaction, "moderator")
        self.guild.create_text_channel.assert_not_awaited()
        self.assertIn(channel.mention, interaction.followup.send.call_args.args[0])

    async def test_legacy_card_with_missing_owner_fails_closed(self):
        channel = self.legacy_channel(30)
        panel = SimpleNamespace(
            author=SimpleNamespace(id=999),
            embeds=[discord.Embed(title="🎫 Destek Talebi #0001", description="Bilet sahibi eksik")],
        )
        channel.history.side_effect = lambda **kwargs: history([panel])
        self.module.stats_register_ticket(
            10, channel.id, ticket_type="oyun_destek", ticket_number=1,
            owner_id=None, opened_at=channel.created_at,
        )
        interaction = self.interaction()
        with self.assertLogs("ticket.statistics", level="ERROR"):
            await self.module.create_ticket(interaction, "moderator")
        self.guild.create_text_channel.assert_not_awaited()
        self.assertIn("doğrulanamadı", interaction.followup.send.call_args.args[0])

    async def test_member_added_to_legacy_ticket_is_not_mistaken_for_owner(self):
        self.legacy_channel(31)
        await self.open_first()

    async def test_closed_legacy_channel_without_owner_access_does_not_block(self):
        self.legacy_channel(30, accessible=False)
        await self.open_first()

    async def test_corrupt_statistics_prevents_creation_and_preserves_file(self):
        path = Path(self.module.STATS_FILE)
        path.write_text("{broken-json", encoding="utf-8")
        interaction = self.interaction()
        with self.assertLogs("ticket.statistics", level="ERROR"):
            await self.module.create_ticket(interaction, "oyun_destek")
        self.guild.create_text_channel.assert_not_awaited()
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken-json")

    async def test_failed_api_creation_can_retry_without_leaving_owner_locked(self):
        self.guild.create_text_channel.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions",
        )
        with self.assertLogs("ticket.statistics", level="ERROR"):
            await self.module.create_ticket(self.interaction(), "oyun_destek")
        self.guild.create_text_channel.side_effect = self.create_channel
        await self.open_first()

    async def test_log_message_has_requested_transcript_text_and_real_attachment(self):
        channel = self.channel(2000)
        message = SimpleNamespace(
            created_at=channel.created_at, author="Test kullanıcısı",
            content="Destek talebinin konuşma kaydı", attachments=[],
        )
        channel.history.side_effect = lambda **kwargs: history([message])
        log_channel = self.channel(3000)
        self.channels[log_channel.id] = log_channel
        data = self.module.load_data()
        data["10"]["support_types"]["oyun_destek"]["log_channel"] = log_channel.id
        self.module.save_data(data)
        await self.module.send_ticket_log(channel, "oyun_destek", "Talep", self.user)
        log_channel.send.assert_awaited_once()
        sent = log_channel.send.call_args.kwargs
        field = next(field for field in sent["embed"].fields if field.name == "📜 Transcript")
        self.assertEqual(field.value, "Transcript panelin yukarısında verilmiştir")
        self.assertEqual(sent["file"].filename, f"{channel.name}-transcript.txt")
        self.assertIn(message.content, sent["file"].fp.getvalue().decode("utf-8"))
        sent["file"].close()


if __name__ == "__main__":
    unittest.main()
