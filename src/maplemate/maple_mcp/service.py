from difflib import SequenceMatcher
from typing import Any
from ..config import Config
from ..runtime import RuntimeClient
from ..secrets import SecretError, key_configured, load_key
from ..state import StateStore, age_seconds
from .nexon import NexonClient, NexonError
from .catalog import endpoint_spec, validate_parameters
from .damage_inputs import assemble_damage_inputs
from .models import DamageAssemblyInputs


class MapleService:
    def __init__(self, config: Config):
        self.config = config
        self.store = StateStore(config.data_dir)
        self.runtime = RuntimeClient(config)

    def status(self) -> dict[str, Any]:
        return {"product": "MapleMate", "api_key_configured": key_configured(self.config.data_dir),
                "registered_character_count": len(self.store.get("registered_characters", [])), "tray": self.runtime.status(),
                "last_observed_character": self.store.snapshot()["last_observed_character"]}

    def find_character(self, name_query: str) -> dict:
        query = name_query.strip()
        if not query or len(query) > 30:
            raise ValueError("Provide a nickname fragment of 1-30 characters.")
        entries = self.store.get("registered_characters", [])
        ranked = sorted(((SequenceMatcher(None, query, entry["character_name"]).ratio(), entry) for entry in entries), key=lambda pair: pair[0], reverse=True)
        candidates = [{"character_name": entry["character_name"], "world_name": entry.get("world_name", ""), "name_similarity": round(score, 3), "exact_match": query == entry["character_name"]} for score, entry in ranked[:5] if score >= 0.3]
        return {"source": "nexon_api_registered_characters", "candidates": candidates, "is_current_character_verified": False}

    def information(self, section: str, character_name: str | None = None, date: str | None = None,
                    parameters: dict[str, str | int] | None = None) -> dict[str, Any]:
        query = dict(parameters or {})
        if date is not None:
            if "date" in query:
                return {"status": "invalid_arguments", "message": "Provide date only once"}
            query["date"] = date
        endpoint = "character/" + section
        try:
            if not any(p["name"] == "ocid" for p in endpoint_spec(endpoint)["parameters"]):
                raise ValueError("Use get_nexon_api for account-level endpoints")
        except ValueError as error:
            return {"status": "invalid_arguments", "message": str(error)}
        return self.api(endpoint, query, character_name)

    def api(self, endpoint: str, parameters: dict[str, str | int] | None = None,
            character_name: str | None = None) -> dict[str, Any]:
        query = dict(parameters or {})
        try:
            spec = endpoint_spec(endpoint)
            uses_ocid = any(p["name"] == "ocid" for p in spec["parameters"])
            requires_ocid = any(p["name"] == "ocid" and p["required"] for p in spec["parameters"])
            if character_name and (not uses_ocid or "ocid" in query):
                raise ValueError("character_name requires an ocid endpoint without an explicit ocid")
            validate_parameters(endpoint, query, allow_missing_ocid=requires_ocid or bool(character_name))
        except ValueError as error:
            return {"status": "invalid_arguments", "message": str(error)}
        observed = self.store.snapshot()["last_observed_character"]
        age = age_seconds(observed.get("observed_at")) if observed else None
        name = character_name
        if requires_ocid and "ocid" not in query and not name:
            name = observed["name"] if age is not None and age <= 300 else None
        if requires_ocid and "ocid" not in query and not name:
            return {"status": "missing_character", "message": "capture_game_screen으로 닉네임을 읽고 저장하거나 캐릭터 이름을 명시해 주세요."}
        client = None
        try:
            client = NexonClient(load_key(self.config.data_dir))
            result = {"status": "ok", **client.query(endpoint, query, name)}
            if name:
                result.update(character_name=name, name_selection="explicit" if character_name else "last_screen_observation")
            # An explicit ocid can only be attributed when it matches a locally
            # registered character; never attach an unrelated response by guess.
            state_name = name
            if not state_name and query.get("ocid"):
                state_name = next((entry["character_name"] for entry in self.store.get("registered_characters", [])
                                   if entry.get("ocid") == query["ocid"]), None)
            result["state_record"] = (self.store.record_api_observation(state_name, query, result) if state_name
                                      else {"saved": False, "reason": "no_character_attribution"})
            return result
        except ValueError as error:
            return {"status": "invalid_arguments", "message": str(error)}
        except (SecretError, NexonError) as error:
            return {"status": "unavailable", "code": getattr(error, "code", "configuration_error"),
                    "message": "API 키·권한·호출 한도·조회 조건을 확인하세요. 계정 전용 API는 키 소유 계정의 정보만 조회할 수 있습니다."}
        finally:
            if client:
                client.close()

    def upgrade_context(self, target_starforce: int = 22) -> dict[str, Any]:
        if not 0 <= target_starforce <= 30:
            raise ValueError("Target starforce must be between 0 and 30.")
        state = self.store.snapshot()
        observation = state["screen_observation"]
        age = age_seconds(observation.get("observed_at")) if observation else None
        recent = age is not None and age <= 900
        candidates = [tooltip for tooltip in state["tooltips"] if tooltip["kind"] == "candidate"] if recent else []
        selected = candidates[0] if len(candidates) == 1 else None
        spending_budget = state["context_facts"].get("budget_meso")
        if spending_budget and spending_budget["value"] is None:
            spending_budget = None
        missing = []
        if not observation or not observation.get("character_name") or not recent:
            missing.append("recent_character_observation")
        if not state["inventory_meso"] and spending_budget is None:
            missing.append("inventory_meso")
        if selected is None:
            missing.append("unambiguous_hovered_item")
        else:
            missing.extend(field for field in ("item_name", "required_level", "starforce") if selected.get(field) is None or field in selected.get("uncertain_fields", []))
        return {"status": "ready_for_client_review" if not missing else "incomplete", "target_starforce": target_starforce,
                "player_state": state, "spending_budget": spending_budget, "selected_item": selected, "missing_fields": missing,
                "requires_latest_research": ["현재 KMS 스타포스 확률과 비용", "파괴 시 복구 방식과 대체 장비 비용", "현재 이벤트 할인 및 조건", "대안 장비 시세와 사용자 목표"],
                "next_tools": ["capture_game_screen", "record_screen_observation", "get_character_information", "list_nexon_apis", "get_nexon_api"], "expected_cost": None, "recommendation": None}

    def damage_context(self, character_name: str, inputs: DamageAssemblyInputs, date: str | None = None,
                       refresh: bool = True, additional_sections: list[str] | None = None) -> dict[str, Any]:
        extras = additional_sections or []
        allowed = {"item-equipment", "symbol-equipment", "hyper-stat", "ability", "link-skill", "set-effect",
                   "vmatrix", "hexamatrix", "hexamatrix-stat", "user/union", "user/union-raider", "user/union-artifact"}
        if len(extras) > 12 or any(section not in allowed for section in extras):
            raise ValueError("Select documented growth sections for damage assembly")
        endpoints = ["character/basic", "character/stat", *dict.fromkeys(section if section.startswith("user/") else "character/" + section for section in extras)]
        observations, errors = {}, {}
        if refresh:
            for endpoint in endpoints:
                result = self.api(endpoint, {"date": date} if date else {}, character_name)
                if result.get("status") == "ok":
                    observations[endpoint] = result
                else:
                    errors[endpoint] = {key: result.get(key) for key in ("status", "code", "message")}
        else:
            for entry in self.store.snapshot(character_name, include_api_data=True)["api_observations"]:
                endpoint = entry["endpoint"]
                if endpoint in endpoints and endpoint not in observations and entry.get("parameters", {}).get("date") == date:
                    observations[endpoint] = entry
            errors = {endpoint: {"status": "not_in_saved_state"} for endpoint in endpoints if endpoint not in observations}
        assembled = assemble_damage_inputs(character_name, observations, inputs)
        assembled.update(retrieval_errors=errors, requested_date=date, refreshed=refresh,
                         next_tool="calculate_final_damage_gain", state_revision=self.store.snapshot(character_name)["revision"])
        return assembled
