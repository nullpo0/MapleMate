"""Bundled official API allowlist; never accepts URLs, headers, or arbitrary paths."""
from datetime import date
from functools import lru_cache
from importlib.resources import files
import json
from typing import Any


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return json.loads(files("maplemate.maple_mcp").joinpath("nexon_catalog.json").read_text(encoding="utf-8"))


def endpoint_spec(endpoint: str) -> dict[str, Any]:
    spec = catalog()["endpoints"].get(endpoint)
    if spec is None:
        raise ValueError("Unknown endpoint. Use list_nexon_apis to select an endpoint.")
    return spec


def list_apis(category: str | None = None, endpoint: str | None = None) -> dict[str, Any]:
    data = catalog()
    common = {"source": "bundled_nexon_official_api_catalog", "verified_at": data["verified_at"],
              "documentation_url": data["documentation_url"], "total_endpoints": len(data["endpoints"]),
              "excluded": data["excluded"]}
    if endpoint is not None:
        return {**common, "endpoint": endpoint, **endpoint_spec(endpoint)}
    # Prefixes (character, user, ranking...) and official category names both work.
    entries = [{"endpoint": name, "category": spec["category"], "summary": spec["summary"],
                "account_bound": spec["account_bound"],
                "required_parameters": [p["name"] for p in spec["parameters"] if p["required"]],
                "optional_parameters": [p["name"] for p in spec["parameters"] if not p["required"]]}
               for name, spec in data["endpoints"].items()
               if category is None or category == spec["category"] or name == category or name.startswith(category + "/") or name.startswith(category + "-")]
    return {**common, "endpoints": entries}


def validate_parameters(endpoint: str, parameters: dict[str, Any], *, allow_missing_ocid: bool = False) -> dict[str, str]:
    spec = endpoint_spec(endpoint)
    allowed = {p["name"]: p for p in spec["parameters"]}
    if set(parameters) - set(allowed):
        raise ValueError("Unsupported query parameter. See list_nexon_apis(endpoint=...).")
    result = {}
    for name, parameter in allowed.items():
        if name not in parameters:
            if parameter["required"] and not (name == "ocid" and allow_missing_ocid):
                raise ValueError(f"Missing required parameter: {name}")
            continue
        value = parameters[name]
        if type(value) not in (str, int) or not 1 <= len(str(value)) <= 4096 or any(ord(c) < 32 for c in str(value)):
            raise ValueError(f"Invalid parameter: {name}")
        text = str(value)
        schema = parameter["schema"]
        if schema.get("type") in ("integer", "number") or name == "count":
            if not text.isascii() or not text.isdigit():
                raise ValueError(f"Expected a non-negative integer: {name}")
            number = int(text)
            if number > 2**63 - 1:
                raise ValueError(f"Integer exceeds supported range: {name}")
            if name in ("page", "page_no", "notice_id") and number < 1:
                raise ValueError(f"Expected a positive integer: {name}")
            if name == "count" and not 10 <= number <= 1000:
                raise ValueError("count must be between 10 and 1000")
            choices = {"ranking_type": (0, 1, 2), "difficulty": (0, 1), "world_type": (0, 1)}
            if name in choices and number not in choices[name]:
                raise ValueError(f"Invalid choice: {name}")
        if "enum" in schema and text not in [str(x) for x in schema["enum"]]:
            raise ValueError(f"Invalid choice: {name}. See list_nexon_apis(endpoint=...).")
        if name == "date":
            try:
                if date.fromisoformat(text).isoformat() != text:
                    raise ValueError()
            except ValueError:
                raise ValueError("date must be a valid YYYY-MM-DD") from None
        result[name] = text
    if endpoint.startswith("history/") and not (result.get("date") or result.get("cursor")):
        raise ValueError("History queries require date or cursor")
    return result
