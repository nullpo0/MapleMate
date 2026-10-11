"""Refresh the bundled KMS GET catalog from Nexon's public Swagger documents.

Developer-only: pip install PyYAML. No player key or authenticated request is used.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import re

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = "https://openapi.nexon.com/ko/game/maplestory/"


def generate():
    with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
        response = client.get(DOCS)
        response.raise_for_status()
        html = response.content.decode("utf-8")
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
        categories = json.loads(match.group(1))["props"]["pageProps"]["categoryResult"]
        endpoints, excluded, sources = {}, [], []
        for category in categories:
            language = next(item for item in category["language"] if item["code"] == "ko")
            url = language["fileUrl"]
            if not url.startswith("https://openapi.nexon.com/static/api/maplestory/"):
                raise ValueError("Unexpected official schema URL")
            schema_response = client.get(url)
            schema_response.raise_for_status()
            schema = yaml.safe_load(schema_response.content.decode("utf-8-sig"))
            sources.append(url)
            for path, methods in schema["paths"].items():
                operation = methods.get("get")
                if not operation:
                    continue
                if not path.startswith("/maplestory/v1/"):
                    excluded.append({"path": path, "reason": "Requires a separate legacy API key."})
                    continue
                parameters = []
                for parameter in operation.get("parameters", []):
                    if parameter["in"] != "query":
                        continue
                    parameters.append({"name": parameter["name"], "required": bool(parameter.get("required")),
                                       "schema": parameter.get("schema", {}),
                                       "description": parameter.get("description", "")})
                endpoint = path.removeprefix("/maplestory/v1/")
                endpoints[endpoint] = {"category": language["categoryName"],
                                       "summary": operation.get("summary", ""),
                                       "description": operation.get("description", ""),
                                       "documentation_url": DOCS + "?id=" + str(language["id"]),
                                       "parameters": parameters,
                                       "account_bound": endpoint in ("character/list", "user/achievement", "ouid", "scheduler/character-state") or endpoint.startswith("history/")}
    catalog = {"verified_at": datetime.now(timezone.utc).isoformat(), "documentation_url": DOCS,
               "schema_urls": sources, "excluded": excluded, "endpoints": endpoints}
    target = ROOT / "src" / "maplemate" / "maple_mcp" / "nexon_catalog.json"
    target.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {len(endpoints)} current-key GET endpoints; excluded {len(excluded)} legacy endpoints.")


if __name__ == "__main__":
    generate()
