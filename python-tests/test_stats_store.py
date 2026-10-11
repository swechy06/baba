import concurrent.futures
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "ticket_system_main_v42.py"


def import_stats():
    spec = importlib.util.spec_from_file_location("v41_test_stats", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("dotenv.load_dotenv", return_value=False):
        spec.loader.exec_module(module)
    return module


class StatsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "ticket_stats.json"
        self.stats = import_stats()
        self.stats.STATS_FILE = str(self.path)
        self.opened_at = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)

    def register(self, channel=20, guild=10, owner=30, claimed_by=None):
        return self.stats.stats_register_ticket(
            guild, channel, ticket_type="oyun_destek", ticket_number=channel,
            owner_id=owner, opened_at=self.opened_at, claimed_by=claimed_by,
        )

    def close(self, channel=20, guild=10, actor=40, seconds=120):
        return self.stats.stats_close_ticket(
            guild, channel, actor, self.opened_at + timedelta(seconds=seconds)
        )

    def test_initial_data_and_missing_records_do_not_create_file(self):
        self.assertEqual(self.stats.load_ticket_stats(), {"version": 1, "guilds": {}})
        self.assertIsNone(self.stats.stats_get_ticket(10, 20))
        self.assertIsNone(self.stats.stats_touch_ticket(10, 20, 40))
        self.assertIsNone(self.stats.stats_claim_ticket(10, 20, 40))
        self.assertIsNone(self.stats.stats_close_ticket(10, 20, 40))
        self.assertEqual(self.stats.stats_submit_rating(10, 20, 30, 5), "unavailable")
        self.assertFalse(self.path.exists())

    def test_persistence_and_return_values_are_independent_snapshots(self):
        record = self.register()
        record["owner_id"] = 999
        record["interventions"]["999"] = {}
        reloaded = import_stats()
        reloaded.STATS_FILE = str(self.path)
        saved = reloaded.stats_get_ticket(10, 20)
        self.assertEqual(saved["owner_id"], 30)
        self.assertEqual(saved["ticket_type"], "oyun_destek")
        self.assertEqual(saved["interventions"], {})
        self.assertEqual(saved["rating_state"], "pending")
        self.assertIsNone(saved["closed_at"])

    def test_registration_is_idempotent_and_does_not_reset_closed_ticket(self):
        self.register()
        self.close()
        self.stats.stats_submit_rating(10, 20, 30, 4)
        old = self.path.read_bytes()
        new = self.stats.stats_register_ticket(
            10, 20, ticket_type="other", ticket_number=999,
            owner_id=999, opened_at=self.opened_at + timedelta(days=1),
        )
        self.assertEqual(new["owner_id"], 30)
        self.assertEqual(new["rating_score"], 4)
        self.assertEqual(self.path.read_bytes(), old)

    def test_timezones_are_normalized_and_naive_times_rejected(self):
        local = datetime(2026, 10, 11, 15, 0, tzinfo=timezone(timedelta(hours=3)))
        result = self.stats.stats_register_ticket(
            10, 20, ticket_type="oyun", ticket_number=1,
            owner_id=30, opened_at=local.isoformat(),
        )
        self.assertEqual(result["opened_at"], self.opened_at.isoformat())
        with self.assertRaises(ValueError):
            self.stats.stats_register_ticket(
                10, 21, ticket_type="oyun", ticket_number=2,
                owner_id=30, opened_at=datetime(2026, 10, 11),
            )
        with self.assertRaises(ValueError):
            self.stats.stats_register_ticket(
                10, 21, ticket_type="oyun", ticket_number=2,
                owner_id=30, opened_at=None,
            )
        self.assertIsNone(self.stats.stats_get_ticket(10, 21))

    def test_first_intervention_is_preserved_for_later_claims(self):
        self.register()
        self.stats.stats_touch_ticket(10, 20, 40, self.opened_at + timedelta(seconds=10))
        self.stats.stats_claim_ticket(10, 20, 40, self.opened_at + timedelta(seconds=30))
        result = self.stats.stats_touch_ticket(10, 20, 40, self.opened_at + timedelta(seconds=60))
        self.assertEqual(result["interventions"]["40"]["elapsed_seconds"], 10)
        self.assertEqual(result["claimed_by"], 40)
        self.assertEqual(self.stats.stats_for_staff(10, 40)["resolved_count"], 0)

    def test_closed_tickets_count_once_for_claimed_staff_and_ignore_later_claims(self):
        self.register()
        self.stats.stats_claim_ticket(10, 20, 40, self.opened_at + timedelta(seconds=15))
        closed = self.close(actor=50)
        self.assertEqual(closed["closed_by"], 50)
        self.assertEqual(closed["staff_id"], 40)
        self.stats.stats_claim_ticket(10, 20, 50, self.opened_at + timedelta(seconds=140))
        self.stats.stats_touch_ticket(10, 20, 60, self.opened_at + timedelta(seconds=140))
        closed_again = self.close(actor=60, seconds=180)
        self.assertEqual(closed_again, closed)
        self.assertEqual(self.stats.stats_for_staff(10, 40)["resolved_count"], 1)
        self.assertEqual(self.stats.stats_for_staff(10, 50)["resolved_count"], 0)
        self.assertEqual(self.stats.stats_for_staff(10, 60)["resolved_count"], 0)

    def test_out_of_order_earlier_message_becomes_first_intervention_only_while_open(self):
        self.register()
        self.stats.stats_claim_ticket(10, 20, 40, self.opened_at + timedelta(seconds=30))
        earlier = self.stats.stats_touch_ticket(10, 20, 40, self.opened_at + timedelta(seconds=10))
        self.assertEqual(earlier["interventions"]["40"]["elapsed_seconds"], 10)
        before = self.path.read_bytes()
        self.stats.stats_touch_ticket(10, 20, 40, self.opened_at + timedelta(seconds=25))
        self.assertEqual(self.path.read_bytes(), before)
        self.close()
        before = self.path.read_bytes()
        self.stats.stats_touch_ticket(10, 20, 40, self.opened_at + timedelta(seconds=5))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.stats.stats_for_staff(10, 40)["average_response_seconds"], 10)

    def test_unclaimed_close_credits_actor_and_records_first_close_intervention(self):
        self.register()
        closed = self.close(actor=50, seconds=75)
        self.assertEqual(closed["staff_id"], 50)
        result = self.stats.stats_for_staff(10, 50)
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["average_response_seconds"], 75)
        self.assertEqual(result["response_sample_count"], 1)

    def test_unknown_owner_can_close_but_cannot_rate(self):
        self.register(owner=None)
        self.close()
        self.assertEqual(self.stats.stats_for_staff(10, 40)["resolved_count"], 1)
        self.assertEqual(self.stats.stats_submit_rating(10, 20, 30, 5), "unavailable")

    def test_legacy_claim_without_time_does_not_invent_response_duration(self):
        self.register(claimed_by=40)
        self.close(actor=50)
        result = self.stats.stats_for_staff(10, 40)
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["response_sample_count"], 0)
        self.assertIsNone(result["average_response_seconds"])
        self.assertEqual(self.stats.stats_get_ticket(10, 20)["interventions"]["50"]["elapsed_seconds"], 120)

    def test_average_uses_only_closed_tickets_and_real_ratings(self):
        for channel, response, rating in ((20, 10, 5), (21, 30, 3), (22, 80, None), (23, 99, 1)):
            self.register(channel)
            self.stats.stats_claim_ticket(10, channel, 40, self.opened_at + timedelta(seconds=response))
            if channel == 23:
                continue
            self.close(channel, actor=50, seconds=150)
            if rating is not None:
                self.assertEqual(self.stats.stats_submit_rating(10, channel, 30, rating), "saved")
        result = self.stats.stats_for_staff(10, 40)
        self.assertEqual(result["resolved_count"], 3)
        self.assertEqual(result["response_sample_count"], 3)
        self.assertEqual(result["average_response_seconds"], 40)
        self.assertEqual(result["rating_count"], 2)
        self.assertEqual(result["average_rating"], 4)
        self.assertEqual(self.stats.stats_submit_rating(10, 23, 30, 1), "unavailable")

    def test_guilds_are_isolated_even_with_same_channel_and_staff_ids(self):
        self.register(guild=10)
        self.register(guild=11, owner=31)
        self.close(guild=10, seconds=10)
        self.close(guild=11, seconds=40)
        self.stats.stats_submit_rating(10, 20, 30, 1)
        self.stats.stats_submit_rating(11, 20, 31, 5)
        self.assertEqual(self.stats.stats_for_staff(10, 40)["average_rating"], 1)
        self.assertEqual(self.stats.stats_for_staff(11, 40)["average_rating"], 5)
        self.assertEqual(self.stats.stats_for_staff(11, 40)["average_response_seconds"], 40)

    def test_rating_authentication_message_binding_and_single_submission(self):
        self.register()
        self.close()
        self.assertEqual(self.stats.stats_submit_rating(10, 20, 999, 5), "forbidden")
        self.assertEqual(self.stats.stats_submit_rating(10, 20, 30, 5, 777), "unavailable")
        self.stats.stats_set_delivery(10, 20, state="sent", message_id=777, dm_channel_id=888)
        self.assertEqual(self.stats.stats_submit_rating(10, 20, 30, 5, 778), "forbidden")
        self.assertEqual(self.stats.stats_submit_rating(10, 20, 30, 4, 777), "saved")
        self.assertEqual(self.stats.stats_submit_rating(10, 20, 30, 1, 777), "already_rated")
        self.assertEqual(self.stats.stats_get_ticket(10, 20)["rating_score"], 4)

    def test_rating_rejects_boolean_float_string_and_out_of_range(self):
        self.register()
        self.close()
        for invalid in (True, False, 0, 6, -1, 2.5, 3.0, "5", None):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.stats.stats_submit_rating(10, 20, 30, invalid), "invalid")
        self.assertIsNone(self.stats.stats_get_ticket(10, 20)["rating_score"])

    def test_pending_views_survive_restart_and_completed_rating_is_not_pending(self):
        self.register()
        self.register(21)
        self.register(22)
        self.close(20)
        self.close(21)
        self.stats.stats_set_delivery(10, 20, state="sent", message_id=777, dm_channel_id=888)
        self.stats.stats_set_delivery(10, 22, state="sent", message_id=779, dm_channel_id=888)
        restarted = import_stats()
        restarted.STATS_FILE = str(self.path)
        pending = restarted.stats_pending_ratings()
        self.assertEqual([(guild, channel) for guild, channel, _ in pending], [(10, 20)])
        self.assertEqual(pending[0][2]["rating_message_id"], 777)
        restarted.stats_submit_rating(10, 20, 30, 5, 777)
        self.assertEqual(restarted.stats_pending_ratings(), [])
        self.assertEqual(restarted.stats_for_staff(10, 40)["average_rating"], 5)

    def test_failed_delivery_preserves_successful_message_and_completed_rating(self):
        self.register()
        self.close()
        self.stats.stats_set_delivery(10, 20, state="sent", message_id=777, dm_channel_id=888)
        failed = self.stats.stats_set_delivery(10, 20, state="failed")
        self.assertEqual(failed["rating_message_id"], 777)
        self.assertEqual(failed["rating_dm_channel_id"], 888)
        self.stats.stats_submit_rating(10, 20, 30, 5, 777)
        failed = self.stats.stats_set_delivery(10, 20, state="failed")
        self.assertEqual(failed["rating_score"], 5)
        self.assertEqual(failed["rating_state"], "rated")

    def test_panel_id_is_saved_after_creation_without_changing_closed_records(self):
        self.register()
        panel = self.stats.stats_set_panel_message(10, 20, 700)
        self.assertEqual(panel["panel_message_id"], 700)
        closed = self.close()
        self.assertEqual(self.stats.stats_set_panel_message(10, 20, 701), closed)

    def test_negative_clock_drift_is_clamped_to_zero(self):
        self.register()
        self.stats.stats_touch_ticket(10, 20, 40, self.opened_at - timedelta(seconds=2))
        self.close()
        self.assertEqual(self.stats.stats_for_staff(10, 40)["average_response_seconds"], 0)

    def test_corrupt_json_never_overwrites_existing_file(self):
        corrupt = b'{"version":1,"guilds":'
        self.path.write_bytes(corrupt)
        with self.assertRaises(self.stats.TicketStatsError):
            self.stats.load_ticket_stats()
        with self.assertRaises(self.stats.TicketStatsError):
            self.register()
        with self.assertRaises(self.stats.TicketStatsError):
            self.stats.save_ticket_stats({"version": 1, "guilds": {}})
        self.assertEqual(self.path.read_bytes(), corrupt)

    def test_wrong_schema_version_missing_fields_and_nan_are_rejected(self):
        self.register()
        valid = json.loads(self.path.read_text(encoding="utf-8"))
        for alter in (
            lambda data: data.update(version=2),
            lambda data: data["guilds"]["10"]["tickets"]["20"].pop("opened_at"),
            lambda data: data["guilds"]["10"]["tickets"]["20"].update(opened_at=None),
            lambda data: data["guilds"]["10"]["tickets"]["20"].update(
                interventions={"40": {"at": self.opened_at.isoformat(), "elapsed_seconds": float("nan")}}
            ),
        ):
            data = json.loads(json.dumps(valid))
            alter(data)
            source = json.dumps(data)
            self.path.write_text(source, encoding="utf-8")
            with self.assertRaises(self.stats.TicketStatsError):
                self.stats.load_ticket_stats()
            self.assertEqual(self.path.read_text(encoding="utf-8"), source)

    def test_atomic_replace_failure_preserves_previous_file_and_cleans_temporary(self):
        self.register()
        before = self.path.read_bytes()
        with mock.patch.object(self.stats._stats_os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(self.stats.TicketStatsError):
                self.close()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()), ["ticket_stats.json"])
        self.assertIsNone(self.stats.stats_get_ticket(10, 20)["closed_at"])

    def test_concurrent_ratings_accept_exactly_one_and_keep_score(self):
        self.register()
        self.close()
        self.stats.stats_set_delivery(10, 20, state="sent", message_id=777, dm_channel_id=888)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as workers:
            results = list(workers.map(
                lambda score: self.stats.stats_submit_rating(10, 20, 30, score, 777), range(1, 6)
            ))
        self.assertEqual(results.count("saved"), 1)
        self.assertEqual(results.count("already_rated"), 4)
        self.assertEqual(self.stats.stats_for_staff(10, 40)["rating_count"], 1)

    def test_concurrent_closures_keep_single_credit_and_completed_count(self):
        self.register()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as workers:
            closed = list(workers.map(lambda actor: self.close(actor=actor), range(40, 45)))
        self.assertTrue(all(record == closed[0] for record in closed))
        self.assertEqual(sum(self.stats.stats_for_staff(10, actor)["resolved_count"] for actor in range(40, 45)), 1)


if __name__ == "__main__":
    unittest.main()
