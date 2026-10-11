import json
import unittest
import httpx
from maplemate.maple_mcp.nexon import NexonClient, NexonError
from maplemate.maple_mcp.expectation import calculate_expectation
from maplemate.secrets import SecretError

class NexonTests(unittest.TestCase):
    def test_invalid_key_is_rejected_without_echoing_it(self):
        for value in ("synthetic-secret\nother", "synthetic-secret\x00", "synthetic-secret 비밀", ""):
            with self.assertRaises(SecretError) as caught:
                NexonClient(value)
            self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_request_header_and_sections_with_source_metadata(self):
        requests = []
        def response(request):
            requests.append(request)
            if request.url.path.endswith("/id"):
                return httpx.Response(200, json={"ocid": "synthetic-ocid"})
            return httpx.Response(200, json={"date": "2026-10-08", "character_name": "초보용사"})
        client = NexonClient("synthetic-api-key", httpx.MockTransport(response))
        try:
            result = client.information("초보용사", "basic")
        finally:
            client.close()
        self.assertEqual(requests[0].headers["x-nxopen-api-key"], "synthetic-api-key")
        self.assertEqual(requests[0].url.params["character_name"], "초보용사")
        self.assertEqual(result["data_date"], "2026-10-08")
        self.assertFalse(result["realtime"])
        self.assertNotIn("synthetic-api-key", json.dumps(result))

    def test_upstream_error_does_not_leak_a_secret(self):
        client = NexonClient("synthetic-secret", httpx.MockTransport(lambda _: httpx.Response(403, json={"error": {"name": "OPENAPI00002", "message": "synthetic-secret"}})))
        try:
            with self.assertRaises(NexonError) as caught:
                client.characters()
            self.assertNotIn("synthetic-secret", str(caught.exception))
        finally:
            client.close()


class ExpectationTests(unittest.TestCase):
    def call(self, states):
        return calculate_expectation(states, "start", "target", ["https://maplestory.nexon.com/News/Update"], "2026-10-09", ["Synthetic probabilities for testing; not MapleStory rules."])

    def test_repeated_attempt_and_conditional_recovery_cost(self):
        result = self.call([{"id": "start", "attempt_cost": 10, "transitions": [{"next_state": "target", "probability": 0.5}, {"next_state": "start", "probability": 0.25}, {"next_state": "start", "probability": 0.25, "extra_cost": 100, "destruction": True}]}])
        self.assertAlmostEqual(result["expected_meso"], 70)
        self.assertAlmostEqual(result["expected_attempts"], 2)
        self.assertAlmostEqual(result["expected_destructions"], 0.5)

    def test_closed_loop_and_invalid_probabilities_are_rejected(self):
        for transitions in ([{"next_state": "start", "probability": 1}], [{"next_state": "target", "probability": 0.9}]):
            with self.assertRaises(ValueError):
                self.call([{"id": "start", "attempt_cost": 1, "transitions": transitions}])


if __name__ == "__main__":
    unittest.main()
