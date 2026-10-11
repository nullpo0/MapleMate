"""Relative expected damage under explicit, equal combat conditions."""
from datetime import date, datetime
import math
from urllib.parse import urlparse
from .models import DamageOptionChange, DamageProfile, DamageReference, DetailedDamageProfile, ScalableValue, SkillDamageComparison

MODEL_VERSION = "damage-v2-eight-factors"


def scaled_value(value: ScalableValue) -> float:
    result = value.percent_affected * (1 + value.percent_bonus / 100) + value.flat_after_percent
    return math.floor(result) if value.rounding == "floor" else result


def resolve_profile(profile: DamageProfile | DetailedDamageProfile) -> DamageProfile:
    if isinstance(profile, DamageProfile):
        return profile
    names = [term.name for term in profile.stat_terms]
    if len(set(names)) != len(names):
        raise ValueError("A stat term must not be counted twice")
    factors = profile.model_dump(exclude={"stat_terms", "attack"})
    return DamageProfile(**factors,
        stat_factor=sum(term.coefficient * scaled_value(term.value) for term in profile.stat_terms),
        attack_power=scaled_value(profile.attack))


def multipliers(profile: DamageProfile, defense_percent: float, is_boss: bool,
                elemental_resistance_percent: float | None = None) -> dict[str, float]:
    if any(not math.isfinite(value) or not -100 < value <= 10000 for value in profile.final_damage_sources):
        raise ValueError("Final damage sources must be finite percentages greater than -100 and at most 10000.")
    if any(not math.isfinite(value) or not 0 <= value <= 100 for value in profile.ignore_defense_sources):
        raise ValueError("Ignore defense sources must be finite percentages between 0 and 100.")
    residual_defense = math.prod(1 - value / 100 for value in profile.ignore_defense_sources)
    return {
        "stat": profile.stat_factor,
        "weapon": profile.weapon_constant,
        "attack": profile.attack_power,
        "damage": 1 + (profile.damage_percent + (profile.boss_damage_percent if is_boss else profile.normal_monster_damage_percent)) / 100,
        "critical": 1 + profile.critical_rate_percent / 100 * (0.35 + profile.critical_damage_percent / 100),
        "final_damage": math.prod(1 + value / 100 for value in profile.final_damage_sources),
        "defense": max(0.0, 1 - defense_percent / 100 * residual_defense),
        "elemental": (1 - elemental_resistance_percent / 100 * (1 - profile.elemental_ignore_percent / 100)
                      if elemental_resistance_percent is not None else 1.0),
        "combat": profile.combat_multiplier,
    }


def calculate_damage_gain(before: DamageProfile | DetailedDamageProfile, after: DamageProfile | DetailedDamageProfile, target_defense_percent: float,
                          is_boss: bool, character_name: str, data_as_of: str, source_urls: list[str],
                          rules_as_of: str, assumptions: list[str], cost_meso: int | None = None,
                          target_elemental_resistance_percent: float | None = None,
                          reference: DamageReference | None = None) -> dict:
    if not math.isfinite(target_defense_percent) or not 0 <= target_defense_percent <= 10000:
        raise ValueError("Supply the target's defense percentage between 0 and 10000.")
    if target_elemental_resistance_percent is not None and (not math.isfinite(target_elemental_resistance_percent) or not 0 <= target_elemental_resistance_percent <= 100):
        raise ValueError("Elemental resistance must be between 0 and 100, or null if not modeled.")
    if cost_meso is not None and (type(cost_meso) is not int or not 0 <= cost_meso <= 9_000_000_000_000_000_000):
        raise ValueError("Cost must be a nonnegative integer meso amount, or null when unknown.")
    date.fromisoformat(rules_as_of)
    # API snapshots may have a date only; screen observations have an ISO timestamp.
    datetime.fromisoformat(data_as_of)
    if not character_name.strip() or len(character_name) > 30:
        raise ValueError("Supply the character name for these inputs.")
    if not source_urls or any(urlparse(url).scheme != "https" or not urlparse(url).hostname for url in source_urls):
        raise ValueError("Supply HTTPS sources for the stats and calculation assumptions.")
    if not assumptions or any(not value.strip() for value in assumptions):
        raise ValueError("Describe the combat conditions, stat derivation and excluded effects.")
    resolved_before, resolved_after = resolve_profile(before), resolve_profile(after)
    if target_elemental_resistance_percent is None and resolved_before.elemental_ignore_percent != resolved_after.elemental_ignore_percent:
        raise ValueError("Supply target elemental resistance when comparing elemental ignore changes.")
    old = multipliers(resolved_before, target_defense_percent, is_boss, target_elemental_resistance_percent)
    new = multipliers(resolved_after, target_defense_percent, is_boss, target_elemental_resistance_percent)
    ratios = {key: new[key] / old[key] if old[key] else None for key in old}
    ratio = math.prod(ratios.values()) if all(value is not None for value in ratios.values()) else None
    if ratio is not None and not math.isfinite(ratio):
        raise ValueError("The supplied inputs exceed the numerical range for a reliable comparison.")
    gain = (ratio - 1) * 100 if ratio is not None else None
    reference_check = compare_reference(gain, reference)
    return {
        "source": "calculation_from_supplied_inputs", "model_version": MODEL_VERSION, "character_name": character_name,
        "data_as_of": data_as_of, "realtime": False,
        "status": "ok" if ratio is not None else "undefined_baseline",
        "damage_ratio": ratio, "equivalent_final_damage_gain_percent": gain,
        "before_multipliers": old, "after_multipliers": new, "component_ratios": ratios,
        "cost_meso": cost_meso,
        "gain_percent_per_100m_meso": gain * 100_000_000 / cost_meso if gain is not None and cost_meso else None,
        "is_free": cost_meso == 0,
        "target": {"defense_percent": target_defense_percent, "is_boss": is_boss,
                   "elemental_resistance_percent": target_elemental_resistance_percent},
        "inputs": {"before": before.model_dump(), "after": after.model_dump()},
        "resolved_profiles": {"before": resolved_before.model_dump(), "after": resolved_after.model_dump()},
        "reference_check": reference_check,
        "source_urls": source_urls, "rules_as_of": rules_as_of, "assumptions": assumptions,
        "limitations": [
            "동일한 직업·스킬·버프·대상 조건에서의 평균 데미지 비율이며 스탯창 최종뎀 자체의 변화량이 아닙니다.",
            "기본 크리티컬 데미지 20~50%의 평균 35%를 사용합니다. 스킬별 추가 방무·데미지는 입력에 반영해야 합니다.",
            "무기상수·속성 반감은 명시한 값만 반영합니다. 미지정 속성 반감은 생략되며, 기본 무기상수/전투 배율 1은 전후 동일 조건이 상쇄되는 정규화 비교용입니다.",
            "스킬별 점유율·방무·크확 차이는 별도 스킬 가중 비교가 필요합니다. 쿨타임·버프 지속·타수·포스·레벨·상한 효과는 확인한 전투 배율 없이는 자동 계산하지 않습니다.",
            "환산주스탯의 직업 사이클·HEXA·도핑 모델과 동등하다는 주장은 아닙니다. 외부 비교값이 없으면 사이트와의 오차는 미검증입니다.",
            "입력값·출처·현재 규칙의 유효성은 서버가 검증하지 않습니다. 목표 달성이나 예산 내 강화 성공을 보장하지 않습니다.",
        ],
    }


def compare_reference(gain: float | None, reference: DamageReference | None) -> dict:
    if reference is None:
        return {"status": "not_provided", "validated_against_maplescouter": False}
    if urlparse(reference.source_url).scheme != "https" or not urlparse(reference.source_url).hostname:
        raise ValueError("Supply an HTTPS reference URL")
    difference = gain - reference.gain_percent if gain is not None else None
    reference_ratio = 1 + reference.gain_percent / 100
    return {"status": ("within_tolerance" if abs(difference) <= reference.max_gain_difference_pp else "outside_tolerance") if difference is not None else "undefined",
            "label": reference.label, "source_url": reference.source_url, "conditions": reference.conditions,
            "reference_gain_percent": reference.gain_percent, "gain_difference_pp": difference,
            "damage_ratio_error_percent": difference / reference_ratio if difference is not None else None,
            "max_gain_difference_pp": reference.max_gain_difference_pp,
            "source": "client_supplied_reference", "conditions_verified_by_server": False,
            "validated_against_maplescouter": False,
            "validation_scope": "This one supplied case only; no claim of site-wide equivalence"}


def apply_option_change(before: DamageProfile | DetailedDamageProfile, change: DamageOptionChange):
    # Source removal is algebraic, including when the API supplied only a total.
    # The client must verify that the removed effect really belongs to that total.
    base = resolve_profile(before)
    multipliers(base, 0, True, 0)
    values = before.model_dump()
    raw_attack_changes = (change.attack_percent_delta, change.attack_affected_delta, change.attack_flat_after_percent_delta)
    if not isinstance(before, DetailedDamageProfile) and (change.stat_percent_deltas or change.stat_affected_deltas or change.stat_flat_after_percent_deltas or any(raw_attack_changes)):
        raise ValueError("Stat/attack option changes require the confirmed percentage/exempt breakdown; use DetailedDamageProfile")
    if isinstance(before, DetailedDamageProfile):
        names = {term.name for term in before.stat_terms}
        if any(key not in names for group in (change.stat_percent_deltas, change.stat_affected_deltas, change.stat_flat_after_percent_deltas) for key in group):
            raise ValueError("A changed stat is missing from the supplied breakdown")
        for term in values["stat_terms"]:
            key = term["name"]
            for field, deltas in (("percent_bonus", change.stat_percent_deltas), ("percent_affected", change.stat_affected_deltas), ("flat_after_percent", change.stat_flat_after_percent_deltas)):
                term["value"][field] += deltas.get(key, 0)
        for field, delta in zip(("percent_bonus", "percent_affected", "flat_after_percent"), raw_attack_changes):
            values["attack"][field] += delta
    for field in ("damage_percent", "boss_damage_percent", "normal_monster_damage_percent", "critical_rate_percent", "critical_damage_percent", "elemental_ignore_percent"):
        values[field] += getattr(change, field + "_delta")
    values["critical_rate_percent"] = min(values["critical_rate_percent"], 100)
    values["combat_multiplier"] *= change.additional_combat_multiplier
    if change.weapon_constant_after is not None:
        values["weapon_constant"] = change.weapon_constant_after
    fd_remove, fd_add = change.remove_final_damage_sources, change.add_final_damage_sources
    if any(not -100 < source <= 10000 for source in [*fd_remove, *fd_add]):
        raise ValueError("Invalid final damage source")
    if fd_remove or fd_add:
        factor = math.prod(1 + source / 100 for source in base.final_damage_sources)
        factor /= math.prod(1 + source / 100 for source in fd_remove)
        factor *= math.prod(1 + source / 100 for source in fd_add)
        values["final_damage_sources"] = [(factor - 1) * 100]
    ied_remove, ied_add = change.remove_ignore_defense_sources, change.add_ignore_defense_sources
    if any(not 0 <= source <= 100 for source in [*ied_remove, *ied_add]):
        raise ValueError("Invalid ignore defense source")
    if 100 in ied_remove:
        raise ValueError("Cannot infer remaining defense ignore after removing a 100% source; supply the after profile")
    if ied_remove or ied_add:
        residual = math.prod(1 - source / 100 for source in base.ignore_defense_sources)
        residual /= math.prod(1 - source / 100 for source in ied_remove)
        if not 0 <= residual <= 1 + 1e-12:
            raise ValueError("Removed sources are inconsistent with total defense ignore")
        residual = min(residual, 1) * math.prod(1 - source / 100 for source in ied_add)
        values["ignore_defense_sources"] = [(1 - min(residual, 1)) * 100]
    return type(before)(**values)


def calculate_option_damage_gain(before: DamageProfile | DetailedDamageProfile, change: DamageOptionChange, **conditions) -> dict:
    after = apply_option_change(before, change)
    result = calculate_damage_gain(before, after, **conditions)
    result["option_change"] = change.model_dump()
    result["limitations"].append("제거할 최종뎀·방무는 실제 총합에 포함된 독립 항임을 확인해야 합니다. 합연산 그룹의 내부 옵션은 그룹 전후 수치를 별도로 계산하세요.")
    return result


def calculate_skill_damage_gain(skills: list[SkillDamageComparison], **conditions) -> dict:
    if not skills or len(skills) > 50 or len({skill.skill_name for skill in skills}) != len(skills):
        raise ValueError("Provide 1-50 distinct skills")
    if not math.isclose(sum(skill.baseline_damage_share_percent for skill in skills), 100, abs_tol=1e-6, rel_tol=0):
        raise ValueError("Baseline damage shares must cover 100% of the comparison cycle")
    reference = conditions.pop("reference", None)
    results = [calculate_damage_gain(skill.before, skill.after, **conditions) for skill in skills]
    ratio = (sum(skill.baseline_damage_share_percent / 100 * result["damage_ratio"] for skill, result in zip(skills, results))
             if all(result["damage_ratio"] is not None for result in results) else None)
    gain = (ratio - 1) * 100 if ratio is not None else None
    cost = conditions.get("cost_meso")
    return {"source": "calculation_from_supplied_skill_cycle", "model_version": MODEL_VERSION,
            "character_name": conditions["character_name"], "data_as_of": conditions["data_as_of"], "realtime": False,
            "status": "ok" if ratio is not None else "undefined_baseline", "damage_ratio": ratio,
            "equivalent_final_damage_gain_percent": gain,
            "cost_meso": cost, "gain_percent_per_100m_meso": gain * 100_000_000 / cost if gain is not None and cost else None,
            "is_free": cost == 0, "reference_check": compare_reference(gain, reference),
            "skill_results": [{"skill_name": skill.skill_name, "baseline_damage_share_percent": skill.baseline_damage_share_percent, "calculation": result} for skill, result in zip(skills, results)],
            "source_urls": conditions["source_urls"], "rules_as_of": conditions["rules_as_of"], "assumptions": conditions["assumptions"],
            "limitations": ["기존 동일 기간/조건의 데미지 점유율로 가중합니다. 사용 횟수 변화는 확인한 combat_multiplier에 포함해야 합니다.",
                            "사이트의 직업별 표준 사이클을 자동 복제하지 않습니다. 누락 스킬/임의 점유율은 검증된 결과가 아닙니다."]}
