"""Bounded, explicit observations supplied by the image-capable client LLM."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class CaptureRegion(BaseModel):
    """Pixels in the original game window, not the resized delivery image."""
    model_config = ConfigDict(extra="forbid", strict=True)
    x: int = Field(ge=0, le=8191)
    y: int = Field(ge=0, le=8191)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)


class TooltipObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["candidate", "equipped_comparison"] = "candidate"
    item_name: str | None = Field(default=None, max_length=100)
    required_level: int | None = Field(default=None, ge=0, le=500)
    starforce: int | None = Field(default=None, ge=0, le=30)
    category: str | None = Field(default=None, max_length=80)
    option_lines: list[str] = Field(default_factory=list, max_length=40)
    potential_lines: list[str] = Field(default_factory=list, max_length=10)
    additional_potential_lines: list[str] = Field(default_factory=list, max_length=10)
    uncertain_fields: list[Literal["item_name", "required_level", "starforce", "category", "options"]] = Field(default_factory=list, max_length=5)


class ScreenObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    screenshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    character_name: str | None = Field(default=None, min_length=1, max_length=30)
    inventory_meso: int | None = Field(default=None, ge=0, le=9_000_000_000_000_000_000)
    tooltips: list[TooltipObservation] = Field(default_factory=list, max_length=3)


class BossExperience(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    boss_name: str = Field(min_length=1, max_length=100)
    difficulty: str = Field(min_length=1, max_length=80)
    party_size: int = Field(ge=1, le=100)
    status: Literal["not_attempted", "practicing", "cleared", "stable"]
    clear_time_minutes: float | None = Field(default=None, gt=0, le=1440)
    notes: str | None = Field(default=None, max_length=2000)


class PlayerContextPatch(BaseModel):
    """Omitted fields stay unchanged; explicit null clears a user-provided fact."""
    model_config = ConfigDict(extra="forbid", strict=True)
    goal: str | None = Field(default=None, max_length=2000)
    play_style: str | None = Field(default=None, max_length=2000)
    target_content: str | None = Field(default=None, max_length=200)
    target_difficulty: str | None = Field(default=None, max_length=80)
    target_party_size: int | None = Field(default=None, ge=1, le=100)
    target_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    budget_meso: int | None = Field(default=None, ge=0, le=9_000_000_000_000_000_000,
                                    description="사용자가 이번 투자에 쓸 수 있다고 말한 메소. 관찰 잔액에서 추정하지 않음.")
    reserve_meso: int | None = Field(default=None, ge=0, le=9_000_000_000_000_000_000)
    spending_preference: Literal["no_paid_spending", "consider_paid_spending", "undecided"] | None = None
    daily_play_minutes: int | None = Field(default=None, ge=0, le=1440)
    weekly_play_minutes: int | None = Field(default=None, ge=0, le=10080)
    weekly_hunting_minutes: int | None = Field(default=None, ge=0, le=10080)
    weekly_boss_minutes: int | None = Field(default=None, ge=0, le=10080)
    risk_preference: str | None = Field(default=None, max_length=2000)
    system_familiarity: str | None = Field(default=None, max_length=2000)
    liberation_progress: str | None = Field(default=None, max_length=2000)
    boss_experience: list[BossExperience] | None = Field(default=None, max_length=30,
        description="같은 보스·난이도·인원 기록을 갱신하고 다른 기록은 유지. null은 전체 경험 기록 초기화.")


class GrowthAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stage: Literal["beginner", "intermediate", "advanced", "unknown"]
    system_understanding: Literal["learning", "mixed", "experienced", "unknown"]
    goal: str = Field(min_length=1, max_length=2000)
    rationale: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(min_length=1, max_length=12)
    bottlenecks: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(default_factory=list, max_length=12)
    uncertainties: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(default_factory=list, max_length=12)
    confidence: Literal["low", "medium", "high"]


class UpgradeTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    next_state: str = Field(min_length=1, max_length=64)
    probability: float = Field(ge=0, le=1)
    extra_cost: float = Field(default=0, ge=0)
    destruction: bool = False


class UpgradeState(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=64)
    attempt_cost: float = Field(ge=0)
    transitions: list[UpgradeTransition] = Field(min_length=1, max_length=20)


class DamageFactors(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    damage_percent: float = Field(ge=0, le=1e6)
    boss_damage_percent: float = Field(ge=0, le=1e6)
    normal_monster_damage_percent: float = Field(ge=0, le=1e6)
    critical_rate_percent: float = Field(ge=0, le=100)
    critical_damage_percent: float = Field(ge=0, le=10000, description="추가 크리티컬 데미지. 기본 20~50%는 제외.")
    final_damage_sources: list[float] = Field(max_length=32, description="독립적으로 곱해지는 최종뎀% 목록. 합산된 스탯창 값을 쓰면 [표시값] 한 항만 사용.")
    ignore_defense_sources: list[float] = Field(max_length=32, description="독립 방무% 목록. 스탯창 총 방무를 쓰면 [표시값] 한 항만 사용. 추가 방무는 별도 항.")
    weapon_constant: float = Field(default=1, gt=0, le=100,
        description="검증한 무기상수. 기본 1은 동일 무기상수가 상쇄되는 기존 정규화 비교용.")
    elemental_ignore_percent: float = Field(default=0, ge=0, le=100,
        description="같은 속성에 적용되는 유효 속성 내성 무시 총합%. 일반 합연산과 직업 예외를 확인한 값.")
    combat_multiplier: float = Field(default=1, gt=0, le=1e6,
        description="검증한 스킬 배율·포스/레벨/숙련도 등 추가 곱. 기본 1은 전후 조건 동일로 상쇄되는 비교용.")


class DamageProfile(DamageFactors):
    """Already-applied total values, preserving the original relative interface."""
    stat_factor: float = Field(gt=0, le=1e12, description="직업 공식의 유효 스탯 항. 일반 직업 4×주스탯+부스탯. 스탯% 적용 후 값이며 /100은 아직 적용하지 않음.")
    attack_power: float = Field(gt=0, le=1e12, description="공격력/마력%와 미적용 항을 반영한 총합. 스탯 공격력·전투력을 넣지 않음.")


class ScalableValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    percent_affected: float = Field(ge=0, le=1e12)
    percent_bonus: float = Field(gt=-100, le=1e6)
    flat_after_percent: float = Field(ge=0, le=1e12)
    rounding: Literal["none", "floor"] = "none"


class WeightedStat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=80)
    coefficient: float = Field(gt=0, le=1e6)
    value: ScalableValue


class DetailedDamageProfile(DamageFactors):
    """Percent-sensitive and percent-exempt inputs must be explicitly known."""
    stat_terms: list[WeightedStat] = Field(min_length=1, max_length=8)
    attack: ScalableValue
    weapon_constant: float = Field(gt=0, le=100)
    elemental_ignore_percent: float = Field(ge=0, le=100)


class DamageAssemblyInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    data_as_of: str | None = Field(default=None, min_length=10, max_length=40,
        description="사용자가 확인한 스탯 기준일 또는 ISO 관찰 시각. API 없이 입력할 때 필수.")
    stat_weights: dict[str, float] | None = None
    attack_stat: Literal["공격력", "마력"] | None = None
    weapon_constant: float | None = Field(default=None, gt=0, le=100)
    elemental_ignore_percent: float | None = Field(default=None, ge=0, le=100)
    stat_factor: float | None = Field(default=None, gt=0, le=1e12, description="특수 직업의 검증한 유효 스탯 항. 단순 HP×4 등으로 추정하지 않음.")
    attack_power: float | None = Field(default=None, gt=0, le=1e12)
    damage_percent: float | None = Field(default=None, ge=0, le=1e6)
    boss_damage_percent: float | None = Field(default=None, ge=0, le=1e6)
    normal_monster_damage_percent: float | None = Field(default=None, ge=0, le=1e6)
    critical_rate_percent: float | None = Field(default=None, ge=0, le=100)
    critical_damage_percent: float | None = Field(default=None, ge=0, le=10000)
    final_damage_sources: list[float] | None = Field(default=None, max_length=32)
    ignore_defense_sources: list[float] | None = Field(default=None, max_length=32)
    stat_percent_bonuses: dict[str, float] | None = None
    stat_flat_after_percent: dict[str, float] | None = None
    attack_percent_bonus: float | None = Field(default=None, gt=-100, le=1e6)
    attack_flat_after_percent: float | None = Field(default=None, ge=0, le=1e12)
    conditions: str | None = Field(default=None, min_length=1, max_length=2000,
        description="API·화면·사용자 입력의 같은 프리셋·버프·스킬·기준 시각 조건. 환산 기본 조건과 같다고 가정하지 않음.")


class DamageReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    label: str = Field(min_length=1, max_length=120)
    source_url: str = Field(min_length=1, max_length=1000)
    gain_percent: float = Field(gt=-100, le=1e9)
    conditions: str = Field(min_length=1, max_length=2000)
    max_gain_difference_pp: float = Field(default=0.05, ge=0, le=100)


class SkillDamageComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    skill_name: str = Field(min_length=1, max_length=120)
    baseline_damage_share_percent: float = Field(gt=0, le=100)
    before: DamageProfile | DetailedDamageProfile
    after: DamageProfile | DetailedDamageProfile


class DamageOptionChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    stat_percent_deltas: dict[str, float] = Field(default_factory=dict)
    stat_affected_deltas: dict[str, float] = Field(default_factory=dict)
    stat_flat_after_percent_deltas: dict[str, float] = Field(default_factory=dict)
    attack_percent_delta: float = 0
    attack_affected_delta: float = 0
    attack_flat_after_percent_delta: float = 0
    damage_percent_delta: float = 0
    boss_damage_percent_delta: float = 0
    normal_monster_damage_percent_delta: float = 0
    critical_rate_percent_delta: float = 0
    critical_damage_percent_delta: float = 0
    elemental_ignore_percent_delta: float = 0
    add_final_damage_sources: list[float] = Field(default_factory=list, max_length=32)
    remove_final_damage_sources: list[float] = Field(default_factory=list, max_length=32)
    add_ignore_defense_sources: list[float] = Field(default_factory=list, max_length=32)
    remove_ignore_defense_sources: list[float] = Field(default_factory=list, max_length=32)
    weapon_constant_after: float | None = Field(default=None, gt=0, le=100)
    additional_combat_multiplier: float = Field(default=1, gt=0, le=1e6)
