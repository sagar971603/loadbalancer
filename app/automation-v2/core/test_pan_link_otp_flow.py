import unittest
from types import SimpleNamespace

from eportal_login_enhanced import EPortalClient


class PanLinkOtpFlowTests(unittest.TestCase):
    def test_valid_otp_saves_the_linkage_request(self):
        client = object.__new__(EPortalClient)
        client.validate_pan_adhaar_number = lambda **_: {"success": True}
        client.get_validate_adhaar_pan_linkage = lambda **_: {"success": True}
        client.send_adhar_pan_link_otp = lambda **_: {"success": True}

        generated = client.generate_pan_link_otp(
            aadhar_no="234567890123",
            pan="ABCDE1234F",
            mobile="9999999999",
            name="TEST USER",
        )
        self.assertTrue(generated["success"])

        calls = []
        responses = iter(
            [
                SimpleNamespace(
                    status_code=200,
                    data={"result": "OTP VALIDATED", "messages": [], "errors": []},
                ),
                SimpleNamespace(
                    status_code=200,
                    data={"messages": [{"code": "EF40127"}], "errors": []},
                ),
            ]
        )

        def safe_post(url, json_data, sn):
            calls.append((url, json_data, sn))
            return next(responses)

        client._safe_post = safe_post
        client._safe_parse_json = lambda response: (response.data, None)

        result = client.vaildate_adhar_pan_link_otp("123456")

        self.assertTrue(result["success"])
        self.assertEqual(
            calls[0][1],
            {
                "userId": "ABCDE1234F",
                "serviceName": "linkAadharOtpService",
                "otpValue": "123456",
            },
        )
        self.assertEqual(calls[1][1]["aadhaarNumber"], "234567890123")
        self.assertEqual(calls[1][1]["nameAsOnAadhaarCard"], "TEST USER")
        self.assertEqual(calls[1][1]["serviceName"], "linkAadhaarPreLoginService")


if __name__ == "__main__":
    unittest.main()
