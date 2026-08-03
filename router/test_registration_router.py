import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import registration_router as router


class RouterTest(unittest.TestCase):
    def test_weighted_config_and_session_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "nginx.conf"
            config.write_text("""upstream regpan4_backend {
 server 217.217.249.145:8002 weight=2;
 server 217.216.78.35:8002 down;
 server 192.0.2.10:8002;
}\n""")
            with patch.object(router, "NGINX_CONFIG", config):
                backends = router.configured_backends()
            self.assertEqual(backends["a"]["capacity"], 4)
            self.assertFalse(backends["b"]["enabled"])
            self.assertIn("n192-0-2-10", backends)

        encoded = json.dumps({"session_id": "a~rg_example"}).encode()
        route, stripped = router.session_route(encoded)
        self.assertEqual(route, "a")
        self.assertEqual(json.loads(stripped)["session_id"], "rg_example")

        encoded = json.dumps({"session_id": "n147-93-169-153~rg_example"}).encode()
        route, stripped = router.session_route(encoded)
        self.assertEqual(route, "n147-93-169-153")
        self.assertEqual(json.loads(stripped)["session_id"], "rg_example")
        response = router.prefix_sessions({"data": {"session_id": "rg_example"}}, "a")
        self.assertEqual(response["data"]["session_id"], "a~rg_example")

        route, unchanged = router.session_route(json.dumps({"session_id": "rg_example"}).encode())
        self.assertEqual(route, "a")
        self.assertEqual(json.loads(unchanged)["session_id"], "rg_example")

        route, unchanged = router.session_route(json.dumps({"session_id": []}).encode())
        self.assertIsNone(route)

    def test_weighted_round_robin_survives_fast_failures(self):
        backends = {
            "a": {"endpoint": "a:8002", "capacity": 4, "enabled": True},
            "c": {"endpoint": "c:8002", "capacity": 2, "enabled": True},
            "d": {"endpoint": "d:8002", "capacity": 2, "enabled": True},
            "e": {"endpoint": "e:8002", "capacity": 4, "enabled": True},
            "f": {"endpoint": "f:8002", "capacity": 4, "enabled": True},
        }
        router.TIE_CURSOR = 0
        router.PENDING.clear()
        router.COOLDOWN_UNTIL.clear()
        with patch.object(router, "configured_backends", return_value=backends), \
             patch.object(router, "backend_health", return_value=(True, 0)):
            chosen = []
            for _ in range(8):
                route, _ = router.choose_backend()
                chosen.append(route)
                router.release_backend(route)
        self.assertEqual(chosen, ["a", "a", "c", "d", "e", "e", "f", "f"])

    def test_cooling_backend_is_skipped(self):
        backends = {
            "a": {"endpoint": "a:8002", "capacity": 5, "enabled": True},
            "c": {"endpoint": "c:8002", "capacity": 5, "enabled": True},
        }
        router.TIE_CURSOR = 0
        router.PENDING.clear()
        router.COOLDOWN_UNTIL.clear()
        router.COOLDOWN_UNTIL["a"] = 200
        with patch.object(router, "configured_backends", return_value=backends), \
             patch.object(router, "backend_health", return_value=(True, 0)), \
             patch.object(router.time, "monotonic", return_value=100):
            route, _ = router.choose_backend()
        router.release_backend(route)
        router.COOLDOWN_UNTIL.clear()
        self.assertEqual(route, "c")

    def test_failed_backend_is_not_selected_again_for_same_init(self):
        backends = {
            "a": {"endpoint": "a:8002", "capacity": 2, "enabled": True},
            "c": {"endpoint": "c:8002", "capacity": 2, "enabled": True},
        }
        router.TIE_CURSOR = 0
        router.PENDING.clear()
        router.COOLDOWN_UNTIL.clear()
        with patch.object(router, "configured_backends", return_value=backends), \
             patch.object(router, "backend_health", return_value=(True, 0)):
            route, _ = router.choose_backend({"a"})
        router.release_backend(route)
        self.assertEqual(route, "c")

    def test_only_safe_step_one_network_error_is_retried(self):
        network_error = "Page.evaluate: NetworkError when attempting to fetch resource."
        self.assertTrue(router.is_safe_init_retry({
            "success": False,
            "message": "Registration initialization failed",
            "error": network_error,
        }))
        self.assertFalse(router.is_safe_init_retry({
            "success": False,
            "message": "Aadhaar validation failed",
            "error": network_error,
        }))


if __name__ == "__main__":
    unittest.main()
