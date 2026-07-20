import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import regmainhybrid as api
from registration_core import ePortalRegistrationApi


class FakeService:
    def __init__(self):
        self.otp_calls = 0
        self.details_calls = 0
        self.contact_calls = 0
        self.password_calls = 0

    def step4_validate_otp(self, **_kwargs):
        self.otp_calls += 1
        return {"success": True}

    def step2_validate_details(self):
        self.details_calls += 1
        return {"success": self.details_calls > 1, "error": "temporary_network_error"}

    def step3_validate_contact(self):
        self.contact_calls += 1
        return {"success": True}

    def step5_set_new_password(self):
        self.password_calls += 1
        return {"success": self.password_calls > 1, "error": "temporary_network_error"}

    def close(self):
        pass


class FakePage:
    def __init__(self):
        self.calls = []

    def evaluate(self, script, arguments):
        self.calls.append((script, arguments))
        return {
            "status": 200,
            "headers": [["content-type", "application/json"]],
            "text": '{"ok":true}',
            "attempts": 2,
        }


class FakePortalResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"reqId": "request-1", "messages": [{"code": "EF00000", "type": "INFO"}]}


class RegistrationResilienceTest(unittest.TestCase):
    def setUp(self):
        api.registration_sessions.clear()
        self.service = FakeService()
        now = datetime.now()
        api.registration_sessions["rg_test_session"] = {
            "service": self.service,
            "created_at": now,
            "expires_at": now + timedelta(minutes=5),
            "browser_expires_at": now + timedelta(minutes=10),
            "stage": "aadhaar_otp_pending",
            "is_aadhaar_flow": True,
            "lock": threading.RLock(),
        }

    def tearDown(self):
        api.registration_sessions.clear()

    def test_aadhaar_retry_resumes_after_completed_otp(self):
        request = api.AadhaarOTPRequest(session_id="rg_test_session", aadhaar_otp="123456")
        with patch.object(api, "metrics", None):
            first = api.verify_aadhaar_otp(request, "test")
            second = api.verify_aadhaar_otp(request, "test")

        self.assertFalse(first.success)
        self.assertTrue(second.success)
        self.assertEqual(self.service.otp_calls, 1)
        self.assertEqual(self.service.details_calls, 2)
        self.assertEqual(self.service.contact_calls, 1)
        self.assertEqual(api.registration_sessions["rg_test_session"]["stage"], "final_otp_pending")

    def test_final_retry_resumes_after_completed_otps(self):
        api.registration_sessions["rg_test_session"]["stage"] = "final_otp_pending"
        request = api.FinalOTPRequest(
            session_id="rg_test_session", mobile_otp="123456", email_otp="654321"
        )
        with patch.object(api, "metrics", None):
            first = api.verify_final_otp(request, "test")
            second = api.verify_final_otp(request, "test")

        self.assertFalse(first.success)
        self.assertTrue(second.success)
        self.assertEqual(self.service.otp_calls, 1)
        self.assertEqual(self.service.password_calls, 2)
        self.assertNotIn("rg_test_session", api.registration_sessions)

    def test_browser_transport_retries_fetch_once(self):
        page = FakePage()
        context = type("Context", (), {"cookies": lambda _self: []})()
        browser = type("Browser", (), {"page": page, "context": context, "proxy_server": None})()
        service = ePortalRegistrationApi({}, {}, [], browser)

        with self.assertLogs("registration_core", level="WARNING") as captured:
            result = service._browser_post("https://example.test/api", {"value": 1})

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"ok": True})
        self.assertEqual(len(page.calls), 1)
        self.assertIn("url.includes('/validateOTP') ? 1 : 2", page.calls[0][0])
        self.assertIn("Browser fetch recovered on retry", captured.output[0])

    def test_optional_first_name_is_encoded_as_empty_text(self):
        user = {
            "PAN": "ABCDE1234F",
            "LASTNAME": "Example",
            "FIRSTNAME": None,
            "MIDDLENAME": None,
            "DOB_YEAR": "1990",
            "DOB_MONTH": "JAN",
            "DOB_DAY": "1",
            "GENDER": "MALE",
            "RESIDENT": True,
            "MOBILE": "9999999999",
            "EMAIL": "person@example.test",
            "ADDRESS": "Example",
            "PIN": "110001",
            "PASSWORD": "your-password-here",
            "PERMSG": "Example",
        }
        service = ePortalRegistrationApi(user, {}, [])
        service.post = lambda *_args, **_kwargs: FakePortalResponse()
        service.get_districts = lambda *_args: True
        service.get_states = lambda *_args: True
        service.get_localities = lambda *_args: True
        service.get_post_offices = lambda *_args: True
        service.tot = 0

        self.assertTrue(service.step2_validate_details()["success"])
        self.assertEqual(service._step5_payload()["firstName"], "")


if __name__ == "__main__":
    unittest.main()
