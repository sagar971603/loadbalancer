import unittest
from types import SimpleNamespace

from eportal_login_enhanced import EPortalClient


class Response:
    status_code = 200

    @staticmethod
    def json():
        return {"messages": []}


class PanLinkChallanTests(unittest.TestCase):
    def test_challan_uses_current_portal_fields(self):
        client = EPortalClient.__new__(EPortalClient)
        client.config = SimpleNamespace(base_url="https://example.test")
        client.payment_pan = "ABCDE1234F"
        client.payment_mobile = "9999999999"
        client.payment_req_id = "request-id"
        captured = {}

        def safe_post(_url, json_data, sn):
            captured.update(json_data)
            self.assertEqual(sn, "prelogincreatechallan")
            return Response()

        client._safe_post = safe_post

        result = client.create_payment_challan(assessment_year="2026-27")

        self.assertTrue(result["success"])
        form_data = captured["formData"]
        self.assertEqual(form_data["taxYear"], "2026-27")
        self.assertEqual(form_data["actType"], "N")
        self.assertNotIn("assmentYear", form_data)


if __name__ == "__main__":
    unittest.main()
