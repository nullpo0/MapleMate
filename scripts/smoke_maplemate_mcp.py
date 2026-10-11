"""Real stdio SDK + named pipe with synthetic pixels and no account key."""
import asyncio
import argparse
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid
from PIL import Image
import numpy as np
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from maplemate.capture import CapturedFrame
from maplemate.config import Config
from maplemate.runtime import CaptureManager, RuntimeServer
from maplemate.state import StateStore, utc_now
from maplemate.maple_mcp.media import MAX_RESPONSE_BYTES

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / ".cache" / "maplemate-mcp-smoke"
EXPECTED = {"get_maple_status", "get_player_state", "get_maple_advice_guide", "capture_game_screen", "record_screen_observation", "find_character", "get_upgrade_context", "get_character_information", "list_nexon_apis", "get_nexon_api", "calculate_upgrade_expectation", "calculate_final_damage_gain", "update_player_context", "record_growth_assessment"}
EXPECTED |= {"get_damage_calculation_context", "calculate_final_damage_option_gain", "calculate_skill_damage_comparison"}
NAME = "\ucd08\ubcf4\uc6a9\uc0ac"


async def main(bundle=None):
    directory = TEST_ROOT / uuid.uuid4().hex
    config = Config.load(directory)
    store = StateStore(directory)
    store.set("registered_characters", [{"character_name": NAME, "ocid": "synthetic"}])
    values = {"STR": "10000", "DEX": "1000", "공격력": "2000", "데미지": "100", "보스 몬스터 데미지": "300",
              "일반 몬스터 데미지": "20", "크리티컬 확률": "100", "크리티컬 데미지": "50", "최종 데미지": "20",
              "방어율 무시": "90", "속성 내성 무시": "5"}
    for endpoint, data in (("character/basic", {"character_name": NAME, "character_class": "아델"}),
                           ("character/stat", {"character_class": "아델", "final_stat": [{"stat_name": key, "stat_value": value} for key, value in values.items()]})):
        store.record_api_observation(NAME, {}, {"source": "nexon_open_api", "endpoint": endpoint,
            "data": data, "data_date": "2026-10-10", "fetched_at": utc_now()})
    pixels = io.BytesIO()
    Image.new("RGB", (640, 480), "green").save(pixels, format="PNG")
    calls = []
    def capture():
        calls.append(True)
        return CapturedFrame(pixels.getvalue(), utc_now())
    manager = CaptureManager(store, capture)
    server = RuntimeServer(config, manager, threading.Event().set)
    try:
        command = str(Path(bundle).resolve() / "MapleMate-mcp.exe") if bundle else sys.executable
        prefix = [] if bundle else ["-m", "maplemate"]
        args = StdioServerParameters(command=command, args=[*prefix, "--data-dir", str(directory), "serve"], env={**os.environ, "NEXON_API_KEY": ""})
        async with stdio_client(args) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert {tool.name for tool in (await session.list_tools()).tools} == EXPECTED
                status = await session.call_tool("get_maple_status", {})
                assert status.structuredContent["tray"]["status"] == "running"
                assert calls == []
                damage_context = await session.call_tool("get_damage_calculation_context", {"character_name": NAME, "refresh": False,
                    "inputs": {"stat_weights": {"STR": 4, "DEX": 1}, "attack_stat": "공격력", "weapon_constant": 1.3,
                        "conditions": "Synthetic same preset and buffs", "stat_percent_bonuses": {"STR": 100, "DEX": 0},
                        "stat_flat_after_percent": {"STR": 3000, "DEX": 0}, "attack_percent_bonus": 100, "attack_flat_after_percent": 100}})
                assert not damage_context.isError, damage_context
                assert damage_context.structuredContent["profile"]["stat_factor"] == 41000
                assert damage_context.structuredContent["detailed_profile"]["attack"]["percent_affected"] == 950
                assert calls == []
                catalog = await session.call_tool("list_nexon_apis", {})
                assert catalog.structuredContent["total_endpoints"] == 55
                assert len(catalog.structuredContent["endpoints"]) == 55
                skill_api = await session.call_tool("list_nexon_apis", {"endpoint": "character/skill"})
                assert any(p["name"] == "character_skill_grade" and p["required"] for p in skill_api.structuredContent["parameters"])
                rejected_api = await session.call_tool("get_nexon_api", {"endpoint": "https://example.com"})
                assert rejected_api.structuredContent["status"] == "invalid_arguments"
                missing_union = await session.call_tool("get_nexon_api", {"endpoint": "user/union"})
                assert missing_union.structuredContent["status"] == "missing_character"
                no_key = await session.call_tool("get_nexon_api", {"endpoint": "notice-event"})
                assert no_key.structuredContent["status"] == "unavailable"
                guide = await session.call_tool("get_maple_advice_guide", {"topic": "upgrade"})
                assert "capture_game_screen" in guide.structuredContent["instructions"]
                assert "upgrade_reference" in guide.structuredContent
                assert "damage_reference" in guide.structuredContent
                overview = await session.call_tool("get_maple_advice_guide", {"topic": "overview"})
                assert not overview.isError and overview.structuredContent["overview_reference"]
                for topic in ("knowledge", "equipment", "progression", "beginner", "intermediate", "advanced", "playstyle", "community", "state"):
                    stage_guide = await session.call_tool("get_maple_advice_guide", {"topic": topic})
                    assert not stage_guide.isError
                    assert stage_guide.structuredContent["topic"] == topic
                    assert stage_guide.structuredContent[topic + "_reference"]
                    if topic == "equipment":
                        assert stage_guide.structuredContent["damage_reference"]
                    assert not any(other + "_reference" in stage_guide.structuredContent for other in ("beginner", "intermediate", "advanced") if other != topic)
                bad_topic = await session.call_tool("get_maple_advice_guide", {"topic": "unknown-topic"})
                assert bad_topic.isError
                missing = await session.call_tool("get_character_information", {"section": "basic"})
                assert missing.structuredContent["status"] == "missing_character"
                screen = await session.call_tool("capture_game_screen", {})
                assert not screen.isError
                images = [item for item in screen.content if item.type == "image"]
                assert len(images) == 1 and base64.b64decode(images[0].data) == pixels.getvalue()
                record = await session.call_tool("record_screen_observation", {"observation": {"screenshot_id": screen.structuredContent["screenshot_id"], "character_name": NAME, "inventory_meso": 12345, "tooltips": [{"kind": "candidate", "item_name": "Synthetic Equipment", "required_level": 200, "starforce": 17}, {"kind": "equipped_comparison", "item_name": "Equipped Equipment", "required_level": 200, "starforce": 22}]}})
                assert not record.isError
                player = await session.call_tool("get_player_state", {})
                assert player.structuredContent["inventory_meso"]["value"] == 12345
                assert player.structuredContent["inventory_meso"]["source"] == "llm_vision"
                assert not player.structuredContent["inventory_meso"]["is_current_balance"]
                context = await session.call_tool("get_upgrade_context", {})
                assert context.structuredContent["selected_item"]["item_name"] == "Synthetic Equipment"
                assert context.structuredContent["expected_cost"] is None
                assert context.structuredContent["status"] == "ready_for_client_review"
                names = await session.call_tool("find_character", {"name_query": NAME})
                assert names.structuredContent["candidates"][0]["exact_match"]
                goal = await session.call_tool("update_player_context", {"character_name": NAME, "goal": "synthetic goal"})
                assert goal.structuredContent["source"] == "user_input"
                profile = await session.call_tool("update_player_context", {"character_name": NAME,
                    "patch": {"budget_meso": 100000000, "daily_play_minutes": 30,
                              "boss_experience": [{"boss_name": "synthetic boss", "difficulty": "synthetic difficulty", "party_size": 1, "status": "practicing"}]},
                    "expected_revision": goal.structuredContent["revision"]})
                assert not profile.isError
                state = (await session.call_tool("get_player_state", {"character_name": NAME})).structuredContent
                assert state["state_schema_version"] == 2 and state["user_context"]["daily_play_minutes"] == 30
                assert state["context_facts"]["budget_meso"]["source"] == "user_input"
                assert state["inventory_meso"]["value"] != state["user_context"]["budget_meso"]
                assessment = {"stage": "unknown", "system_understanding": "unknown", "goal": "synthetic goal",
                              "rationale": ["Synthetic test evidence"], "confidence": "low", "uncertainties": ["Not actual game advice"]}
                assessed = await session.call_tool("record_growth_assessment", {"character_name": NAME,
                    "assessment": assessment, "based_on_revision": state["revision"]})
                assert not assessed.isError and assessed.structuredContent["growth_assessment"]["source"] == "llm_assessment"
                conflict = await session.call_tool("update_player_context", {"character_name": NAME, "goal": "stale goal", "expected_revision": 0})
                assert conflict.isError
                cleared = await session.call_tool("update_player_context", {"character_name": NAME,
                    "patch": {"budget_meso": None}, "expected_revision": state["revision"]})
                assert not cleared.isError and cleared.structuredContent["context"]["budget_meso"] is None
                reread = (await session.call_tool("get_player_state", {"character_name": NAME, "include_api_data": True})).structuredContent
                assert reread["user_context"]["goal"] == "synthetic goal"
                assert reread["growth_assessment"]["needs_review"]
                stale_assessment = await session.call_tool("record_growth_assessment", {"character_name": NAME,
                    "assessment": assessment, "based_on_revision": state["revision"]})
                assert stale_assessment.isError
                calculation = await session.call_tool("calculate_upgrade_expectation", {"states": [{"id": "start", "attempt_cost": 10, "transitions": [{"next_state": "target", "probability": 0.5}, {"next_state": "start", "probability": 0.5}]}], "start_state": "start", "target_state": "target", "source_urls": ["https://maplestory.nexon.com/News/Update"], "rules_as_of": "2026-10-10", "assumptions": ["Synthetic test rules, not game rules"]})
                assert calculation.structuredContent["expected_meso"] == 20
                before = {"stat_factor": 1, "attack_power": 1, "damage_percent": 0, "boss_damage_percent": 0,
                          "normal_monster_damage_percent": 0, "critical_rate_percent": 100,
                          "critical_damage_percent": 0, "final_damage_sources": [20], "ignore_defense_sources": [90]}
                after = {**before, "final_damage_sources": [20, 10]}
                damage = await session.call_tool("calculate_final_damage_gain", {
                    "before": before, "after": after, "target_defense_percent": 300, "is_boss": True,
                    "character_name": NAME, "data_as_of": "2026-10-10", "rules_as_of": "2026-10-10",
                    "source_urls": ["https://maplestory.nexon.com/Guide/N23GameInformation/Articles/390"],
                    "assumptions": ["Synthetic normalized test inputs; not character stats"], "cost_meso": 100000000})
                assert not damage.isError
                assert abs(damage.structuredContent["equivalent_final_damage_gain_percent"] - 10) < 1e-8
                conditions = {"target_defense_percent": 300, "is_boss": True, "character_name": NAME,
                    "data_as_of": "2026-10-10", "rules_as_of": "2026-10-11",
                    "source_urls": ["https://maplestory.nexon.com/Guide/N23GameInformation/Articles/390"],
                    "assumptions": ["Synthetic test conditions, not actual character damage"]}
                option = await session.call_tool("calculate_final_damage_option_gain", {**conditions,
                    "before": before, "change": {"add_final_damage_sources": [10]}})
                assert not option.isError, option
                assert abs(option.structuredContent["equivalent_final_damage_gain_percent"] - 10) < 1e-8
                raw = {key: value for key, value in before.items() if key not in ("stat_factor", "attack_power")}
                raw.update(weapon_constant=1, elemental_ignore_percent=0,
                    stat_terms=[{"name": "STR", "coefficient": 4, "value": {"percent_affected": 100, "percent_bonus": 0, "flat_after_percent": 0}}],
                    attack={"percent_affected": 100, "percent_bonus": 30, "flat_after_percent": 0})
                raw_option = await session.call_tool("calculate_final_damage_option_gain", {**conditions,
                    "before": raw, "change": {"attack_percent_delta": 9},
                    "reference": {"label": "User supplied FMK example", "source_url": "https://www.fmkorea.com/8336709224",
                        "gain_percent": 6.9, "conditions": "Attack percentage 30 to 39; no exempt attack"}})
                assert not raw_option.isError, raw_option
                assert abs(raw_option.structuredContent["equivalent_final_damage_gain_percent"] - 9 / 130 * 100) < 1e-8
                assert raw_option.structuredContent["reference_check"]["status"] == "within_tolerance"
                insufficient = await session.call_tool("calculate_final_damage_option_gain", {**conditions,
                    "before": before, "change": {"attack_percent_delta": 9}})
                assert insufficient.isError
                skills = []
                for index, (ied, share) in enumerate(((80, 60), (95, 40))):
                    skill_before = {**before, "ignore_defense_sources": [ied]}
                    skills.append({"skill_name": str(index), "baseline_damage_share_percent": share,
                        "before": skill_before, "after": {**skill_before, "ignore_defense_sources": [ied, 30]}})
                cycle = await session.call_tool("calculate_skill_damage_comparison", {**conditions, "skills": skills})
                assert not cycle.isError, cycle
                assert abs(cycle.structuredContent["equivalent_final_damage_gain_percent"] - (45 * .6 + (895 / 850 - 1) * 100 * .4)) < 1e-8
                invalid_damage = await session.call_tool("calculate_final_damage_gain", {
                    "before": {**before, "ignore_defense_sources": [101]}, "after": after,
                    "target_defense_percent": 300, "is_boss": True, "character_name": NAME,
                    "data_as_of": "2026-10-10", "rules_as_of": "2026-10-10",
                    "source_urls": ["https://maplestory.nexon.com/Guide/N23GameInformation/Articles/390"],
                    "assumptions": ["Synthetic invalid input"]})
                assert invalid_damage.isError
                invalid = await session.call_tool("record_screen_observation", {"observation": {"screenshot_id": uuid.uuid4().hex, "character_name": NAME, "inventory_meso": 1}})
                assert invalid.isError
                bad_delay = await session.call_tool("capture_game_screen", {"delay_seconds": 99})
                assert bad_delay.isError and len(calls) == 1
                removed = await session.call_tool("capture_game_screen", {"use_saved": True})
                assert removed.isError and len(calls) == 1
                fresh = await session.call_tool("capture_game_screen", {})
                assert fresh.structuredContent["delivery"] == "requested" and len(calls) == 2
                # Reproduce the host's oversized-image failure using synthetic 4K pixels.
                full_image = Image.fromarray(np.random.default_rng(19).integers(0, 256, (2160, 3840, 3), dtype=np.uint8))
                pixels = io.BytesIO()
                full_image.save(pixels, format="PNG")
                assert len(base64.b64encode(pixels.getvalue())) > 1_000_000
                large = await session.call_tool("capture_game_screen", {})
                assert not large.isError
                large_bytes = len(large.model_dump_json(by_alias=True).encode("utf-8"))
                assert large_bytes <= MAX_RESPONSE_BYTES
                assert large.structuredContent["image_mime_type"] == "image/jpeg"
                assert large.structuredContent["original_width"] == 3840
                region = {"x": 100, "y": 150, "width": 400, "height": 120}
                cropped = await session.call_tool("capture_game_screen", {"region": region})
                assert not cropped.isError
                assert cropped.structuredContent["capture_region"] == region
                assert not cropped.structuredContent["image_resized"]
                cropped_image = next(item for item in cropped.content if item.type == "image")
                with Image.open(io.BytesIO(base64.b64decode(cropped_image.data))) as decoded:
                    assert decoded.tobytes() == full_image.crop((100, 150, 500, 270)).tobytes()
                assert cropped.structuredContent["screenshot_id"] != large.structuredContent["screenshot_id"]
                outside = await session.call_tool("capture_game_screen", {"region": {**region, "x": 3800}})
                assert outside.isError and len(calls) == 5
                invalid_region = await session.call_tool("capture_game_screen", {"region": {**region, "width": 0}})
                assert invalid_region.isError and len(calls) == 5
                assert list(directory.rglob("*.png")) == []
                assert {p.name for p in (await session.list_prompts()).prompts} == {"maplemate", "maple_equipment_advice"}
                prompt = await session.get_prompt("maple_equipment_advice", {})
                assert "record_screen_observation" in prompt.messages[0].content.text
                general = await session.get_prompt("maplemate", {"question": "이번 이벤트의 보상을 알려줘."})
                assert general.messages[0].content.text.startswith("이번 이벤트의 보상을 알려줘.")
                for result in (status, guide, record, context, player, fresh):
                    value = json.dumps(result.structuredContent)
                    assert "authkey_dpapi" not in value and "nexon-key" not in value
        eof = subprocess.run([command, *prefix, "--data-dir", str(directory), "serve"],
                             input=b"", capture_output=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert eof.returncode == 0 and b"Traceback" not in eof.stderr, eof.stderr.decode(errors="replace")
        print(json.dumps({"stdio_tools": len(EXPECTED), "bundled_executable": bool(bundle), "clean_stdio_eof": True, "structured_context_round_trip": True, "growth_assessment_invalidation": True, "revision_conflicts_rejected": True, "final_damage_calculation": True, "damage_input_assembly": True, "option_change_calculation": True, "weighted_skill_comparison": True, "reference_difference": True, "image_response": True, "vision_observation_round_trip": True, "removed_pin_request_rejected": True, "large_4k_response_bytes": large_bytes, "region_original_pixels_preserved": True, "no_continuous_capture": True, "no_account_key_used": True}))
    finally:
        server.close()
        assert directory.resolve().parent == TEST_ROOT.resolve()
        shutil.rmtree(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle")
    asyncio.run(main(parser.parse_args().bundle))
