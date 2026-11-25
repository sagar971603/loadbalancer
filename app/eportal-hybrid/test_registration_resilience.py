import asyncio
import json
import threading
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
import requests
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

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
    def __init__(self, outcomes=()):
        self.calls = []
        self.outcomes = iter(outcomes)
        self.listener = None

    def on(self, _event, listener):
        self.listener = listener

    def remove_listener(self, _event, _listener):
        self.listener = None

    def evaluate(self, script, arguments):
        self.calls.append((script, arguments))
        result, failure = next(self.outcomes, ({
            "status": 200,
            "headers": [["content-type", "application/json"]],
            "text": '{"ok":true}',
        }, None))
        if failure:
            self.listener(SimpleNamespace(
                url=arguments["url"], method="POST", post_data=arguments["payload"],
                failure=failure, redirected_from=None,
            ))
        return result


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

    def test_final_otp_accepts_paste_whitespace_and_keeps_leading_zero(self):
        request = api.FinalOTPRequest(
            session_id="rg_test_session", mobile_otp=" 012345 ", email_otp="654321\n"
        )
        self.assertEqual(request.mobile_otp, "012345")
        self.assertEqual(request.email_otp, "654321")

    def test_validation_response_and_log_omit_rejected_otp(self):
        body = {"session_id": "rg_test_session", "mobile_otp": "123456", "email_otp": "ABCDEF"}
        with self.assertRaises(ValidationError) as exc:
            api.FinalOTPRequest(**body)
        error = RequestValidationError(exc.exception.errors(), body=body)
        request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/v1/registration/verify-final-otp"))
        with self.assertLogs("regmainhybrid", level="WARNING") as logged:
            response = asyncio.run(api.validation_exception_handler(request, error))
        data = json.loads(response.body)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(data["error"], "validation_error")
        self.assertEqual(data["message"], "Enter the six-digit Email OTP exactly as received.")
        self.assertEqual(data["data"]["errors"][0]["loc"], ["email_otp"])
        self.assertEqual(data["data"]["errors"][0]["type"], "string_pattern_mismatch")
        self.assertNotIn("ABCDEF", response.body.decode())
        self.assertNotIn("ABCDEF", " ".join(logged.output))

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

    def test_browser_transport_uses_bounded_backoff(self):
        page = FakePage([({"failed": "before_headers"}, "NS_ERROR_CONNECTION_REFUSED")])
        context = type("Context", (), {"cookies": lambda _self: []})()
        browser = type("Browser", (), {"page": page, "context": context, "proxy_server": None})()
        service = ePortalRegistrationApi({}, {}, [], browser)

        with patch("registration_core.pace_portal_request") as pace, self.assertLogs(
            "registration_core", level="WARNING"
        ) as captured:
            result = service._browser_post("https://example.test/api", {"value": 1})

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"ok": True})
        self.assertEqual(len(page.calls), 2)
        self.assertEqual(pace.call_count, 2)
        self.assertIn("Browser fetch recovered on retry", captured.output[-1])
        self.assertIsNone(page.listener)

    def test_contact_request_is_not_resent_when_outcome_is_uncertain(self):
        for phase in ("before_headers", "response_body"):
            with self.subTest(phase=phase):
                page = FakePage([({"failed": phase}, "NS_ERROR_NET_RESET")])
                context = SimpleNamespace(cookies=lambda: [])
                browser = SimpleNamespace(page=page, context=context, proxy_server=None)
                service = ePortalRegistrationApi({}, {"sn": "indivRegistrationService"}, [], browser)
                with patch("registration_core.pace_portal_request"), self.assertRaisesRegex(
                    requests.ConnectionError, "last step could not be confirmed"
                ):
                    service._browser_post("https://example.test/api", {"isTrue": False})
                self.assertEqual(len(page.calls), 1)
                self.assertIsNone(page.listener)

    def test_browser_transport_lock_is_shared_per_proxy(self):
        browser_a = type("Browser", (), {"proxy_server": "http://127.0.0.1:18889"})()
        browser_b = type("Browser", (), {"proxy_server": "http://127.0.0.1:18889"})()
        browser_c = type("Browser", (), {"proxy_server": "http://127.0.0.1:18888"})()

        service_a = ePortalRegistrationApi({}, {}, [], browser_a)
        service_b = ePortalRegistrationApi({}, {}, [], browser_b)
        service_c = ePortalRegistrationApi({}, {}, [], browser_c)

        self.assertIs(service_a.portal_request_lock, service_b.portal_request_lock)
        self.assertIsNot(service_a.portal_request_lock, service_c.portal_request_lock)

    def test_browser_worker_retains_request_log_context(self):
        class Worker(api.PortalServiceWorker):
            def _bootstrap(self):
                return api.REQUEST_ID.get()

        worker = Worker({})
        token = api.REQUEST_ID.set("registration-request-test")
        try:
            self.assertEqual(worker.bootstrap(), "registration-request-test")
        finally:
            api.REQUEST_ID.reset(token)
            worker.close()

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
