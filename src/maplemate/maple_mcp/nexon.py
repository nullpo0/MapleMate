from datetime import datetime, timezone
from typing import Any
import httpx
from ..secrets import validate_key
from .catalog import endpoint_spec, validate_parameters

ORIGIN = "https://open.api.nexon.com"
ATTRIBUTION = "Data based on NEXON Open API"


class NexonError(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(f"Nexon API request failed ({code}).")


class NexonClient:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        api_key = validate_key(api_key)
        self._client = httpx.Client(base_url=ORIGIN, headers={"x-nxopen-api-key": api_key}, timeout=10, follow_redirects=False, trust_env=False, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _get(self, endpoint: str, **parameters: str) -> dict[str, Any]:
        try:
            response = self._client.get("/maplestory/v1/" + endpoint, params=parameters)
        except httpx.HTTPError:
            raise NexonError("network_unavailable") from None
        if response.status_code != 200:
            # Never propagate headers, request objects, or an upstream error message.
            code = "http_error"
            try:
                candidate = response.json().get("error", {}).get("name", "")
                if candidate.startswith("OPENAPI") and candidate[7:].isdigit():
                    code = candidate
            except (ValueError, AttributeError):
                pass
            raise NexonError(code, response.status_code)
        try:
            result = response.json()
        except ValueError:
            raise NexonError("invalid_response") from None
        if not isinstance(result, dict):
            raise NexonError("invalid_response")
        return result

    def character_id(self, name: str) -> str:
        result = self._get("id", character_name=name)
        if not isinstance(result.get("ocid"), str):
            raise NexonError("missing_character_id")
        return result["ocid"]

    def characters(self) -> list[dict[str, str]]:
        result = self._get("character/list")
        characters = []
        for account in result.get("account_list", []):
            for character in account.get("character_list", []):
                if character.get("character_name") and character.get("ocid"):
                    characters.append({field: str(character.get(field, "")) for field in ("character_name", "ocid", "world_name", "character_class")})
        return characters

    def query(self, endpoint: str, parameters: dict[str, str | int] | None = None,
              character_name: str | None = None) -> dict[str, Any]:
        spec = endpoint_spec(endpoint)
        query = dict(parameters or {})
        uses_ocid = any(p["name"] == "ocid" for p in spec["parameters"])
        if character_name and (not uses_ocid or "ocid" in query):
            raise ValueError("character_name requires an ocid endpoint without an explicit ocid")
        # Validate everything before spending a request on nickname resolution.
        query = validate_parameters(endpoint, query, allow_missing_ocid=bool(character_name))
        if character_name:
            if not 1 <= len(character_name) <= 30 or any(ord(c) < 32 for c in character_name):
                raise ValueError("Invalid character name")
            query["ocid"] = self.character_id(character_name)
        data = self._get(endpoint, **query)
        return {"source": "nexon_open_api", "attribution": ATTRIBUTION, "endpoint": endpoint,
                "documentation_url": spec["documentation_url"], "account_bound": spec["account_bound"],
                "fetched_at": datetime.now(timezone.utc).isoformat(), "requested_date": query.get("date"),
                "data_date": data.get("date"), "realtime": False, "data": data}

    def information(self, name: str, section: str, date: str | None = None,
                    parameters: dict[str, str | int] | None = None) -> dict[str, Any]:
        endpoint = "character/" + section
        spec = endpoint_spec(endpoint)
        if not any(p["name"] == "ocid" for p in spec["parameters"]):
            raise ValueError("Select a character-specific endpoint")
        query = dict(parameters or {})
        if date is not None:
            if "date" in query:
                raise ValueError("Provide date only once")
            query["date"] = date
        return self.query(endpoint, query, name)
