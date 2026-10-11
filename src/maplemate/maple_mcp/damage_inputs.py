"""Assemble dated API totals without adding their constituent bonuses twice."""
import math
import re
from datetime import datetime
from .damage import multipliers
from .models import DamageAssemblyInputs, DamageProfile, DetailedDamageProfile

STAT_NAMES = {
    "damage_percent": "데미지", "boss_damage_percent": "보스 몬스터 데미지",
    "normal_monster_damage_percent": "일반 몬스터 데미지", "critical_rate_percent": "크리티컬 확률",
    "critical_damage_percent": "크리티컬 데미지", "elemental_ignore_percent": "속성 내성 무시",
}


def number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?)%?", value.strip()):
        return None
    result = float(value.strip().removesuffix("%").replace(",", ""))
    return result if math.isfinite(result) else None


def assemble_damage_inputs(name: str, observations: dict[str, dict], inputs: DamageAssemblyInputs) -> dict:
    basic = observations.get("character/basic", {})
    stat = observations.get("character/stat", {})
    character_class = basic.get("data", {}).get("character_class") or stat.get("data", {}).get("character_class")
    totals, duplicate_names = {}, set()
    for entry in stat.get("data", {}).get("final_stat", []):
        label = entry.get("stat_name")
        if not isinstance(label, str):
            continue
        if label in totals:
            duplicate_names.add(label)
        totals[label] = number(entry.get("stat_value"))
    for label in duplicate_names:
        totals[label] = None
    supplied = inputs.model_dump(exclude_none=True)
    if inputs.data_as_of is not None:
        datetime.fromisoformat(inputs.data_as_of)
    data_as_of = inputs.data_as_of or stat.get("data_date") or stat.get("fetched_at")
    profile, provenance, missing = {}, {}, []

    def assign(field, value, origin):
        if field in supplied:
            profile[field], provenance[field] = supplied[field], {"source": "client_supplied", "conditions": inputs.conditions,
                "data_as_of": inputs.data_as_of}
        elif value is not None:
            profile[field], provenance[field] = value, {"source": "nexon_open_api", "endpoint": "character/stat", "stat_name": origin,
                "data_date": stat.get("data_date"), "fetched_at": stat.get("fetched_at")}
        else:
            missing.append(field)

    for field, label in STAT_NAMES.items():
        value = totals.get(label)
        if field == "critical_rate_percent" and value is not None:
            value = min(value, 100)
        assign(field, value, label)
    for field, label in (("final_damage_sources", "최종 데미지"), ("ignore_defense_sources", "방어율 무시")):
        value = totals.get(label)
        assign(field, [value] if value is not None else None, label)
    weights = inputs.stat_weights or {}
    if weights and (len(weights) > 8 or any(key not in {"STR", "DEX", "INT", "LUK", "HP"} or not math.isfinite(weight) or not 0 < weight <= 1e6 for key, weight in weights.items())):
        raise ValueError("Supply verified positive weights for STR/DEX/INT/LUK/HP")
    weighted = sum(totals[key] * weight for key, weight in weights.items()) if weights and all(totals.get(key) is not None for key in weights) else None
    # Demon Avenger's effective HP term is not a simple 4×HP formula.
    if character_class == "데몬어벤져" and inputs.stat_factor is None:
        weighted = None
    assign("stat_factor", weighted, list(weights))
    if weighted is not None and inputs.stat_factor is None:
        provenance["stat_factor"]["source"] = "api_totals_with_client_stat_weights"
    assign("attack_power", totals.get(inputs.attack_stat) if inputs.attack_stat else None, inputs.attack_stat)
    assign("weapon_constant", None, None)
    if inputs.conditions is None:
        missing.append("conditions")
    if data_as_of is None:
        missing.append("data_as_of")

    dates = {entry.get("data_date")[:10] for entry in observations.values() if isinstance(entry.get("data_date"), str)}
    classes = {entry["data"]["character_class"] for entry in observations.values() if entry.get("data", {}).get("character_class")}
    conflicts = []
    if len(dates) > 1:
        conflicts.append("API 기준일이 서로 다릅니다. 같은 날짜로 다시 조회하세요.")
    if inputs.data_as_of and dates and any(value != inputs.data_as_of[:10] for value in dates):
        conflicts.append("사용자 입력 시각과 API 기준일이 다릅니다. 전투 조건과 기준일을 맞춰 주세요.")
    if len(classes) > 1:
        conflicts.append("조회한 직업이 서로 다릅니다. 캐릭터·기준일을 확인하세요.")
    for entry in observations.values():
        api_name = entry.get("data", {}).get("character_name") or entry.get("character_name")
        if api_name and api_name != name:
            conflicts.append("다른 캐릭터의 API 응답은 합칠 수 없습니다.")
            break

    validated = None
    if not missing:
        validated = DamageProfile(**profile)
        # Validate multiplier source ranges before declaring these inputs usable.
        multipliers(validated, 0, True, 0)
    detailed, breakdown_missing = None, []
    if weights and validated and inputs.stat_factor is None:
        terms = []
        for key, weight in weights.items():
            percent = (inputs.stat_percent_bonuses or {}).get(key)
            flat = (inputs.stat_flat_after_percent or {}).get(key)
            if percent is None:
                breakdown_missing.append("stat_percent_bonuses." + key)
            if flat is None:
                breakdown_missing.append("stat_flat_after_percent." + key)
            if percent is not None and flat is not None:
                if not math.isfinite(percent) or not -100 < percent <= 1e6 or not math.isfinite(flat) or not 0 <= flat <= totals[key]:
                    raise ValueError("Invalid percentage or percent-exempt stat")
                terms.append({"name": key, "coefficient": weight, "value": {
                    "percent_affected": (totals[key] - flat) / (1 + percent / 100), "percent_bonus": percent, "flat_after_percent": flat}})
        percent, flat = inputs.attack_percent_bonus, inputs.attack_flat_after_percent
        if percent is None:
            breakdown_missing.append("attack_percent_bonus")
        if flat is None:
            breakdown_missing.append("attack_flat_after_percent")
        if not breakdown_missing:
            if flat > validated.attack_power:
                raise ValueError("Percent-exempt attack exceeds total attack")
            factors = validated.model_dump(exclude={"stat_factor", "attack_power"})
            detailed = DetailedDamageProfile(**factors, stat_terms=terms, attack={
                "percent_affected": (validated.attack_power - flat) / (1 + percent / 100),
                "percent_bonus": percent, "flat_after_percent": flat}).model_dump()
    else:
        breakdown_missing.append("verified_stat_breakdown_or_custom_formula")

    return {"character_name": name, "source": "assembly_from_api_and_client_inputs", "realtime": False,
            "status": "ready_for_total_stat_comparison" if validated and not conflicts else "needs_input",
            "character_class": character_class, "conditions": inputs.conditions,
            "data_as_of": data_as_of,
            "data_as_of_kind": "client_supplied" if inputs.data_as_of else "api_data_date" if stat.get("data_date") else "fetch_time_only" if stat.get("fetched_at") else "unknown",
            "profile": validated.model_dump() if validated and not conflicts else None,
            "partial_profile": profile, "field_provenance": provenance, "missing_fields": missing,
            "consistency_issues": conflicts, "api_total_stats": totals,
            "detailed_profile": detailed if not conflicts else None, "breakdown_missing_fields": breakdown_missing,
            "auxiliary_data": {endpoint: entry for endpoint, entry in observations.items() if endpoint != "character/stat"},
            "warnings": ["final_stat은 이미 합산된 총합입니다. 장비·링크·유니온·코어 등은 분해와 대조에만 사용하고 다시 더하지 않습니다.",
                         "총 스탯과 확인한 %·미적용 스탯에서 역산한 기본값은 API 총합에 맞춘 값이며 반올림 전의 실제 기본 스탯을 보장하지 않습니다.",
                         "심볼·장비 부분합은 전체 %미적용 항이나 전체 스탯%가 아닙니다. 직업 패시브·버프 등 미확인 항을 0으로 두지 마세요.",
                         "전투력·환산 수치를 attack_power/stat_factor로 대입하지 않습니다. 환산의 노도핑/스택/쓸만한 스킬 조건과 별도 대조하세요."]}
