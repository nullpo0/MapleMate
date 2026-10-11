import math
import unittest
from pydantic import ValidationError
from maplemate.maple_mcp.damage import apply_option_change, calculate_damage_gain, calculate_option_damage_gain, calculate_skill_damage_gain, resolve_profile
from maplemate.maple_mcp.models import DamageOptionChange, DamageProfile, DamageReference, DetailedDamageProfile, SkillDamageComparison


def profile(**changes):
    return DamageProfile(**{
        "stat_factor": 41000.0, "attack_power": 1000.0,
        "damage_percent": 100.0, "boss_damage_percent": 300.0,
        "normal_monster_damage_percent": 0.0, "critical_rate_percent": 100.0,
        "critical_damage_percent": 50.0, "final_damage_sources": [20.0],
        "ignore_defense_sources": [90.0], **changes,
    })


class DamageTests(unittest.TestCase):
    def calculate(self, before=None, after=None, **changes):
        return calculate_damage_gain(**{
            "before": before or profile(), "after": after or profile(),
            "target_defense_percent": 300.0, "is_boss": True,
            "character_name": "초보용사", "data_as_of": "2026-10-10",
            "source_urls": ["https://maplestory.nexon.com/Guide/N23GameInformation/Articles/390"],
            "rules_as_of": "2026-10-10", "assumptions": ["Synthetic inputs for tests."], **changes,
        })

    def test_independent_final_damage_and_cost(self):
        result = self.calculate(after=profile(final_damage_sources=[20.0, 10.0]), cost_meso=200000000)
        self.assertAlmostEqual(result["damage_ratio"], 1.1)
        self.assertAlmostEqual(result["equivalent_final_damage_gain_percent"], 10)
        self.assertAlmostEqual(result["gain_percent_per_100m_meso"], 5)
        total = self.calculate(after=profile(final_damage_sources=[32.0]))
        self.assertAlmostEqual(total["damage_ratio"], result["damage_ratio"])

    def test_boss_damage_is_additive_and_target_specific(self):
        after = profile(boss_damage_percent=340.0)
        self.assertAlmostEqual(self.calculate(after=after)["damage_ratio"], 5.4 / 5)
        self.assertEqual(self.calculate(after=after, is_boss=False)["damage_ratio"], 1)
        self.assertAlmostEqual(self.calculate(after=profile(normal_monster_damage_percent=50.0), is_boss=False)["damage_ratio"], 2.5 / 2)

    def test_ignore_defense_is_multiplicative_not_percentage_point_addition(self):
        result = self.calculate(after=profile(ignore_defense_sources=[90.0, 30.0]))
        self.assertAlmostEqual(result["before_multipliers"]["defense"], 0.7)
        self.assertAlmostEqual(result["after_multipliers"]["defense"], 0.79)
        self.assertAlmostEqual(result["damage_ratio"], 0.79 / 0.7)
        self.assertAlmostEqual(self.calculate(after=profile(ignore_defense_sources=[93.0]))["damage_ratio"], result["damage_ratio"])

    def test_critical_rate_weighting(self):
        result = self.calculate(before=profile(critical_rate_percent=50.0), after=profile())
        self.assertAlmostEqual(result["damage_ratio"], 1.85 / 1.425)
        result = self.calculate(before=profile(critical_rate_percent=0.0), after=profile(critical_rate_percent=0.0, critical_damage_percent=100.0))
        self.assertEqual(result["damage_ratio"], 1)

    def test_stat_attack_and_losses_combine(self):
        result = self.calculate(after=profile(stat_factor=45100.0, attack_power=1200.0, final_damage_sources=[]))
        self.assertAlmostEqual(result["damage_ratio"], 1.1 * 1.2 / 1.2)
        self.assertLess(self.calculate(after=profile(attack_power=900.0))["equivalent_final_damage_gain_percent"], 0)

    def test_zero_baseline_unknown_cost_and_free_alternative(self):
        result = self.calculate(before=profile(ignore_defense_sources=[0.0]))
        self.assertEqual(result["status"], "undefined_baseline")
        self.assertIsNone(result["damage_ratio"])
        self.assertIsNone(result["equivalent_final_damage_gain_percent"])
        self.assertIsNone(self.calculate()["gain_percent_per_100m_meso"])
        result = self.calculate(cost_meso=0)
        self.assertTrue(result["is_free"])
        self.assertIsNone(result["gain_percent_per_100m_meso"])

    def test_invalid_unknown_or_nonfinite_inputs_are_rejected(self):
        for options in ({"final_damage_sources": [math.nan]}, {"final_damage_sources": [-100.0]},
                        {"ignore_defense_sources": [101.0]}):
            with self.assertRaises(ValueError):
                self.calculate(after=profile(**options))
        for options in ({"target_defense_percent": math.inf}, {"cost_meso": -1},
                        {"source_urls": []}, {"assumptions": []}, {"data_as_of": "unknown"}):
            with self.assertRaises(ValueError):
                self.calculate(**options)
        for options in ({"critical_rate_percent": 101.0}, {"attack_power": math.nan}):
            with self.assertRaises(ValidationError):
                profile(**options)
        with self.assertRaises(ValidationError):
            DamageProfile(stat_factor=1.0)

    def test_pasted_article_examples_with_independent_expected_results(self):
        # Source: user-pasted https://www.fmkorea.com/8336709224, not fetched text.
        cases = [
            (profile(attack_power=130), profile(attack_power=139), {}, 90 / 13),
            (profile(damage_percent=50, boss_damage_percent=100), profile(damage_percent=59, boss_damage_percent=100), {}, 3.6),
            (profile(final_damage_sources=[2]), profile(final_damage_sources=[2, 3]), {}, 3),
            (profile(ignore_defense_sources=[80]), profile(ignore_defense_sources=[80, 30]), {}, 45),
            (profile(ignore_defense_sources=[95]), profile(ignore_defense_sources=[95, 30]), {}, 90 / 17),
            (profile(critical_damage_percent=0), profile(critical_damage_percent=10), {}, 200 / 27),
            (profile(elemental_ignore_percent=0), profile(elemental_ignore_percent=5), {"target_elemental_resistance_percent": 50}, 5),
        ]
        for before, after, options, expected in cases:
            with self.subTest(expected=expected):
                self.assertAlmostEqual(self.calculate(before, after, **options)["equivalent_final_damage_gain_percent"], expected, places=10)

    def detailed(self, **changes):
        factors = profile().model_dump(exclude={"stat_factor", "attack_power"})
        return DetailedDamageProfile(**{**factors,
            "stat_terms": [
                {"name": "STR", "coefficient": 4, "value": {"percent_affected": 1000, "percent_bonus": 100, "flat_after_percent": 3000}},
                {"name": "DEX", "coefficient": 1, "value": {"percent_affected": 100, "percent_bonus": 0, "flat_after_percent": 100}}],
            "attack": {"percent_affected": 100, "percent_bonus": 30, "flat_after_percent": 10}, **changes})

    def test_percent_changes_preserve_symbol_and_level_ability_exempt_values(self):
        before = self.detailed()
        after = apply_option_change(before, DamageOptionChange(stat_percent_deltas={"STR": 12}, attack_percent_delta=9))
        self.assertEqual(after.stat_terms[0].value.flat_after_percent, 3000)
        self.assertEqual(after.attack.flat_after_percent, 10)
        result = self.calculate(before, after)
        self.assertAlmostEqual(result["damage_ratio"], 20680 / 20200 * 149 / 140)
        self.assertEqual(resolve_profile(after).stat_factor, 20680)

    def test_option_replacement_removes_old_defense_source_from_api_total(self):
        before = profile(ignore_defense_sources=[93])  # existing 90% plus 30% line
        after = apply_option_change(before, DamageOptionChange(remove_ignore_defense_sources=[30], add_ignore_defense_sources=[40]))
        self.assertAlmostEqual(after.ignore_defense_sources[0], 94)
        self.assertAlmostEqual(self.calculate(before, after)["damage_ratio"], 0.82 / 0.79)
        with self.assertRaises(ValueError):
            apply_option_change(profile(ignore_defense_sources=[20]), DamageOptionChange(remove_ignore_defense_sources=[30], add_ignore_defense_sources=[40]))
        with self.assertRaises(ValueError):
            apply_option_change(profile(ignore_defense_sources=[100]), DamageOptionChange(remove_ignore_defense_sources=[100]))

    def test_weapon_and_confirmed_combat_changes_are_factors(self):
        result = self.calculate(before=profile(weapon_constant=1.2), after=profile(weapon_constant=1.3, combat_multiplier=1.05))
        self.assertAlmostEqual(result["damage_ratio"], 1.3 / 1.2 * 1.05)
        with self.assertRaises(ValueError):
            self.calculate(after=profile(elemental_ignore_percent=5))

    def test_option_deltas_need_real_breakdown_and_reject_double_counted_stats(self):
        for change in (DamageOptionChange(attack_percent_delta=12), DamageOptionChange(stat_percent_deltas={"STR": 12})):
            with self.assertRaises(ValueError):
                apply_option_change(profile(), change)
        before = self.detailed()
        with self.assertRaises(ValueError):
            apply_option_change(before, DamageOptionChange(stat_percent_deltas={"LUK": 12}))
        duplicate = before.model_copy(update={"stat_terms": [before.stat_terms[0], before.stat_terms[0]]})
        with self.assertRaises(ValueError):
            self.calculate(duplicate, before)

    def test_reference_check_reports_error_without_adjusting_model(self):
        reference = DamageReference(label="article example", source_url="https://www.fmkorea.com/8336709224", gain_percent=6.9, conditions="attack 100, attack% 30->39, no exempt attack")
        result = self.calculate(profile(attack_power=130), profile(attack_power=139), reference=reference)
        self.assertEqual(result["reference_check"]["status"], "within_tolerance")
        self.assertAlmostEqual(result["reference_check"]["gain_difference_pp"], 90 / 13 - 6.9)
        reference = reference.model_copy(update={"gain_percent": 3.0})
        result = self.calculate(profile(attack_power=130), profile(attack_power=139), reference=reference)
        self.assertEqual(result["reference_check"]["status"], "outside_tolerance")
        self.assertAlmostEqual(result["equivalent_final_damage_gain_percent"], 90 / 13)
        self.assertFalse(result["reference_check"]["validated_against_maplescouter"])

    def test_skill_weighting_uses_per_skill_defense_and_original_damage_shares(self):
        conditions = {
            "target_defense_percent": 300, "is_boss": True, "character_name": "초보용사", "data_as_of": "2026-10-10",
            "source_urls": ["https://www.fmkorea.com/8336709224"], "rules_as_of": "2026-10-10", "assumptions": ["Synthetic cycle"]}
        skills = [SkillDamageComparison(skill_name="A", baseline_damage_share_percent=60,
                    before=profile(ignore_defense_sources=[80]), after=profile(ignore_defense_sources=[80, 30])),
                  SkillDamageComparison(skill_name="B", baseline_damage_share_percent=40,
                    before=profile(ignore_defense_sources=[95]), after=profile(ignore_defense_sources=[95, 30]))]
        result = calculate_skill_damage_gain(skills, **conditions)
        self.assertAlmostEqual(result["damage_ratio"], 0.6 * 1.45 + 0.4 * (0.895 / 0.85))
        self.assertNotEqual(result["damage_ratio"], 1.45)
        with self.assertRaises(ValueError):
            calculate_skill_damage_gain(skills[:1], **conditions)
