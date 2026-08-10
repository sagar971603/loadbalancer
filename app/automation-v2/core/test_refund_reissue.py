import unittest

from eportal_login_enhanced import EPortalClient


class RefundReissueTests(unittest.TestCase):
    def setUp(self):
        self.client = EPortalClient.__new__(EPortalClient)
        self.client.aadhaar_transaction_id = None
        self.client.refund_reissue_context = None

    def test_initialize_returns_only_eligible_banks(self):
        self.client.get_refund_failures = lambda pan: {
            "success": True,
            "data": {"workflowDetails": [{"ackNum": "ACK1"}]},
        }
        self.client.get_refund_bank_accounts = lambda pan: {
            "success": True,
            "data": {"bankDetails": [
                {"bankAcctNum": "1", "statusCd": "V", "refundFlag": "Y"},
                {"bankAcctNum": "2", "statusCd": "I", "refundFlag": "Y"},
            ]},
        }

        result = self.client.initialize_refund_reissue("ABCDE1234F")

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["eligible_bank_details"], [
            {"bankAcctNum": "1", "statusCd": "V", "refundFlag": "Y"}
        ])

    def test_send_and_complete_keeps_context_until_success(self):
        self.client.initialize_refund_reissue = lambda pan: {
            "success": True,
            "data": {
                "workflowDetails": [{"ackNum": "ACK1", "assmentYear": "2024"}],
                "eligible_bank_details": [{"accountNumber": "123", "statusCd": "V", "refundFlag": "Y"}],
            },
        }
        self.client.check_aadhaar_linked = lambda pan: {"success": True}
        self.client.send_otp_aadhaar = lambda pan: {"success": True, "aadhaar_transaction_id": "TX1"}
        self.client.validate_refund_aadhaar_otp = lambda *args: {"success": True}
        self.client.submit_refund_reissue = lambda *args: {"success": True, "data": {"submitted": True}}

        sent = self.client.send_refund_reissue_otp("ABCDE1234F", "ACK1", "123")
        completed = self.client.complete_refund_reissue("ABCDE1234F", "123456")

        self.assertTrue(sent["success"])
        self.assertEqual(sent["data"]["aadhaar_transaction_id"], "TX1")
        self.assertTrue(completed["success"])
        self.assertIsNone(self.client.refund_reissue_context)


if __name__ == "__main__":
    unittest.main()
