"""Snapshot facts are dated observations, never a continuously live balance."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def age_seconds(timestamp: str | None) -> float | None:
    try:
        date = datetime.fromisoformat(timestamp) if timestamp else None
        return max(0.0, (datetime.now(timezone.utc) - date).total_seconds()) if date and date.tzinfo else None
    except (ValueError, TypeError):
        return None


class StateStore:
    def __init__(self, directory: Path):
        self.path = directory / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meso (character TEXT PRIMARY KEY, value INTEGER NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS captures (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS player_fields (
                    character TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,
                    source TEXT NOT NULL, updated_at TEXT,
                    PRIMARY KEY (character, field));
                CREATE TABLE IF NOT EXISTS player_revisions (character TEXT PRIMARY KEY, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS api_observations (
                    character TEXT NOT NULL, variant TEXT NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY (character, variant));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def put(db: sqlite3.Connection, key: str, value: Any) -> None:
        db.execute("INSERT INTO records VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def set(self, key: str, value: Any) -> None:
        with self.connect() as db:
            self.put(db, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM records WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def character_names(self) -> list[str]:
        with self.connect() as db:
            rows = dict(db.execute("SELECT key,value FROM records"))
            names = {row[0] for table in ("meso", "player_fields", "player_revisions", "api_observations")
                     for row in db.execute("SELECT DISTINCT character FROM " + table)}
        names.update(entry.get("character_name") for entry in json.loads(rows.get("registered_characters", "[]")))
        names.update(key.split(":", 1)[1] for key in rows if key.startswith(("context:", "assessment:", "screen:", "reset:")))
        for key in ("active_character", "last_browser_character", "screen_observation"):
            value = json.loads(rows.get(key, "null"))
            names.add(value if isinstance(value, str) else (value.get("name") or value.get("character_name")) if isinstance(value, dict) else None)
        return sorted(name for name in names if isinstance(name, str) and name)

    @staticmethod
    def before_reset(value: str | None, reset_at: str | None) -> bool:
        if not reset_at:
            return False
        try:
            return datetime.fromisoformat(value) <= datetime.fromisoformat(reset_at)
        except (ValueError, TypeError):
            return True

    def reset_player_state(self, name: str, scope: str, expected_revision: int) -> dict[str, Any]:
        """User-confirmed reset of one character; never delete keys or identity lists."""
        if not isinstance(name, str) or not 1 <= len(name) <= 30 or any(ord(c) < 32 for c in name):
            raise ValueError("Invalid character name")
        if scope not in ("context", "all") or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Invalid reset scope or revision")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.revision(db, name) != expected_revision:
                raise ValueError("State changed. Review the current state before confirming reset again.")
            now = utc_now()
            rows = dict(db.execute("SELECT key,value FROM records"))
            previous = json.loads(rows.get("reset:" + name, "{}"))
            keys = {"context:" + name, "assessment:" + name}
            db.execute("DELETE FROM player_fields WHERE character=?", (name,))
            if scope == "all":
                keys.add("screen:" + name)
                for key in ("screen_observation", "active_character", "last_browser_character"):
                    value = json.loads(rows.get(key, "null"))
                    recorded_name = value if isinstance(value, str) else (value.get("character_name") or value.get("name")) if isinstance(value, dict) else None
                    if recorded_name == name:
                        keys.add(key)
                        if key in ("active_character", "last_browser_character"):
                            keys.add("observer")
                for key in keys:
                    value = json.loads(rows.get(key, "null"))
                    if isinstance(value, dict) and value.get("screenshot_id"):
                        db.execute("DELETE FROM captures WHERE id=?", (value["screenshot_id"],))
                db.execute("DELETE FROM meso WHERE character=?", (name,))
                db.execute("DELETE FROM api_observations WHERE character=?", (name,))
            db.executemany("DELETE FROM records WHERE key=?", ((key,) for key in keys))
            reset = {"reset_at": now, "scope": scope, "all_reset_at": now if scope == "all" else previous.get("all_reset_at"),
                     "source": "user_settings_reset"}
            self.put(db, "reset:" + name, reset)
            # Revisions stay monotonic so old conversations cannot reuse revision 0.
            revision = self.advance_revision(db, name)
        return {"status": "reset", "character_name": name, "scope": scope, "revision": revision, "reset_at": now}

    @staticmethod
    def revision(db: sqlite3.Connection, name: str) -> int:
        row = db.execute("SELECT revision FROM player_revisions WHERE character=?", (name,)).fetchone()
        return row[0] if row else 0

    @staticmethod
    def advance_revision(db: sqlite3.Connection, name: str) -> int:
        db.execute("INSERT INTO player_revisions VALUES (?,1) ON CONFLICT(character) DO UPDATE SET revision=revision+1", (name,))
        return StateStore.revision(db, name)

    @staticmethod
    def context_facts(db: sqlite3.Connection, name: str) -> dict[str, Any]:
        row = db.execute("SELECT value FROM records WHERE key=?", ("context:" + name,)).fetchone()
        legacy = json.loads(row[0]) if row else {}
        # Legacy text remains intact; its source/time cannot be retroactively certified.
        facts = {key: {"value": value, "source": "legacy_context", "updated_at": None} for key, value in legacy.items()}
        for field, value, source, updated_at in db.execute(
                "SELECT field,value,source,updated_at FROM player_fields WHERE character=?", (name,)):
            facts[field] = {"value": json.loads(value), "source": source, "updated_at": updated_at}
        return facts

    def update_context(self, name: str, patch: dict[str, Any], expected_revision: int | None = None) -> dict[str, Any]:
        if not patch:
            raise ValueError("Provide at least one context field")
        if patch.get("target_date") is not None:
            date.fromisoformat(patch["target_date"])
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = self.revision(db, name)
            if expected_revision is None and db.execute("SELECT 1 FROM records WHERE key=?", ("reset:" + name,)).fetchone():
                raise ValueError("State was reset. Read get_player_state and supply its revision before saving new context.")
            if expected_revision is not None and expected_revision != revision:
                raise ValueError("State changed. Read get_player_state and retry with its revision.")
            facts = self.context_facts(db, name)
            for field, value in patch.items():
                if field == "boss_experience" and value is not None:
                    existing = (facts.get(field) or {}).get("value") or []
                    # Upsert a user's boss report without discarding other bosses or
                    # previously supplied optional values. Each report retains its time.
                    entries = {(item["boss_name"], item["difficulty"], item["party_size"]): item for item in existing}
                    for item in value:
                        key = (item["boss_name"], item["difficulty"], item["party_size"])
                        entries[key] = {**entries.get(key, {}), **item, "source": "user_input", "updated_at": now}
                    value = list(entries.values())
                    if len(value) > 100:
                        raise ValueError("Too many boss records; clear obsolete records before adding more")
                fact = {"value": value, "source": "user_input", "updated_at": now}
                db.execute("INSERT INTO player_fields VALUES (?,?,?,?,?) ON CONFLICT(character,field) DO UPDATE SET value=excluded.value,source=excluded.source,updated_at=excluded.updated_at",
                           (name, field, json.dumps(value, ensure_ascii=False), "user_input", now))
                facts[field] = fact
            # Keep the old public context shape and older app's reads compatible.
            context = {field: fact["value"] for field, fact in facts.items()}
            self.put(db, "context:" + name, context)
            revision = self.advance_revision(db, name)
        return {"status": "saved", "source": "user_input", "character_name": name,
                "context": context, "context_facts": facts, "revision": revision}

    def record_assessment(self, name: str, assessment: dict[str, Any], based_on_revision: int) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if based_on_revision != self.revision(db, name):
                raise ValueError("State changed. Reassess from get_player_state before saving.")
            value = {**assessment, "source": "llm_assessment", "assessed_at": utc_now(),
                     "based_on_revision": based_on_revision, "is_user_fact": False}
            self.put(db, "assessment:" + name, value)
        return {"status": "saved", "character_name": name, "growth_assessment": value}

    def record_api_observation(self, name: str, parameters: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        # Cache only successfully requested character/union growth data, never
        # account history, headers, authentication, or unrelated public endpoints.
        endpoint = result["endpoint"]
        if not (endpoint.startswith("character/") or endpoint.startswith("user/union")) or endpoint == "character/list":
            return {"saved": False, "reason": "outside_growth_state"}
        query = {key: value for key, value in parameters.items() if key != "ocid"}
        variant = json.dumps([endpoint, query], sort_keys=True, ensure_ascii=False)
        value = {key: result.get(key) for key in ("source", "attribution", "endpoint", "documentation_url", "fetched_at", "requested_date", "data_date", "data")}
        value.update(parameters=query, is_live=False)
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 512_000:
            return {"saved": False, "reason": "response_too_large"}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reset_row = db.execute("SELECT value FROM records WHERE key=?", ("reset:" + name,)).fetchone()
            reset_at = json.loads(reset_row[0]).get("all_reset_at") if reset_row else None
            if self.before_reset(value.get("fetched_at"), reset_at):
                return {"saved": False, "reason": "before_state_reset"}
            row = db.execute("SELECT value FROM api_observations WHERE character=? AND variant=?", (name, variant)).fetchone()
            previous = json.loads(row[0]) if row else None
            if previous:
                old_date, new_date = previous.get("data_date"), value.get("data_date")
                if old_date and (not new_date or new_date < old_date):
                    return {"saved": False, "reason": "older_or_unknown_data_date"}
                if previous["fetched_at"] > value["fetched_at"]:
                    return {"saved": False, "reason": "older_fetch"}
            changed = not previous or any(previous.get(key) != value.get(key) for key in ("data", "data_date", "requested_date"))
            db.execute("INSERT OR REPLACE INTO api_observations VALUES (?,?,?)", (name, variant, encoded))
            # Bound variants (historical dates, skill grades, presets) per character.
            db.execute("DELETE FROM api_observations WHERE character=? AND rowid NOT IN (SELECT rowid FROM api_observations WHERE character=? ORDER BY rowid DESC LIMIT 32)", (name, name))
            revision = self.advance_revision(db, name) if changed else self.revision(db, name)
        return {"saved": True, "revision": revision}

    def register_capture(self, metadata: dict[str, Any]) -> None:
        # Pixels stay in the tray's memory; only capture provenance reaches SQLite.
        with self.connect() as db:
            db.execute("INSERT INTO captures VALUES (?,?)", (metadata["screenshot_id"], json.dumps(metadata)))
            db.execute("DELETE FROM captures WHERE id NOT IN (SELECT id FROM captures ORDER BY rowid DESC LIMIT 32)")

    def record_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM captures WHERE id=?", (observation["screenshot_id"],)).fetchone()
            capture = json.loads(row[0]) if row else None
            age = age_seconds(capture.get("captured_at")) if capture else None
            if age is None or age > 900:
                raise ValueError("Capture is missing or older than 15 minutes. Capture the game again.")
            previous = db.execute("SELECT value FROM records WHERE key='screen_observation'").fetchone()
            if previous and json.loads(previous[0])["observed_at"] > capture["captured_at"]:
                raise ValueError("A newer observation is already saved. An older screenshot cannot replace it.")
            entries_row = db.execute("SELECT value FROM records WHERE key='registered_characters'").fetchone()
            entries = json.loads(entries_row[0]) if entries_row else []
            name = observation.get("character_name")
            reset_row = db.execute("SELECT value FROM records WHERE key=?", ("reset:" + str(name),)).fetchone()
            reset_at = json.loads(reset_row[0]).get("all_reset_at") if reset_row else None
            if self.before_reset(capture.get("captured_at"), reset_at):
                raise ValueError("This screenshot predates the state reset. Capture the game again.")
            if name and name not in {entry.get("character_name") for entry in entries}:
                raise ValueError("Nickname does not match the API-registered characters. Use find_character or check the name.")
            if observation.get("inventory_meso") is not None and not name:
                raise ValueError("Read the character nickname before attributing inventory mesos.")
            observed = {**observation, "source": "llm_vision", "observed_at": capture["captured_at"], "recorded_at": utc_now(), "is_live": False, "verified_by_server": False}
            self.put(db, "screen_observation", observed)
            if name:
                self.put(db, "screen:" + name, observed)
                self.advance_revision(db, name)
            if name and observation.get("inventory_meso") is not None:
                db.execute("INSERT INTO meso VALUES (?,?,?,?) ON CONFLICT(character) DO UPDATE SET value=excluded.value, observed_at=excluded.observed_at, source=excluded.source", (name, observation["inventory_meso"], observed["observed_at"], "llm_vision"))
        return {"status": "saved", "observation": observed}

    def snapshot(self, character_name: str | None = None, include_api_data: bool = False) -> dict[str, Any]:
        with self.connect() as db:
            # All evidence and its revision must belong to the same SQLite snapshot.
            db.execute("BEGIN")
            rows = dict(db.execute("SELECT key,value FROM records"))
            observation = json.loads(rows.get("screen_observation", "null"))
            # Retain the old installation's last nickname and balances as history.
            legacy = json.loads(rows.get("active_character", "null")) or json.loads(rows.get("last_browser_character", "null"))
            if isinstance(legacy, str):
                legacy = {"name": legacy}
            if not isinstance(legacy, dict):
                legacy = {}
            observed_name = observation.get("character_name") if observation else (legacy or {}).get("name")
            observed_at = observation.get("observed_at") if observation else json.loads(rows.get("observer", "{}")).get("last_frame_at")
            if not observation and not observed_at and observed_name:
                historical = db.execute("SELECT observed_at FROM meso WHERE character=?", (observed_name,)).fetchone()
                observed_at = historical[0] if historical else None
            name = character_name or observed_name
            meso = db.execute("SELECT value,observed_at,source FROM meso WHERE character=?", (name,)).fetchone() if name else None
            facts = self.context_facts(db, name) if name else {}
            context = {field: fact["value"] for field, fact in facts.items()}
            revision = self.revision(db, name) if name else 0
            api = [json.loads(row[0]) for row in db.execute("SELECT value FROM api_observations WHERE character=? ORDER BY rowid DESC", (name,))] if name else []
            assessment = json.loads(rows.get("assessment:" + str(name), "null"))
            if assessment:
                assessment["needs_review"] = assessment["based_on_revision"] != revision
                assessment["age_seconds"] = age_seconds(assessment["assessed_at"])
        matching = (observation if observation and observation.get("character_name") == name
                    else json.loads(rows.get("screen:" + str(name), "null")))
        for item in api:
            item["age_seconds"] = age_seconds(item["fetched_at"])
            if not include_api_data:
                item.pop("data", None)
        character = {"name": observed_name, "source": observation["source"] if observation else "legacy_screen_observation", "observed_at": observed_at, "age_seconds": age_seconds(observed_at), "is_live": False} if observed_name else None
        return {"state_schema_version": 2, "revision": revision,
                "capture_mode": "on_demand", "character_name": name, "last_observed_character": character,
                "inventory_meso": {"value": meso[0], "observed_at": meso[1], "source": meso[2], "age_seconds": age_seconds(meso[1]), "is_current_balance": False} if meso else None,
                "screen_observation": matching, "tooltips": matching.get("tooltips", []) if matching else [],
                "user_context": context, "context_facts": facts, "growth_assessment": assessment, "api_observations": api,
                "last_reset": json.loads(rows.get("reset:" + str(name), "null"))}
