import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from eportal_login_enhanced import EPortalClient


class PaymentBankCodeTests(unittest.TestCase):
    def client(self):
        client = EPortalClient.__new__(EPortalClient)
        client.config = SimpleNamespace(base_url="https://example.invalid")
        client.payment_pan = "ABCDE1234F"
        client.payment_mobile = "9999999999"
        client.payment_req_id = "test-request"
        client.payment_year = "2026-27"
        client.payment_refrence_no = "test-draft"
        client.old_regime = True
        client.minor_head = "100"
        client.minor_sl_no = "1"
        client.add_tax_applicable_details = lambda *args, **kwargs: {"success": True}
        client.add_payment_details = lambda **kwargs: {"success": True}
        client.calls = []

        def post(url, json_data, sn):
            client.calls.append(json_data)
            return SimpleNamespace(status_code=200)

        client._safe_post = post
        client._safe_parse_json = lambda response: ({
            "successFlag": True, "crn": "test-crn", "bankName": "Display name",
            "bankUrl": "https://example.invalid/payment", "messages": [],
        }, None)
        return client

    def test_bank_is_consistent_at_every_payment_step(self):
        for choice, expected in [(None, "CNRB"), ("", "CNRB"), ("  ", "CNRB"), (" hdfc ", "HDFC"), ("MAHB", "MAHB")]:
            with self.subTest(choice=choice):
                client = self.client()
                result = client.pay_payment("ABCDE1234F", {"basicTax": 1000, "totalAmt": 1000}, bank_code=choice)
                self.assertTrue(result["success"])
                self.assertEqual(client.calls[0]["formData"]["bankCd"], expected)
                self.assertEqual(client.calls[1]["formData"]["bankCode"], expected)
                self.assertEqual(client.calls[2]["bank_cd"], expected)

                client = self.client()
                self.assertTrue(client.create_payment_challan(bank_code=choice)["success"])
                self.assertTrue(client.create_payment()["success"])
                self.assertEqual(client.calls[0]["formData"]["bankCode"], expected)
                self.assertEqual(client.calls[1]["formData"]["bank_cd"], expected)

        client = self.client()
        self.assertTrue(client.pay_payment("ABCDE1234F", {})["success"])
        self.assertEqual(client.calls[2]["bank_cd"], "CNRB")

    def test_bad_bank_is_rejected_before_any_portal_request(self):
        for choice in (123, {}, "BAD", "CNRB;", "../BANK"):
            client = self.client()
            result = client.pay_payment("ABCDE1234F", {}, bank_code=choice)
            self.assertFalse(result["success"])
            self.assertIn("bank_code", result["error"])
            self.assertEqual(client.calls, [])

    def test_websocket_forwards_bank_choice_to_both_flows(self):
        source = Path(__file__).parents[1] / "api" / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        calls = {
            node.args[0].attr: node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "to_thread" and node.args
            and isinstance(node.args[0], ast.Attribute)
        }
        for method, keyword in [("pay_payment", "bank_code"), ("create_payment_challan", "bank_code"), ("create_payment", "bank_cd")]:
            self.assertIn(keyword, {item.arg for item in calls[method].keywords})


if __name__ == "__main__":
    unittest.main()
