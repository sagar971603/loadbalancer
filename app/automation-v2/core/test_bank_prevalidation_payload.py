import unittest
from types import SimpleNamespace

from eportal_login_enhanced import EPortalClient


class BankPrevalidationPayloadTest(unittest.TestCase):
    def test_prevalidate_save_uses_account_number(self):
        client = EPortalClient.__new__(EPortalClient)
        client.account_details = {
            "accountNumber": "123456789012",
            "accountType": "savings",
            "accountHolderType": "individual",
            "ifsc": "ABCD0123456",
        }
        client.branch_details = {"bank_name": "Example Bank", "branch_txt": "Example Branch"}
        client.user_profile = {"priMobileNum": "9999999999", "priEmailId": "test@example.invalid"}
        client.credentials = SimpleNamespace(pan="ABCDE1234F")
        client.bnk_transaction_no = "test-transaction"
        captured = {}

        def fake_post(url, json_data, sn):
            captured.update(json_data)
            return SimpleNamespace(status_code=200)

        client._safe_post = fake_post
        client._safe_parse_json = lambda response: ({"messages": [{"code": "EF00000"}]}, None)

        result = client.prevalidate_save()

        self.assertTrue(result["success"])
        self.assertEqual(captured["bankAcctNum"], "123456789012")


if __name__ == "__main__":
    unittest.main()
