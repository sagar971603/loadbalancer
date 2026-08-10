import os
import unittest

os.environ["CLIENT_KEY"] = "test-key"

from main import WebSocketMessage


class RefundActionTests(unittest.TestCase):
    def test_refund_actions_are_accepted(self):
        for action in (
            "refund_reissue_init",
            "refund_reissue_send_otp",
            "refund_reissue_submit",
            "refund_reissue_status",
        ):
            self.assertEqual(WebSocketMessage(action=action).action, action)


if __name__ == "__main__":
    unittest.main()
