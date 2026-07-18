import importlib
import os
import unittest


class EgressSlotsTest(unittest.TestCase):
    def test_slots_are_balanced_and_released(self):
        os.environ["EGRESS_PROXY_POOL"] = "http://one,http://two"
        os.environ["EGRESS_MAX_ACTIVE"] = "2"
        module = importlib.import_module("playwrite_login_with_session_cookie")
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


if __name__ == "__main__":
    unittest.main()
