import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import httpx

from maplemate.config import Config
from maplemate.maple_mcp.catalog import catalog, list_apis, validate_parameters
from maplemate.maple_mcp.nexon import NexonClient
from maplemate.maple_mcp.service import MapleService
from maplemate.state import utc_now

TEST_ROOT = Path(__file__).resolve().parents[1] / ".cache" / "api-tests"


class CatalogTests(unittest.TestCase):
    def test_every_official_current_key_endpoint_can_be_called(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"date": "2026-10-08", "sample": "synthetic"})
        client = NexonClient("synthetic-secret", httpx.MockTransport(respond))
        try:
            self.assertEqual(len(catalog()["endpoints"]), 55)
            for endpoint, spec in catalog()["endpoints"].items():
                with self.subTest(endpoint=endpoint):
                    params = {}
                    for p in spec["parameters"]:
                        schema, name = p["schema"], p["name"]
                        if not p["required"]:
                            continue
                        params[name] = (schema["enum"][0] if "enum" in schema else
                                        "2026-10-08" if name == "date" else
                                        10 if name == "count" else
                                        0 if name in ("ranking_type", "difficulty") else
                                        1 if schema.get("type") in ("number", "integer") else "synthetic")
                    if endpoint.startswith("history/"):
                        params["date"] = "2026-10-08"
                    result = client.query(endpoint, params)
                    request = requests[-1]
                    self.assertEqual(request.url.host, "open.api.nexon.com")
                    self.assertEqual(request.url.path, "/maplestory/v1/" + endpoint)
                    self.assertEqual(dict(request.url.params), {k: str(v) for k, v in params.items()})
                    self.assertEqual(request.headers["x-nxopen-api-key"], "synthetic-secret")
                    self.assertEqual(result["data_date"], "2026-10-08")
                    self.assertFalse(result["realtime"])
                    self.assertNotIn("synthetic-secret", json.dumps(result))
            self.assertEqual(len(requests), 55)
        finally:
            client.close()

    def test_invalid_calls_are_rejected_before_any_request(self):
        requests = []
        client = NexonClient("synthetic-secret", httpx.MockTransport(lambda request: requests.append(request)))
        cases = [
            ("https://example.com", {}), ("../id", {}), ("/maplestory/legacy/ouid", {}),
            ("notice", {"x-nxopen-api-key": "synthetic-secret"}),
            ("character/skill", {}), ("character/skill", {"character_skill_grade": "7"}),
            ("character/basic", {"date": "2026-02-30"}),
            ("character/basic", {"date": "20261010"}),
            ("history/starforce", {"count": 10}),
            ("history/starforce", {"count": 9, "date": "2026-10-08"}),
            ("history/cube", {"count": 1001, "cursor": "synthetic"}),
            ("ranking/overall", {"date": "2026-10-08", "world_name": "made-up"}),
            ("notice/detail", {"notice_id": True}), ("notice/detail", {"notice_id": 1.5}),
            ("notice/detail", {"notice_id": 2**64}),
        ]
        try:
            for endpoint, params in cases:
                with self.subTest(endpoint=endpoint, params=params):
                    with self.assertRaises(ValueError) as caught:
                        name = "초보용사" if endpoint.startswith("character/") else None
                        client.query(endpoint, params, name)
                    self.assertNotIn("synthetic-secret", str(caught.exception))
            self.assertEqual(requests, [])
        finally:
            client.close()

    def test_skill_grade_and_nickname_resolution(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"ocid": "synthetic-ocid"} if request.url.path.endswith("/id") else {"date": "2026-10-08", "character_skill": []})
        client = NexonClient("synthetic-secret", httpx.MockTransport(respond))
        try:
            result = client.information("초보용사", "skill", "2026-10-08", {"character_skill_grade": "6"})
            self.assertEqual(len(requests), 2)
            self.assertEqual(dict(requests[1].url.params), {"ocid": "synthetic-ocid", "date": "2026-10-08", "character_skill_grade": "6"})
            self.assertEqual(result["requested_date"], "2026-10-08")
        finally:
            client.close()

    def test_discovery_and_cursor_keep_account_scope_explicit(self):
        self.assertEqual(len(list_apis(category="user")["endpoints"]), 5)
        self.assertEqual(len(list_apis(category="notice")["endpoints"]), 8)
        details = list_apis(endpoint="history/starforce")
        self.assertTrue(details["account_bound"])
        self.assertIn("확률", details["description"])
        self.assertEqual(len(list_apis()["excluded"]), 1)
        self.assertEqual(validate_parameters("history/potential", {"count": 10, "cursor": "synthetic-cursor"}), {"count": "10", "cursor": "synthetic-cursor"})


class ServiceApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = TEST_ROOT / uuid.uuid4().hex
        self.service = MapleService(Config.load(self.directory))

    def tearDown(self):
        assert self.directory.resolve().parent == TEST_ROOT.resolve()
        shutil.rmtree(self.directory)

    def test_recent_observation_only_fills_required_character_identity(self):
        # Use the same observation format exposed by StateStore.snapshot.
        snapshot = {"last_observed_character": {"name": "초보용사", "observed_at": utc_now()}}
        with patch.object(self.service.store, "snapshot", return_value=snapshot), patch("maplemate.maple_mcp.service.load_key", return_value="synthetic-secret"), patch("maplemate.maple_mcp.service.NexonClient") as client:
            client.return_value.query.side_effect = lambda endpoint, *_: {"source": "nexon_open_api", "endpoint": endpoint, "data": {}, "fetched_at": utc_now(), "data_date": None}
            result = self.service.api("user/union-artifact")
            self.assertEqual(result["name_selection"], "last_screen_observation")
            client.return_value.query.assert_called_with("user/union-artifact", {}, "초보용사")
            self.service.api("ranking/overall", {"date": "2026-10-08"})
            client.return_value.query.assert_called_with("ranking/overall", {"date": "2026-10-08"}, None)
            self.service.api("character/basic", {"ocid": "explicit-ocid"})
            client.return_value.query.assert_called_with("character/basic", {"ocid": "explicit-ocid"}, None)

    def test_stale_identity_and_bad_arguments_do_not_load_key(self):
        with patch.object(self.service.store, "snapshot", return_value={"last_observed_character": {"name": "초보용사", "observed_at": "2020-01-01T00:00:00+00:00"}}), patch("maplemate.maple_mcp.service.load_key") as key:
            self.assertEqual(self.service.api("user/union")["status"], "missing_character")
            self.assertEqual(self.service.api("history/cube", {"count": 10})["status"], "invalid_arguments")
            self.assertEqual(self.service.information("list", "초보용사")["status"], "invalid_arguments")
            key.assert_not_called()

    def test_api_growth_data_is_saved_with_attribution_and_errors_preserve_it(self):
        self.service.store.set("registered_characters", [{"character_name": "초보용사", "ocid": "synthetic-ocid"}])
        def respond(request):
            if request.url.path.endswith("/id"):
                return httpx.Response(200, json={"ocid": "synthetic-ocid"})
            return httpx.Response(200, json={"date": "2026-10-10", "synthetic": "growth data"})
        client = NexonClient("synthetic-secret", httpx.MockTransport(respond))
        with patch("maplemate.maple_mcp.service.load_key", return_value="synthetic-secret"), patch("maplemate.maple_mcp.service.NexonClient", return_value=client):
            result = self.service.information("stat", "초보용사")
        self.assertTrue(result["state_record"]["saved"])
        state = self.service.store.snapshot("초보용사", True)
        self.assertEqual(state["api_observations"][0]["data"]["synthetic"], "growth data")
        self.assertEqual(state["api_observations"][0]["source"], "nexon_open_api")
        self.assertNotIn("synthetic-secret", self.service.store.path.read_bytes().decode(errors="replace"))
        failed = NexonClient("synthetic-secret", httpx.MockTransport(lambda _: httpx.Response(403, json={"error": {"name": "OPENAPI00002"}})))
        with patch("maplemate.maple_mcp.service.load_key", return_value="synthetic-secret"), patch("maplemate.maple_mcp.service.NexonClient", return_value=failed):
            self.assertEqual(self.service.information("stat", "초보용사")["status"], "unavailable")
        after = self.service.store.snapshot("초보용사", True)["api_observations"][0]
        self.assertEqual(after["data"], state["api_observations"][0]["data"])
        self.assertEqual(after["fetched_at"], state["api_observations"][0]["fetched_at"])
        self.assertEqual(self.service.store.snapshot("초보용사")["revision"], state["revision"])

    def test_explicit_ocid_cache_requires_known_identity_and_does_not_use_active_name(self):
        self.service.store.set("registered_characters", [{"character_name": "초보용사", "ocid": "known-ocid"}])
        for ocid, saved in (("known-ocid", True), ("unknown-ocid", False)):
            client = NexonClient("synthetic-secret", httpx.MockTransport(lambda _: httpx.Response(200, json={"date": "2026-10-10"})))
            with patch("maplemate.maple_mcp.service.load_key", return_value="synthetic-secret"), patch("maplemate.maple_mcp.service.NexonClient", return_value=client):
                result = self.service.api("user/union", {"ocid": ocid})
            self.assertEqual(result["state_record"]["saved"], saved)
        self.assertEqual(len(self.service.store.snapshot("초보용사")["api_observations"]), 1)
