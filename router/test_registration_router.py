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
            self.assertEqual(backends["a"]["capacity"], 10)
            self.assertFalse(backends["b"]["enabled"])
            self.assertIn("n192-0-2-10", backends)

        encoded = json.dumps({"session_id": "a~rg_example"}).encode()
        route, stripped = router.session_route(encoded)
        self.assertEqual(route, "a")
        self.assertEqual(json.loads(stripped)["session_id"], "rg_example")
        response = router.prefix_sessions({"data": {"session_id": "rg_example"}}, "a")
        self.assertEqual(response["data"]["session_id"], "a~rg_example")

        route, unchanged = router.session_route(json.dumps({"session_id": "rg_example"}).encode())
        self.assertEqual(route, "a")
        self.assertEqual(json.loads(unchanged)["session_id"], "rg_example")

        route, unchanged = router.session_route(json.dumps({"session_id": []}).encode())
        self.assertIsNone(route)


if __name__ == "__main__":
    unittest.main()
