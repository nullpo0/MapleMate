from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from maplemate.config import Config
from maplemate.maple_mcp.damage import resolve_profile
from maplemate.maple_mcp.damage_inputs import assemble_damage_inputs, number
from maplemate.maple_mcp.models import DamageAssemblyInputs
from maplemate.maple_mcp.service import MapleService
from maplemate.state import utc_now

ROOT = Path(__file__).resolve().parents[1] / ".cache" / "damage-input-tests"


def observations():
    values = {"STR": "10000", "DEX": "1000", "공격력": "2000", "데미지": "100", "보스 몬스터 데미지": "300",
              "일반 몬스터 데미지": "20", "크리티컬 확률": "105", "크리티컬 데미지": "50", "최종 데미지": "20",
              "방어율 무시": "90", "속성 내성 무시": "5"}
    stat = {"final_stat": [{"stat_name": key, "stat_value": value} for key, value in values.items()], "character_class": "아델"}
    basic = {"character_name": "초보용사", "character_class": "아델"}
    return {endpoint: {"source": "nexon_open_api", "endpoint": endpoint, "data": data, "data_date": "2026-10-10T00:00+09:00", "fetched_at": utc_now()}
            for endpoint, data in (("character/basic", basic), ("character/stat", stat))}


def inputs(**changes):
    return DamageAssemblyInputs(**{"stat_weights": {"STR": 4, "DEX": 1}, "attack_stat": "공격력",
        "weapon_constant": 1.3, "conditions": "Synthetic same preset and buff conditions", **changes})


class DamageInputTests(unittest.TestCase):
    def test_manual_inputs_need_observation_date_and_reject_mixed_dates(self):
        manual = dict(stat_factor=41000, attack_power=2000, damage_percent=100, boss_damage_percent=300,
            normal_monster_damage_percent=20, critical_rate_percent=100, critical_damage_percent=50,
            final_damage_sources=[20], ignore_defense_sources=[90], elemental_ignore_percent=5)
        result = assemble_damage_inputs("초보용사", {}, inputs(**manual))
        self.assertIsNone(result["profile"])
        self.assertIn("data_as_of", result["missing_fields"])
        result = assemble_damage_inputs("초보용사", {}, inputs(**manual, data_as_of="2026-10-10"))
        self.assertEqual(result["status"], "ready_for_total_stat_comparison")
        self.assertEqual(result["data_as_of_kind"], "client_supplied")
        result = assemble_damage_inputs("초보용사", observations(), inputs(data_as_of="2026-10-09"))
        self.assertIsNone(result["profile"])
        with self.assertRaises(ValueError):
            assemble_damage_inputs("초보용사", {}, inputs(**manual, data_as_of="2026-13-40"))

    def test_api_totals_are_used_once_and_source_dates_remain_attached(self):
        data = observations()
        data["character/item-equipment"] = {**data["character/basic"], "endpoint": "character/item-equipment", "data": {"item_equipment": [{"item_total_option": {"str": "1000", "attack_power": "200"}}]}}
        result = assemble_damage_inputs("초보용사", data, inputs())
        self.assertEqual(result["status"], "ready_for_total_stat_comparison")
        self.assertEqual(result["profile"]["stat_factor"], 41000)
        self.assertEqual(result["profile"]["attack_power"], 2000)
        self.assertEqual(result["profile"]["critical_rate_percent"], 100)
        self.assertEqual(result["field_provenance"]["attack_power"]["data_date"], "2026-10-10T00:00+09:00")
        self.assertIn("character/item-equipment", result["auxiliary_data"])

    def test_missing_or_ambiguous_api_values_do_not_become_zero(self):
        data = observations()
        stats = data["character/stat"]["data"]["final_stat"]
        stats[:] = [entry for entry in stats if entry["stat_name"] != "일반 몬스터 데미지"]
        stats.append({"stat_name": "공격력", "stat_value": "9999"})
        result = assemble_damage_inputs("초보용사", data, inputs())
        self.assertIsNone(result["profile"])
        self.assertIn("normal_monster_damage_percent", result["missing_fields"])
        self.assertIn("attack_power", result["missing_fields"])
        fixed = assemble_damage_inputs("초보용사", data, inputs(normal_monster_damage_percent=0, attack_power=2000))
        self.assertEqual(fixed["status"], "ready_for_total_stat_comparison")
        self.assertEqual(fixed["field_provenance"]["attack_power"]["source"], "client_supplied")

    def test_date_and_character_conflicts_block_ready_profiles(self):
        data = observations()
        data["character/basic"]["data_date"] = "2026-10-09T00:00+09:00"
        self.assertIsNone(assemble_damage_inputs("초보용사", data, inputs())["profile"])
        data = observations()
        data["character/basic"]["data"]["character_name"] = "다른용사"
        result = assemble_damage_inputs("초보용사", data, inputs())
        self.assertIsNone(result["profile"])
        self.assertTrue(result["consistency_issues"])

    def test_percent_breakdown_requires_full_exempt_values_and_anchors_to_total(self):
        partial = assemble_damage_inputs("초보용사", observations(), inputs(stat_percent_bonuses={"STR": 100}))
        self.assertIsNone(partial["detailed_profile"])
        self.assertIn("stat_flat_after_percent.STR", partial["breakdown_missing_fields"])
        result = assemble_damage_inputs("초보용사", observations(), inputs(
            stat_percent_bonuses={"STR": 100, "DEX": 0}, stat_flat_after_percent={"STR": 3000, "DEX": 0},
            attack_percent_bonus=100, attack_flat_after_percent=100))
        self.assertEqual(result["detailed_profile"]["stat_terms"][0]["value"]["percent_affected"], 3500)
        from maplemate.maple_mcp.models import DetailedDamageProfile
        normalized = resolve_profile(DetailedDamageProfile(**result["detailed_profile"]))
        self.assertEqual(normalized.stat_factor, 41000)
        self.assertEqual(normalized.attack_power, 2000)

    def test_special_hp_job_requires_verified_effective_stat_formula(self):
        data = observations()
        for entry in data.values():
            entry["data"]["character_class"] = "데몬어벤져"
        data["character/stat"]["data"]["final_stat"].append({"stat_name": "HP", "stat_value": "500000"})
        result = assemble_damage_inputs("초보용사", data, inputs(stat_weights={"HP": 4}))
        self.assertIn("stat_factor", result["missing_fields"])
        result = assemble_damage_inputs("초보용사", data, inputs(stat_weights=None, stat_factor=123456))
        self.assertEqual(result["profile"]["stat_factor"], 123456)

    def test_number_parsing_does_not_execute_or_accept_unknown_values(self):
        self.assertEqual(number("10,000.5"), 10000.5)
        self.assertEqual(number("90.5%"), 90.5)
        for value in ("1,23", "NaN", "Infinity", "2+3", "<script>1</script>", True, float("nan")):
            self.assertIsNone(number(value))

    def test_cached_context_has_no_network_and_refresh_queries_only_requested_sections(self):
        directory = ROOT / uuid.uuid4().hex
        service = MapleService(Config.load(directory))
        data = observations()
        try:
            for entry in data.values():
                service.store.record_api_observation("초보용사", {}, entry)
            with patch("maplemate.maple_mcp.service.load_key", side_effect=AssertionError("No API call expected")):
                result = service.damage_context("초보용사", inputs(), refresh=False)
            self.assertEqual(result["status"], "ready_for_total_stat_comparison")
            with patch.object(service, "api", side_effect=lambda endpoint, *_: {"status": "ok", **data.get(endpoint, {"endpoint": endpoint, "data": {}})}) as api:
                result = service.damage_context("초보용사", inputs(), refresh=True, additional_sections=["item-equipment"])
                self.assertEqual([call.args[0] for call in api.call_args_list], ["character/basic", "character/stat", "character/item-equipment"])
            with patch.object(service, "api") as api:
                with self.assertRaises(ValueError):
                    service.damage_context("초보용사", inputs(), additional_sections=["history/cube"])
                api.assert_not_called()
        finally:
            assert directory.resolve().parent == ROOT.resolve()
            shutil.rmtree(directory)
