"""Shared account setup for terminal and desktop entry points."""
from .config import Config
from .maple_mcp.nexon import NexonClient, NexonError
from .secrets import save_key
from .state import StateStore, utc_now


def registered_character(client: NexonClient, name: str) -> dict[str, str]:
    basic = client.information(name, "basic")["data"]
    return {"character_name": name, "ocid": client.character_id(name), "world_name": basic.get("world_name", ""), "identity_verified_by": "nexon_api_user_registered"}


def configure_key(config: Config, key: str, character_name: str | None = None) -> int:
    client = NexonClient(key)
    try:
        try:
            characters = client.characters()
        except NexonError:
            if not character_name:
                raise
            characters = []
        if not characters and character_name:
            characters = [registered_character(client, character_name)]
    finally:
        client.close()
    save_key(config.data_dir, key)
    store = StateStore(config.data_dir)
    store.set("registered_characters", characters)
    store.set("api_connection", {"verified_at": utc_now(), "character_count": len(characters)})
    return len(characters)
