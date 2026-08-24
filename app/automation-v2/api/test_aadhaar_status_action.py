import os
import unittest

os.environ["CLIENT_KEY"] = "test-key"

from main import WebSocketMessage, valid_pan_aadhaar


class AadhaarStatusActionTests(unittest.TestCase):
    def test_action_and_identifiers_are_validated(self):
        self.assertEqual(
            WebSocketMessage(action="check_aadhaar_status").action,
            "check_aadhaar_status",
        )
        self.assertTrue(valid_pan_aadhaar("ABCDE1234F", "234567890123"))
        self.assertFalse(valid_pan_aadhaar("BADPAN", "234567890123"))
        self.assertFalse(valid_pan_aadhaar("ABCDE1234F", "1234"))


if __name__ == "__main__":
    unittest.main()
