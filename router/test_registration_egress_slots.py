import importlib
import os
import unittest
from unittest.mock import patch


class EgressSlotsTest(unittest.TestCase):
    def test_slots_are_balanced_and_released(self):
        os.environ["EGRESS_PROXY_POOL"] = "http://one,http://two"
        os.environ["EGRESS_MAX_ACTIVE"] = "2"
        module = importlib.import_module("playwrite_login_with_session_cookie")
        module = importlib.reload(module)
        one = module.EPortalLoginStealth({})
        two = module.EPortalLoginStealth({})
        three = module.EPortalLoginStealth({})
        self.assertEqual([one.proxy_server, two.proxy_server, three.proxy_server], ["http://one", "http://two", "http://one"])
        one.close()
        four = module.EPortalLoginStealth({})
        self.assertEqual(four.proxy_server, "http://one")
        two.close()
        three.close()
        four.close()
        self.assertTrue(all(item["active"] == 0 for item in module.proxy_slot_status().values()))

    def test_three_transport_failures_open_one_ip_circuit(self):
        os.environ["EGRESS_PROXY_POOL"] = "http://one"
        os.environ["EGRESS_MAX_ACTIVE"] = "2"
        os.environ["REG_EGRESS_FAILURE_THRESHOLD"] = "3"
        os.environ["REG_EGRESS_COOLDOWN_BASE_SECONDS"] = "1800"
        module = importlib.reload(importlib.import_module("playwrite_login_with_session_cookie"))

        with patch.object(module.time, "monotonic", return_value=100):
            for _ in range(3):
                module._record_proxy_result("http://one", False)
            status = module.proxy_slot_status()["http://one"]

        self.assertFalse(status["available"])
        self.assertEqual(status["cooldown_seconds"], 1800)

        with patch.object(module.time, "monotonic", return_value=1901):
            self.assertTrue(module.proxy_slot_status()["http://one"]["half_open"])
            module._record_proxy_result("http://one", True)
            self.assertTrue(module.proxy_slot_status()["http://one"]["available"])


if __name__ == "__main__":
    unittest.main()
