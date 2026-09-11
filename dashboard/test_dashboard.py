import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard import server


class DashboardTest(unittest.TestCase):
    def test_two_machine_topology_and_outgoing_ips(self):
        self.assertEqual([item["id"] for item in server.MACHINES], ["node-i", "node-j"])
        node_i = next(item for item in server.MACHINES if item["id"] == "node-i")
        node_j = next(item for item in server.MACHINES if item["id"] == "node-j")
        self.assertEqual(node_i["egress"]["registration"], ["217.217.249.229", "147.93.168.74"])
        self.assertEqual(node_j["egress"]["newtool"], ["217.216.58.27", "147.93.168.146"])

    def test_parse_upstream_and_status_shape(self):
        config = """upstream demo {
    ip_hash;
    server 10.0.0.1:8000 max_fails=3;
    server 10.0.0.2:8000 down;
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nginx.conf"
            path.write_text(config, encoding="utf-8")
            rows = server.parse_upstream(str(path), "demo")
        self.assertEqual(rows["10.0.0.1"], {"port": 8000, "enabled": True})
        self.assertEqual(rows["10.0.0.2"], {"port": 8000, "enabled": False})

    def test_saved_production_upstreams_only_use_live_nodes(self):
        root = Path(__file__).resolve().parents[1]
        sites = root / "backup" / "load-balancer" / "nginx" / "sites-available"
        newtool = server.parse_upstream(str(sites / "automation_v2"), "newtool2_backend")
        registration = server.parse_upstream(
            str(sites / "regpan4.fskindia.com"), "regpan4_backend"
        )
        expected = {"217.217.249.229", "217.216.58.27"}
        self.assertEqual(set(newtool), expected)
        self.assertEqual(set(registration), expected)
        self.assertTrue(all(route["enabled"] for route in newtool.values()))
        self.assertTrue(all(route["enabled"] for route in registration.values()))

    @patch("dashboard.server.urllib.request.urlopen")
    def test_health_failure_is_safe(self, urlopen):
        urlopen.side_effect = OSError("offline")
        result = server.fetch_health("newtool", "10.0.0.1", 8000)
        self.assertFalse(result["healthy"])
        self.assertIn("offline", result["error"])


if __name__ == "__main__":
    unittest.main()
