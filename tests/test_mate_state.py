from contextlib import closing
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch
import uuid
import httpx
from PIL import Image
from pydantic import ValidationError
from maplemate.capture import CapturedFrame, CaptureError
from maplemate.config import Config
from maplemate.maple_mcp.models import PlayerContextPatch, ScreenObservation
from maplemate.maple_mcp.nexon import NexonClient, NexonError
from maplemate.maple_mcp.service import MapleService
from maplemate.runtime import CaptureManager
from maplemate.secrets import _crypt, load_key, save_key
from maplemate.setup import configure_key
from maplemate.state import StateStore, utc_now

TEST_ROOT = Path(__file__).resolve().parents[1] / ".cache" / "mate-tests"


class MateCase(unittest.TestCase):
    def setUp(self):
        self.directory = TEST_ROOT / uuid.uuid4().hex
        self.config = Config.load(self.directory)
        self.store = StateStore(self.directory)
        self.store.set("registered_characters", [{"character_name": "초보용사", "ocid": "synthetic-one"}, {"character_name": "다른용사", "ocid": "synthetic-two"}])

    def tearDown(self):
        assert self.directory.resolve().parent == TEST_ROOT.resolve()
        shutil.rmtree(self.directory)

    def capture(self, seconds_ago=0):
        metadata = {"screenshot_id": uuid.uuid4().hex, "captured_at": (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()}
        self.store.register_capture(metadata)
        return metadata

    def observation(self, capture, name="초보용사", meso=0, starforce=None):
        return ScreenObservation(screenshot_id=capture["screenshot_id"], character_name=name, inventory_meso=meso,
                                 tooltips=[{"item_name": "테스트 장비", "required_level": 200, "starforce": starforce}]).model_dump()


class StateTests(MateCase):
    def test_old_database_schema_upgrades_without_removing_records_or_mesos(self):
        directory = self.directory / "old-installation"
        directory.mkdir()
        with closing(sqlite3.connect(directory / "state.sqlite3")) as db, db:
            db.executescript("""
                CREATE TABLE records (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE meso (character TEXT PRIMARY KEY, value INTEGER NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL);
                CREATE TABLE captures (id TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            db.execute("INSERT INTO records VALUES (?,?)", ("context:초보용사", json.dumps({"goal": "기존 목표"})))
            db.execute("INSERT INTO records VALUES (?,?)", ("unknown_existing_record", json.dumps({"preserve": True})))
            db.execute("INSERT INTO meso VALUES (?,?,?,?)", ("초보용사", 77, utc_now(), "screen"))
        store = StateStore(directory)
        store.update_context("초보용사", {"daily_play_minutes": 30}, 0)
        state = store.snapshot("초보용사")
        self.assertEqual(state["inventory_meso"]["value"], 77)
        self.assertEqual(state["user_context"]["goal"], "기존 목표")
        self.assertEqual(state["context_facts"]["goal"]["source"], "legacy_context")
        self.assertEqual(store.get("unknown_existing_record"), {"preserve": True})

    def test_zero_balance_and_unknown_stars_keep_time_and_source(self):
        metadata = self.capture()
        self.store.record_observation(self.observation(metadata))
        state = self.store.snapshot()
        self.assertEqual(state["inventory_meso"]["value"], 0)
        self.assertEqual(state["inventory_meso"]["source"], "llm_vision")
        self.assertEqual(state["inventory_meso"]["observed_at"], metadata["captured_at"])
        self.assertFalse(state["inventory_meso"]["is_current_balance"])
        self.assertIsNone(state["tooltips"][0]["starforce"])
        self.assertIn("starforce", MapleService(self.config).upgrade_context()["missing_fields"])

    def test_foreign_unknown_and_unidentified_nicknames_cannot_save_meso(self):
        metadata = self.capture()
        for name in ("목록에없는용사", None):
            with self.assertRaises(ValueError):
                self.store.record_observation(self.observation(metadata, name))
        self.assertIsNone(self.store.snapshot("초보용사")["inventory_meso"])

    def test_unknown_image_and_expired_image_cannot_write(self):
        for metadata in ({"screenshot_id": uuid.uuid4().hex}, self.capture(901)):
            with self.assertRaises(ValueError):
                self.store.record_observation(self.observation(metadata))

    def test_older_async_result_cannot_replace_newer_character(self):
        old, new = self.capture(30), self.capture()
        self.store.record_observation(self.observation(new, "다른용사", 55))
        with self.assertRaises(ValueError):
            self.store.record_observation(self.observation(old, meso=100))
        self.assertEqual(self.store.snapshot()["character_name"], "다른용사")
        self.assertIsNone(self.store.snapshot("초보용사")["inventory_meso"])

    def test_switch_and_unreadable_inventory_do_not_cross_attribute(self):
        old = self.capture(20)
        self.store.record_observation(self.observation(old, meso=123))
        newer = self.capture()
        self.store.record_observation(self.observation(newer, "다른용사", None))
        self.assertIsNone(self.store.snapshot()["inventory_meso"])
        self.assertEqual(self.store.snapshot("초보용사")["inventory_meso"]["value"], 123)
        self.assertEqual(self.store.snapshot("초보용사")["screen_observation"]["screenshot_id"], old["screenshot_id"])
        self.assertEqual(self.store.snapshot("초보용사")["last_observed_character"]["name"], "다른용사")

    def test_legacy_key_and_balance_survive_with_historical_label(self):
        observed_at = utc_now()
        self.store.set("last_browser_character", {"name": "초보용사"})
        self.store.set("observer", {"last_frame_at": observed_at})
        with self.store.connect() as db:
            db.execute("INSERT INTO meso VALUES (?,?,?,?)", ("초보용사", 77, observed_at, "screen"))
        save_key(self.directory, "synthetic-secret")
        with patch.dict(os.environ, {"NEXON_API_KEY": ""}):
            self.assertEqual(load_key(self.directory), "synthetic-secret")
        state = self.store.snapshot()
        self.assertEqual(state["inventory_meso"]["value"], 77)
        self.assertEqual(state["last_observed_character"]["source"], "legacy_screen_observation")
        self.assertFalse(state["last_observed_character"]["is_live"])

    def test_observation_schema_rejects_bool_amount_and_out_of_range_stars(self):
        for fields in ({"inventory_meso": True}, {"inventory_meso": -1}, {"tooltips": [{"starforce": 31}]}, {"tooltips": [{"starforce": 22, "unknown": "extra"}]}):
            with self.assertRaises(ValidationError):
                ScreenObservation(screenshot_id=uuid.uuid4().hex, **fields)

    def test_legacy_string_nickname_after_web_shutdown_uses_historical_meso_time(self):
        observed_at = utc_now()
        self.store.set("last_browser_character", "초보용사")
        self.store.set("observer", {"status": "stopped", "last_frame_at": None})
        with self.store.connect() as db:
            db.execute("INSERT INTO meso VALUES (?,?,?,?)", ("초보용사", 55, observed_at, "screen"))
        state = self.store.snapshot()
        self.assertEqual(state["last_observed_character"]["name"], "초보용사")
        self.assertEqual(state["last_observed_character"]["observed_at"], observed_at)
        self.assertEqual(state["inventory_meso"]["value"], 55)

    def test_uncertain_stars_and_stale_tooltips_do_not_make_complete_context(self):
        capture = self.capture()
        value = self.observation(capture, starforce=22)
        value["tooltips"][0]["uncertain_fields"] = ["starforce"]
        self.store.record_observation(value)
        self.assertIn("starforce", MapleService(self.config).upgrade_context()["missing_fields"])
        stored = self.store.get("screen_observation")
        stored["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        self.store.set("screen_observation", stored)
        self.assertIsNone(MapleService(self.config).upgrade_context()["selected_item"])

    def test_dpapi_round_trip_does_not_contain_plaintext(self):
        value = b"synthetic-secret"
        encrypted = _crypt(value)
        self.assertNotIn(value, encrypted)
        self.assertEqual(_crypt(encrypted, decrypt=True), value)

    def test_failed_key_setup_does_not_replace_existing_key_or_account(self):
        save_key(self.directory, "previous-synthetic-key")
        previous = (self.directory / "nexon-key.dpapi").read_bytes()
        client = NexonClient("new-synthetic-key", httpx.MockTransport(lambda _: httpx.Response(403, json={"error": {"name": "OPENAPI00002"}})))
        with patch("maplemate.setup.NexonClient", return_value=client):
            with self.assertRaises(NexonError):
                configure_key(self.config, "new-synthetic-key")
        self.assertEqual((self.directory / "nexon-key.dpapi").read_bytes(), previous)
        self.assertEqual(len(self.store.get("registered_characters")), 2)

    def test_context_upgrade_preserves_legacy_unknown_time_and_other_characters(self):
        self.store.set("context:초보용사", {"goal": "이전 목표", "play_style": "이전 성향"})
        before = self.store.snapshot("초보용사")
        self.assertEqual(before["context_facts"]["goal"]["source"], "legacy_context")
        self.assertIsNone(before["context_facts"]["goal"]["updated_at"])
        self.store.update_context("초보용사", {"budget_meso": 0, "daily_play_minutes": 30}, 0)
        reopened = StateStore(self.directory).snapshot("초보용사")
        self.assertEqual(reopened["user_context"]["goal"], "이전 목표")
        self.assertEqual(reopened["user_context"]["budget_meso"], 0)
        self.assertEqual(reopened["context_facts"]["daily_play_minutes"]["source"], "user_input")
        self.assertIsNotNone(reopened["context_facts"]["daily_play_minutes"]["updated_at"])
        self.assertEqual(self.store.snapshot("다른용사")["user_context"], {})

    def test_partial_update_and_clear_keep_other_fields_and_observed_balance(self):
        self.store.record_observation(self.observation(self.capture(), meso=123))
        self.store.update_context("초보용사", {"budget_meso": 100, "goal": "목표"})
        old_goal = self.store.snapshot("초보용사")["context_facts"]["goal"]
        self.store.update_context("초보용사", {"budget_meso": None})
        state = self.store.snapshot("초보용사")
        self.assertIsNone(state["user_context"]["budget_meso"])
        self.assertEqual(state["context_facts"]["goal"], old_goal)
        self.assertEqual(state["inventory_meso"]["value"], 123)

    def test_revision_conflict_and_invalid_date_are_atomic(self):
        self.store.update_context("초보용사", {"goal": "최신 목표"}, 0)
        with self.assertRaises(ValueError):
            self.store.update_context("초보용사", {"goal": "과거 목표", "budget_meso": 500}, 0)
        with self.assertRaises(ValueError):
            self.store.update_context("초보용사", {"goal": "잘못된 목표", "target_date": "2026-02-30"})
        state = self.store.snapshot("초보용사")
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["user_context"], {"goal": "최신 목표"})

    def test_boss_reports_merge_by_conditions_and_preserve_optional_details(self):
        solo = {"boss_name": "테스트 보스", "difficulty": "일반", "party_size": 1, "status": "practicing", "notes": "생존 연습"}
        party = {**solo, "party_size": 2, "status": "cleared"}
        self.store.update_context("초보용사", {"boss_experience": [solo, party]})
        self.store.update_context("초보용사", {"boss_experience": [{**{k: v for k, v in solo.items() if k != "notes"}, "status": "stable"}]})
        reports = self.store.snapshot("초보용사")["user_context"]["boss_experience"]
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["notes"], "생존 연습")
        self.assertEqual(reports[0]["status"], "stable")
        self.assertEqual(reports[1]["status"], "cleared")
        self.assertEqual(reports[0]["source"], "user_input")
        self.store.update_context("초보용사", {"boss_experience": None})
        self.assertIsNone(self.store.snapshot("초보용사")["user_context"]["boss_experience"])

    def api_result(self, date="2026-10-10", data=None, endpoint="character/stat"):
        return {"source": "nexon_open_api", "endpoint": endpoint, "fetched_at": utc_now(), "requested_date": None,
                "data_date": date, "data": data if data is not None else {"date": date, "sample": "synthetic"}}

    def test_api_observations_keep_variants_dates_and_do_not_replace_with_older_data(self):
        result = self.api_result()
        self.store.record_api_observation("초보용사", {"ocid": "synthetic"}, result)
        self.assertFalse(self.store.record_api_observation("초보용사", {}, self.api_result("2026-10-09"))["saved"])
        self.assertFalse(self.store.record_api_observation("초보용사", {}, self.api_result(None))["saved"])
        self.store.record_api_observation("초보용사", {"date": "2026-10-08"}, self.api_result("2026-10-08"))
        summarized = self.store.snapshot("초보용사")
        self.assertEqual(len(summarized["api_observations"]), 2)
        self.assertNotIn("data", summarized["api_observations"][0])
        self.assertNotIn("ocid", summarized["api_observations"][1]["parameters"])
        complete = self.store.snapshot("초보용사", include_api_data=True)
        self.assertEqual(complete["api_observations"][1]["data"], result["data"])
        self.assertFalse(complete["api_observations"][1]["is_live"])
        self.assertEqual(self.store.snapshot("다른용사")["api_observations"], [])

    def test_api_state_is_bounded_and_excludes_unrelated_data(self):
        for endpoint in ("history/starforce", "notice-event", "character/list"):
            self.assertFalse(self.store.record_api_observation("초보용사", {}, self.api_result(endpoint=endpoint))["saved"])
        for index in range(35):
            self.store.record_api_observation("초보용사", {"preset_no": index}, self.api_result())
        self.assertEqual(len(self.store.snapshot("초보용사")["api_observations"]), 32)
        self.assertFalse(self.store.record_api_observation("초보용사", {}, self.api_result(data={"large": "x" * 512001}))["saved"])

    def test_assessment_requires_matching_revision_and_is_invalidated_by_new_facts(self):
        assessment = {"stage": "beginner", "system_understanding": "unknown", "goal": "테스트 목표", "rationale": ["확인한 기반"], "confidence": "low"}
        self.store.record_assessment("초보용사", assessment, 0)
        state = self.store.snapshot("초보용사")
        self.assertFalse(state["growth_assessment"]["needs_review"])
        self.assertEqual(state["growth_assessment"]["source"], "llm_assessment")
        self.assertFalse(state["growth_assessment"]["is_user_fact"])
        self.store.update_context("다른용사", {"goal": "다른 목표"})
        self.assertFalse(self.store.snapshot("초보용사")["growth_assessment"]["needs_review"])
        self.store.update_context("초보용사", {"goal": "새 목표"})
        self.assertTrue(self.store.snapshot("초보용사")["growth_assessment"]["needs_review"])
        with self.assertRaises(ValueError):
            self.store.record_assessment("초보용사", assessment, 0)
        self.assertEqual(self.store.snapshot("초보용사")["growth_assessment"]["goal"], "테스트 목표")

    def test_unchanged_api_refresh_keeps_assessment_but_changed_data_or_capture_invalidates(self):
        self.store.record_api_observation("초보용사", {}, self.api_result())
        assessment = {"stage": "unknown", "goal": "테스트", "rationale": ["테스트"], "confidence": "low"}
        self.store.record_assessment("초보용사", assessment, 1)
        self.store.record_api_observation("초보용사", {}, self.api_result())
        self.assertFalse(self.store.snapshot("초보용사")["growth_assessment"]["needs_review"])
        self.store.record_api_observation("초보용사", {}, self.api_result(data={"sample": "changed"}))
        self.assertTrue(self.store.snapshot("초보용사")["growth_assessment"]["needs_review"])
        self.store.record_assessment("초보용사", assessment, 2)
        self.store.record_observation(self.observation(self.capture()))
        self.assertTrue(self.store.snapshot("초보용사")["growth_assessment"]["needs_review"])

    def test_context_schema_distinguishes_zero_null_and_omitted_and_rejects_inferred_fields(self):
        self.assertEqual(PlayerContextPatch(budget_meso=0, goal=None).model_dump(exclude_unset=True), {"goal": None, "budget_meso": 0})
        for value in ({"budget_meso": True}, {"daily_play_minutes": 1441}, {"stage": "advanced"}, {"boss_experience": [{"boss_name": "테스트", "status": "cleared"}]}):
            with self.assertRaises(ValidationError):
                PlayerContextPatch(**value)

    def test_explicit_zero_spending_budget_is_available_without_guessing_inventory(self):
        self.store.record_observation(self.observation(self.capture(), meso=None, starforce=10))
        self.store.update_context("초보용사", {"budget_meso": 0})
        context = MapleService(self.config).upgrade_context()
        self.assertEqual(context["spending_budget"]["value"], 0)
        self.assertEqual(context["spending_budget"]["source"], "user_input")
        self.assertNotIn("inventory_meso", context["missing_fields"])
        self.assertIsNone(context["player_state"]["inventory_meso"])


class ResetTests(MateCase):
    def populate(self):
        self.store.set("unknown_existing_record", {"preserve": True})
        for name in ("초보용사", "다른용사"):
            self.store.update_context(name, {"goal": "보스 솔플", "budget_meso": 500_000_000, "daily_play_minutes": 60})
            self.store.record_observation(self.observation(self.capture(), name, meso=123))
            self.store.record_api_observation(name, {}, {"endpoint": "character/stat", "fetched_at": utc_now(), "data": {"test": 1}})
            self.store.record_assessment(name, {"stage": "intermediate"}, self.store.snapshot(name)["revision"])

    def test_context_reset_preserves_evidence_other_characters_and_key(self):
        self.populate()
        save_key(self.directory, "synthetic-reset-key")
        encrypted = (self.directory / "nexon-key.dpapi").read_bytes()
        before, other = self.store.snapshot("초보용사"), self.store.snapshot("다른용사")
        self.store.reset_player_state("초보용사", "context", before["revision"])
        after = self.store.snapshot("초보용사")
        self.assertEqual(after["user_context"], {})
        self.assertEqual(after["context_facts"], {})
        self.assertIsNone(after["growth_assessment"])
        for field in ("value", "source", "observed_at", "is_current_balance"):
            self.assertEqual(after["inventory_meso"][field], before["inventory_meso"][field])
        self.assertEqual(after["screen_observation"], before["screen_observation"])
        self.assertEqual(after["api_observations"][0]["fetched_at"], before["api_observations"][0]["fetched_at"])
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(self.store.snapshot("다른용사")["user_context"], other["user_context"])
        self.assertEqual(self.store.snapshot("다른용사")["revision"], other["revision"])
        self.assertEqual(self.store.get("unknown_existing_record"), {"preserve": True})
        self.assertEqual((self.directory / "nexon-key.dpapi").read_bytes(), encrypted)
        self.assertEqual(self.store.character_names(), ["다른용사", "초보용사"])
        for stale_write in (
            lambda: self.store.update_context("초보용사", {"goal": "버전 없는 오래된 목표"}),
            lambda: self.store.update_context("초보용사", {"goal": "오래된 목표"}, before["revision"]),
            lambda: self.store.record_assessment("초보용사", {"stage": "advanced"}, before["revision"]),
            lambda: self.store.reset_player_state("초보용사", "all", before["revision"]),
        ):
            with self.assertRaises(ValueError):
                stale_write()
        self.store.update_context("초보용사", {"goal": "새 목표"}, after["revision"])
        self.assertEqual(self.store.snapshot("초보용사")["user_context"]["goal"], "새 목표")

    def test_full_reset_preserves_other_character_and_blocks_inflight_results(self):
        self.populate()
        old_capture, old_fetch = self.capture(), utc_now()
        before = self.store.snapshot("초보용사")
        other_screen = self.store.snapshot("다른용사")["screen_observation"]
        self.store.reset_player_state("초보용사", "all", before["revision"])
        after = self.store.snapshot("초보용사")
        for key in ("inventory_meso", "screen_observation", "growth_assessment"):
            self.assertIsNone(after[key])
        self.assertEqual(after["api_observations"], [])
        self.assertEqual(after["user_context"], {})
        self.assertEqual(self.store.get("screen_observation"), other_screen)
        with self.assertRaisesRegex(ValueError, "predates"):
            self.store.record_observation(self.observation(old_capture))
        api = {"endpoint": "character/stat", "fetched_at": old_fetch, "data": {"test": 2}}
        self.assertEqual(self.store.record_api_observation("초보용사", {}, api)["reason"], "before_state_reset")
        # A later context-only reset must retain the earlier full-reset barrier.
        self.store.reset_player_state("초보용사", "context", after["revision"])
        self.assertEqual(self.store.record_api_observation("초보용사", {}, api)["reason"], "before_state_reset")
        self.store.record_observation(self.observation(self.capture(), meso=456))
        api["fetched_at"] = utc_now()
        self.assertTrue(self.store.record_api_observation("초보용사", {}, api)["saved"])
        self.assertEqual(self.store.snapshot("초보용사")["inventory_meso"]["value"], 456)

    def test_full_reset_clears_matching_legacy_identity_and_capture_metadata(self):
        self.store.set("last_browser_character", "초보용사")
        self.store.set("active_character", {"name": "초보용사"})
        self.store.set("observer", {"last_frame_at": utc_now()})
        capture = self.capture()
        self.store.record_observation(self.observation(capture))
        self.store.reset_player_state("초보용사", "all", self.store.snapshot("초보용사")["revision"])
        self.assertIsNone(self.store.snapshot()["last_observed_character"])
        self.assertIsNone(self.store.get("observer"))
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT id FROM captures WHERE id=?", (capture["screenshot_id"],)).fetchone())
        self.assertEqual(len(self.store.get("registered_characters")), 2)

    def test_manager_reset_checks_revision_and_refuses_busy_capture(self):
        buffer = io.BytesIO()
        Image.new("RGB", (20, 10), "green").save(buffer, format="PNG")
        manager = CaptureManager(self.store, lambda: CapturedFrame(buffer.getvalue(), utc_now()))
        manager.get()
        manager.reset_player_state("초보용사", "context", 0)
        with self.assertRaises(ValueError):
            manager.reset_player_state("초보용사", "all", 0)
        manager.lock.acquire()
        try:
            with self.assertRaises(CaptureError):
                manager.reset_player_state("초보용사", "all", 1)
        finally:
            manager.lock.release()
        manager.reset_player_state("초보용사", "all", 1)
        self.assertIsNone(manager.status()["saved_capture"])
        self.assertEqual(list(self.directory.glob("*.png")), [])


class CaptureTests(MateCase):
    def test_capture_occurs_only_on_request_and_pixels_are_not_retained(self):
        calls = []
        buffer = io.BytesIO()
        Image.new("RGB", (200, 100), "green").save(buffer, format="PNG")
        def capture():
            calls.append(True)
            return CapturedFrame(buffer.getvalue(), utc_now())
        manager = CaptureManager(self.store, capture)
        self.assertEqual(calls, [])
        with self.assertRaises(CaptureError):
            manager.get(use_saved=True)
        self.assertEqual(calls, [])
        first, second = manager.get(), manager.get()
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first.metadata["screenshot_id"], second.metadata["screenshot_id"])
        self.assertEqual(second.metadata["delivery"], "requested")
        self.assertIsNone(manager.status()["saved_capture"])
        self.assertFalse(hasattr(manager, "saved"))
        self.assertEqual(list(self.directory.glob("*.png")), [])
        with self.assertRaises(CaptureError):
            manager.get(use_saved=True)
        self.assertEqual(len(calls), 2)
        manager.close()
        with self.assertRaises(CaptureError):
            manager.get()
        self.assertEqual(len(calls), 2)

    def test_busy_capture_cannot_start_another_session(self):
        manager = CaptureManager(self.store)
        manager.lock.acquire()
        try:
            with self.assertRaises(CaptureError):
                manager.get()
        finally:
            manager.lock.release()
