"""Discord workflow checks using the real discord.py UI and mocked API calls."""
import asyncio
import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import discord


MODULE_PATH = Path(__file__).resolve().parents[1] / "ticket_system_main_v42.py"


def import_bot():
    spec = importlib.util.spec_from_file_location("ticket_v41_integration", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("dotenv.load_dotenv", return_value=False):
        spec.loader.exec_module(module)
    return module


def api_error(kind=discord.HTTPException, status=503):
    return kind(SimpleNamespace(status=status, reason="Unavailable"), "test failure")


async def messages(items):
    for item in items:
        yield item


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot_module = import_bot()
        self.bot_module.STATS_FILE = str(Path(self.tmp.name) / "ticket_stats.json")
        self.bot_module.DATA_FILE = str(Path(self.tmp.name) / "ticket_data.json")
        self.bot_module.bot._connection.user = SimpleNamespace(id=999, bot=True)
        self.opened_at = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
        self.staff_role = SimpleNamespace(id=50)
        self.owner = SimpleNamespace(id=30, bot=False, roles=[self.staff_role], mention="<@30>")
        self.staff = SimpleNamespace(id=40, bot=False, roles=[self.staff_role], mention="<@40>")
        self.other_staff = SimpleNamespace(id=60, bot=False, roles=[self.staff_role], mention="<@60>")
        self.guild = mock.MagicMock(spec=discord.Guild)
        self.guild.id = 10
        self.guild.name = "Deneme Destek Sunucusu"
        self.guild.icon = None
        self.guild.default_role = mock.Mock(id=10)
        self.guild.me = mock.Mock(id=999)
        member_by_id = {30: self.owner, 40: self.staff, 60: self.other_staff}
        self.guild.get_member.side_effect = member_by_id.get
        self.guild.get_channel.return_value = None
        self.channel = mock.MagicMock(spec=discord.TextChannel)
        self.channel.id = 20
        self.channel.guild = self.guild
        self.channel.name = "oyun-destek-bileti-0007"
        self.channel.topic = "oyun_destek|Oyunda destek gerekiyor"
        self.channel.created_at = self.opened_at
        self.channel.edit = mock.AsyncMock()
        self.channel.set_permissions = mock.AsyncMock()
        self.channel.history.side_effect = lambda **kwargs: messages([])
        self.countdown_message = mock.Mock(id=501)
        self.countdown_message.edit = mock.AsyncMock()
        self.channel.send = mock.AsyncMock(return_value=self.countdown_message)
        self.bot_module.save_data({"10": {"support_types": {"oyun_destek": {
            "roles": [50], "closed_category": None,
        }}}})

    def register(self, *, claimed_by=None, panel_message_id=101):
        return self.bot_module.stats_register_ticket(
            10, 20, ticket_type="oyun_destek", ticket_number=7,
            owner_id=30, opened_at=self.opened_at,
            panel_message_id=panel_message_id, claimed_by=claimed_by,
        )

    def interaction(self, actor=None, *, at_seconds=120, message_id=501):
        return SimpleNamespace(
            guild=self.guild, channel=self.channel, user=actor or self.staff,
            created_at=self.opened_at + timedelta(seconds=at_seconds),
            message=SimpleNamespace(id=message_id),
            response=SimpleNamespace(
                defer=mock.AsyncMock(), send_message=mock.AsyncMock(),
                edit_message=mock.AsyncMock(), is_done=mock.Mock(return_value=False),
            ),
            followup=SimpleNamespace(send=mock.AsyncMock()),
        )

    def panel(self, *, owner=30, claimer=40, message_id=101):
        embed = discord.Embed(
            title="🎫 Destek Talebi #0007",
            description=f"👤 **<@!{owner}>** tarafından ticket talebi\n<@&50> yetkilileri ilgilenecektir.",
        )
        embed.add_field(
            name="📌 Ticket Bilgileri",
            value=("**📊 Destek Durumu:** 🟢 İnceleniyor\n\n"
                   f"**👤 Biletle İlgilenen Yetkili:** <@!{claimer}>"),
        )
        return SimpleNamespace(
            id=message_id, author=SimpleNamespace(id=999, bot=True),
            embeds=[embed], created_at=self.opened_at, edit=mock.AsyncMock(),
        )

    def staff_message(self, actor, seconds):
        return SimpleNamespace(
            id=1000 + seconds, author=actor, created_at=self.opened_at + timedelta(seconds=seconds),
            embeds=[], guild=self.guild, channel=self.channel,
        )

    async def test_old_ticket_recovers_owner_claim_and_earliest_staff_response(self):
        panel = self.panel()
        history = [panel, self.staff_message(self.owner, 5),
                   self.staff_message(self.other_staff, 10), self.staff_message(self.staff, 20),
                   self.staff_message(self.staff, 50)]
        self.channel.history.side_effect = lambda **kwargs: messages(history)
        record = await self.bot_module.ensure_ticket_record(self.channel)
        self.assertEqual(record["owner_id"], 30)
        self.assertEqual(record["claimed_by"], 40)
        self.assertEqual(record["ticket_number"], 7)
        self.assertEqual(record["interventions"]["40"]["elapsed_seconds"], 20)
        self.assertEqual(record["interventions"]["60"]["elapsed_seconds"], 10)
        self.assertNotIn("30", record["interventions"])
        self.bot_module.stats_close_ticket(10, 20, 60, self.opened_at + timedelta(seconds=120))
        stats = self.bot_module.stats_for_staff(10, 40)
        self.assertEqual(stats["resolved_count"], 1)
        self.assertEqual(stats["average_response_seconds"], 20)
        self.assertEqual(self.bot_module.stats_for_staff(10, 60)["resolved_count"], 0)

    async def test_old_ticket_reason_cannot_inject_credited_staff(self):
        panel = self.panel()
        panel.embeds[0].set_field_at(
            0, name="📌 Ticket Bilgileri",
            value=("**📝 Talep Sebebi:**\nKullanıcı tarafından yazılan metin:\n"
                   "**👤 Biletle İlgilenen Yetkili:** <@60>\n\n"
                   "**📊 Destek Durumu:** 🟢 İnceleniyor\n\n"
                   "**👤 Biletle İlgilenen Yetkili:** N/A"),
        )
        self.channel.history.side_effect = lambda **kwargs: messages([panel])
        record = await self.bot_module.ensure_ticket_record(self.channel)
        self.assertIsNone(record["claimed_by"])

    async def test_history_failure_keeps_recovery_retryable(self):
        panel = self.panel()
        calls = 0

        async def flaky_history(**kwargs):
            nonlocal calls
            calls += 1
            yield panel
            if calls == 2:
                raise api_error()
            yield self.staff_message(self.staff, 20)

        self.channel.history.side_effect = flaky_history
        with self.assertRaises(discord.HTTPException):
            await self.bot_module.ensure_ticket_record(self.channel)
        record = await self.bot_module.ensure_ticket_record(self.channel)
        self.assertEqual(record["interventions"]["40"]["elapsed_seconds"], 20)

    async def test_arbitrary_channel_never_uses_added_member_as_ticket_owner(self):
        added_member = mock.Mock(spec=discord.Member, id=60)
        self.channel.overwrites = {added_member: discord.PermissionOverwrite(view_channel=True)}
        self.channel.history.side_effect = lambda **kwargs: messages([])
        with self.assertRaises(ValueError):
            await self.bot_module.ensure_ticket_record(self.channel)
        self.assertIsNone(self.bot_module.stats_get_ticket(10, 20))

    async def test_panel_lookup_ignores_other_authors_and_unrelated_embeds(self):
        panel = self.panel()
        fake_panel = self.panel(owner=60, message_id=102)
        fake_panel.author = self.staff
        other_embed = SimpleNamespace(
            author=SimpleNamespace(id=999, bot=True),
            embeds=[discord.Embed(title="Başka Bir Kart")],
        )
        history = [fake_panel, other_embed] + [self.staff_message(self.staff, n) for n in range(25)] + [panel]
        self.channel.history.side_effect = lambda **kwargs: messages(history)
        result = await self.bot_module.find_ticket_message(self.channel)
        self.assertIs(result, panel)
        self.assertEqual(self.channel.history.call_args.kwargs, {"limit": None, "oldest_first": True})

    async def test_claim_and_solved_status_track_intervention_but_do_not_count_closure(self):
        self.register()
        view = self.bot_module.CloseTicketView()
        with mock.patch.object(self.bot_module, "update_ticket_panel", mock.AsyncMock()):
            await view._staff_action(self.interaction(at_seconds=15), claim=True)
            await view._staff_action(self.interaction(at_seconds=30), status="✅ Çözüldü")
        record = self.bot_module.stats_get_ticket(10, 20)
        self.assertEqual(record["claimed_by"], 40)
        self.assertEqual(record["interventions"]["40"]["elapsed_seconds"], 15)
        self.assertIsNone(record["closed_at"])
        self.assertEqual(self.bot_module.stats_for_staff(10, 40)["resolved_count"], 0)

    async def test_owner_message_does_not_start_response_timer(self):
        self.register()
        with mock.patch.object(self.bot_module.bot, "process_commands", mock.AsyncMock()) as process:
            await self.bot_module.on_message(self.staff_message(self.owner, 5))
            await self.bot_module.on_message(self.staff_message(self.staff, 20))
            await self.bot_module.on_message(self.staff_message(self.staff, 30))
        record = self.bot_module.stats_get_ticket(10, 20)
        self.assertNotIn("30", record["interventions"])
        self.assertEqual(record["interventions"]["40"]["elapsed_seconds"], 20)
        self.assertEqual(process.await_count, 3)

    async def test_registered_ticket_recovers_staff_message_sent_while_bot_was_offline(self):
        self.register()
        self.channel.history.side_effect = lambda **kwargs: messages([
            self.panel(), self.staff_message(self.staff, 20),
        ])
        with mock.patch.object(self.bot_module, "update_ticket_panel", mock.AsyncMock()):
            await self.bot_module.CloseTicketView()._staff_action(self.interaction(at_seconds=120), claim=True)
        record = self.bot_module.stats_get_ticket(10, 20)
        self.assertEqual(record["interventions"]["40"]["elapsed_seconds"], 20)

    async def test_recovered_earlier_message_corrects_later_known_intervention(self):
        self.register()
        self.bot_module.stats_touch_ticket(10, 20, 40, self.opened_at + timedelta(seconds=90))
        self.channel.history.side_effect = lambda **kwargs: messages([
            self.panel(), self.staff_message(self.staff, 20),
        ])
        with mock.patch.object(self.bot_module, "update_ticket_panel", mock.AsyncMock()):
            await self.bot_module.CloseTicketView()._staff_action(self.interaction(at_seconds=120), claim=True)
        record = self.bot_module.stats_get_ticket(10, 20)
        self.assertEqual(record["interventions"]["40"]["elapsed_seconds"], 20)

    async def test_close_recovers_offline_response_of_claimer_even_when_other_staff_closes(self):
        self.register(claimed_by=40)
        self.channel.history.side_effect = lambda **kwargs: messages([
            self.panel(), self.staff_message(self.staff, 20), self.staff_message(self.other_staff, 50),
        ])
        interaction = self.interaction(self.other_staff)
        with mock.patch.object(self.bot_module.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "send_ticket_log", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "update_ticket_panel", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "find_ticket_message", mock.AsyncMock(return_value=None)), \
                mock.patch.object(self.bot_module, "send_ticket_rating", mock.AsyncMock(return_value="")):
            await self.bot_module.CloseTicketView().close_ticket.callback(interaction)
        stats = self.bot_module.stats_for_staff(10, 40)
        self.assertEqual(stats["resolved_count"], 1)
        self.assertEqual(stats["average_response_seconds"], 20)
        self.assertEqual(self.bot_module.stats_for_staff(10, 60)["resolved_count"], 0)

    async def test_two_close_clicks_count_once_send_one_dm_and_preserve_bot_permissions(self):
        self.register(claimed_by=40)
        self.bot_module.stats_touch_ticket(10, 20, 40, self.opened_at + timedelta(seconds=15))
        first = self.interaction(self.other_staff)
        second = self.interaction(self.other_staff)
        view = self.bot_module.CloseTicketView()
        with mock.patch.object(self.bot_module.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "send_ticket_log", mock.AsyncMock()) as log, \
                mock.patch.object(self.bot_module, "update_ticket_panel", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "find_ticket_message", mock.AsyncMock(return_value=self.panel())), \
                mock.patch.object(self.bot_module, "send_ticket_rating", mock.AsyncMock(return_value="DM gönderildi")) as rating:
            await asyncio.gather(view.close_ticket.callback(first), view.close_ticket.callback(second))
        self.assertEqual(self.channel.edit.await_count, 1)
        self.assertEqual(log.await_count, 1)
        self.assertEqual(rating.await_count, 1)
        stats = self.bot_module.stats_for_staff(10, 40)
        self.assertEqual(stats["resolved_count"], 1)
        self.assertEqual(stats["average_response_seconds"], 15)
        self.assertEqual(self.bot_module.stats_for_staff(10, 60)["resolved_count"], 0)
        overwrites = self.channel.edit.call_args.kwargs["overwrites"]
        self.assertTrue(overwrites[self.guild.me].view_channel)
        self.assertTrue(overwrites[self.guild.me].read_message_history)
        self.assertFalse(overwrites[self.guild.default_role].view_channel)
        self.assertIn("ikinci kez", second.followup.send.call_args.args[0])

    async def test_discord_channel_edit_failure_does_not_count_or_request_rating(self):
        self.register()
        self.channel.edit.side_effect = api_error(discord.Forbidden, 403)
        interaction = self.interaction()
        with mock.patch.object(self.bot_module.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "send_ticket_log", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "send_ticket_rating", mock.AsyncMock()) as rating:
            await self.bot_module.CloseTicketView().close_ticket.callback(interaction)
        self.assertIsNone(self.bot_module.stats_get_ticket(10, 20)["closed_at"])
        self.assertEqual(self.bot_module.stats_for_staff(10, 40)["resolved_count"], 0)
        rating.assert_not_awaited()
        self.assertIn("tamamlanamadı", interaction.followup.send.call_args.args[0])

    async def test_log_api_failure_still_counts_successful_closure_and_requests_rating(self):
        self.register()
        interaction = self.interaction()
        with mock.patch.object(self.bot_module.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "send_ticket_log", mock.AsyncMock(side_effect=api_error())), \
                mock.patch.object(self.bot_module, "update_ticket_panel", mock.AsyncMock()), \
                mock.patch.object(self.bot_module, "find_ticket_message", mock.AsyncMock(return_value=None)), \
                mock.patch.object(self.bot_module, "send_ticket_rating", mock.AsyncMock(return_value="")) as rating:
            await self.bot_module.CloseTicketView().close_ticket.callback(interaction)
        self.assertEqual(self.bot_module.stats_for_staff(10, 40)["resolved_count"], 1)
        rating.assert_awaited_once()
        self.assertIn("log kanalına gönderilemedi", interaction.followup.send.call_args.args[0])

    async def test_rating_dm_content_buttons_delivery_and_no_duplicate(self):
        self.register()
        self.bot_module.stats_close_ticket(10, 20, 40, self.opened_at + timedelta(seconds=120))
        dm_message = SimpleNamespace(id=501, channel=SimpleNamespace(id=601))
        owner = SimpleNamespace(send=mock.AsyncMock(return_value=dm_message))
        with mock.patch.object(self.bot_module.bot, "get_user", return_value=owner):
            notice = await self.bot_module.send_ticket_rating(self.channel)
            await self.bot_module.send_ticket_rating(self.channel)
        owner.send.assert_awaited_once()
        sent = owner.send.call_args.kwargs
        self.assertEqual(sent["embed"].title, self.guild.name)
        self.assertIn("Oyun Destek Bileti #0007", sent["embed"].description)
        self.assertIn("Biletinizle yeterince iyi ilgilenildi mi?", sent["embed"].description)
        self.assertIn("aşağıdaki tuşlardan puanlayın", sent["embed"].description)
        self.assertEqual([button.score for button in sent["view"].children], [1, 2, 3, 4, 5])
        self.assertTrue(sent["view"].is_persistent())
        record = self.bot_module.stats_get_ticket(10, 20)
        self.assertEqual(record["rating_message_id"], 501)
        self.assertEqual(record["rating_dm_channel_id"], 601)
        self.assertEqual(record["rating_state"], "sent")
        self.assertIn("gönderildi", notice)

    async def test_dm_blocked_preserves_closed_ticket_and_unrated_average(self):
        self.register()
        self.bot_module.stats_close_ticket(10, 20, 40, self.opened_at + timedelta(seconds=120))
        owner = SimpleNamespace(send=mock.AsyncMock(side_effect=api_error(discord.Forbidden, 403)))
        with mock.patch.object(self.bot_module.bot, "get_user", return_value=owner):
            notice = await self.bot_module.send_ticket_rating(self.channel)
        self.assertIn("DM'i kapalı", notice)
        self.assertEqual(self.bot_module.stats_get_ticket(10, 20)["rating_state"], "blocked")
        self.assertEqual(self.bot_module.stats_for_staff(10, 40)["resolved_count"], 1)
        self.assertIsNone(self.bot_module.stats_for_staff(10, 40)["average_rating"])

    async def test_rating_ui_enforces_owner_message_identity_and_single_score(self):
        self.register()
        self.bot_module.stats_close_ticket(10, 20, 40, self.opened_at + timedelta(seconds=120))
        self.bot_module.stats_set_delivery(10, 20, state="sent", message_id=501, dm_channel_id=601)
        view = self.bot_module.TicketRatingView(10, 20)
        impostor = self.interaction(self.staff)
        self.assertFalse(await view.interaction_check(impostor))
        valid = self.interaction(self.owner)
        self.assertTrue(await view.interaction_check(valid))
        wrong_message = self.interaction(self.owner, message_id=502)
        await view.submit(wrong_message, 5)
        self.assertIsNone(self.bot_module.stats_get_ticket(10, 20)["rating_score"])
        await view.submit(valid, 4)
        self.assertEqual(self.bot_module.stats_for_staff(10, 40)["average_rating"], 4)
        valid.response.edit_message.assert_awaited_once()
        self.assertIsNone(valid.response.edit_message.call_args.kwargs["view"])
        repeat = self.interaction(self.owner)
        await self.bot_module.TicketRatingView(10, 20).submit(repeat, 1)
        self.assertEqual(self.bot_module.stats_for_staff(10, 40)["average_rating"], 4)
        self.assertIn("daha önce", repeat.response.send_message.call_args.args[0])

    async def test_startup_restores_dm_rating_and_ticket_views_before_command_sync(self):
        self.register()
        self.bot_module.stats_close_ticket(10, 20, 40, self.opened_at + timedelta(seconds=120))
        self.bot_module.stats_set_delivery(10, 20, state="sent", message_id=501, dm_channel_id=601)
        with mock.patch.object(self.bot_module.bot, "add_view") as add_view, \
                mock.patch.object(self.bot_module.bot.tree, "sync", mock.AsyncMock()) as sync:
            await self.bot_module.bot.setup_hook()
        self.assertEqual(add_view.call_count, 3)
        restored = add_view.call_args_list[-1]
        self.assertEqual(restored.kwargs["message_id"], 501)
        self.assertIsInstance(restored.args[0], self.bot_module.TicketRatingView)
        for call in add_view.call_args_list:
            self.assertTrue(call.args[0].is_persistent())
        sync.assert_awaited_once()

    async def test_existing_panel_random_ids_are_restored_without_editing_message(self):
        default_view = self.bot_module.TicketPanelView()
        old_ids = {child.label: f"v40-random-{number}" for number, child in enumerate(default_view.children)}
        old_message = SimpleNamespace(
            id=201, author=SimpleNamespace(id=999),
            embeds=[discord.Embed(title="# Destek Talebi Oluştur #")],
            components=[SimpleNamespace(children=[SimpleNamespace(label=label, custom_id=value)
                                                for label, value in old_ids.items()])],
            edit=mock.AsyncMock(),
        )
        settings = self.bot_module.load_data()
        settings["10"].update(panel_channel=20, panel_message_id=201)
        self.bot_module.save_data(settings)
        self.guild.get_channel.return_value = self.channel
        self.channel.fetch_message = mock.AsyncMock(return_value=old_message)
        with mock.patch.object(type(self.bot_module.bot), "guilds", new_callable=mock.PropertyMock,
                               return_value=[self.guild]), \
                mock.patch.object(self.bot_module.bot, "add_view") as add_view:
            await self.bot_module.restore_existing_ticket_panels()
        add_view.assert_called_once()
        restored = add_view.call_args.args[0]
        self.assertEqual({child.label: child.custom_id for child in restored.children}, old_ids)
        self.assertEqual(add_view.call_args.kwargs["message_id"], 201)
        self.assertTrue(restored.is_persistent())
        old_message.edit.assert_not_awaited()

    async def test_statistics_command_shows_independent_means_and_rating_count(self):
        self.register()
        self.bot_module.stats_touch_ticket(10, 20, 40, self.opened_at + timedelta(seconds=15))
        self.bot_module.stats_close_ticket(10, 20, 40, self.opened_at + timedelta(seconds=120))
        self.bot_module.stats_submit_rating(10, 20, 30, 2)
        self.bot_module.stats_register_ticket(
            10, 21, ticket_type="oyun_destek", ticket_number=8, owner_id=30,
            opened_at=self.opened_at,
        )
        self.bot_module.stats_touch_ticket(10, 21, 40, self.opened_at + timedelta(seconds=45))
        self.bot_module.stats_close_ticket(10, 21, 40, self.opened_at + timedelta(seconds=180))
        self.bot_module.stats_submit_rating(10, 21, 30, 5)
        self.staff.roles = [SimpleNamespace(id=self.bot_module.AUTHORIZED_COMMAND_ROLE)]
        self.staff.display_avatar = SimpleNamespace(url="https://cdn.discordapp.com/test-avatar.png")
        interaction = self.interaction()
        await self.bot_module.staff_statistics.callback(interaction, None)
        sent = interaction.response.send_message.call_args.kwargs
        self.assertTrue(sent["ephemeral"])
        values = {field.name: field.value for field in sent["embed"].fields}
        self.assertEqual(values["✅ Çözülen Bilet"], "2")
        self.assertEqual(values["⏱️ Ortalama Müdahale Süresi"], "30 sn")
        self.assertIn("3.50/5", values["⭐ Kullanıcı Değerlendirmesi"])
        self.assertIn("2 değerlendirme", values["⭐ Kullanıcı Değerlendirmesi"])

    async def test_statistics_command_rejects_member_without_command_role(self):
        interaction = self.interaction()
        await self.bot_module.staff_statistics.callback(interaction, None)
        sent = interaction.response.send_message.call_args
        self.assertIn("yetkiniz yok", sent.args[0])
        self.assertTrue(sent.kwargs["ephemeral"])
        self.assertNotIn("embed", sent.kwargs)

    async def test_corrupt_ledger_rejects_rating_cleanly_without_overwriting_it(self):
        path = Path(self.bot_module.STATS_FILE)
        path.write_text("{broken-json", encoding="utf-8")
        interaction = self.interaction(self.owner)
        view = self.bot_module.TicketRatingView(10, 20)
        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken-json")


if __name__ == "__main__":
    unittest.main()
