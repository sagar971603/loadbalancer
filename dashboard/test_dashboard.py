import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard import server


class DashboardTest(unittest.TestCase):
    def test_eight_machine_topology_and_new_outgoing_ips(self):
        self.assertEqual(len(server.MACHINES), 8)
        node_e = next(item for item in server.MACHINES if item["id"] == "node-e")
        node_f = next(item for item in server.MACHINES if item["id"] == "node-f")
        node_g = next(item for item in server.MACHINES if item["id"] == "node-g")
        node_h = next(item for item in server.MACHINES if item["id"] == "node-h")
        self.assertEqual(node_e["egress"]["registration"], ["147.93.169.153", "147.93.171.244"])
        self.assertEqual(node_f["egress"]["newtool"], ["147.93.171.101", "147.93.171.245"])
        self.assertEqual(node_g["egress"]["registration"], ["147.93.169.212", "147.93.169.213"])
        self.assertEqual(node_h["egress"]["newtool"], ["147.93.169.214", "147.93.169.215"])

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

    @patch("dashboard.server.urllib.request.urlopen")
    def test_health_failure_is_safe(self, urlopen):
        urlopen.side_effect = OSError("offline")
        result = server.fetch_health("newtool", "10.0.0.1", 8000)
        self.assertFalse(result["healthy"])
        self.assertIn("offline", result["error"])


if __name__ == "__main__":
    unittest.main()
