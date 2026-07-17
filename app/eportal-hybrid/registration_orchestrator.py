import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, Tuple


BASE_DIR = Path(__file__).resolve().parent
CORE_SCRIPT = BASE_DIR / "registration_core.py"
sys.path.insert(0, str(BASE_DIR))

spec = importlib.util.spec_from_file_location("registration_core", CORE_SCRIPT)
registration_core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = registration_core
spec.loader.exec_module(registration_core)

from playwrite_login_with_session_cookie import EPortalLoginStealth  # noqa: E402


logger = logging.getLogger(__name__)


class OtpProvider(Protocol):
    def get_aadhaar_otp(self, context: Dict[str, Any]) -> str:
        ...

    def get_contact_otps(self, context: Dict[str, Any]) -> Tuple[str, str]:
        ...


@dataclass
class RegistrationOptions:
    submit_final: bool = False
    browser_type: str = "firefox"
    mask_payload: bool = True
    verify_login: bool = False


def validate_user_details(user: Dict[str, Any]) -> None:
    required = [
        "PAN",
        "FIRSTNAME",
        "LASTNAME",
        "GENDER",
        "RESIDENT",
        "ADDRESS",
        "PIN",
        "PASSWORD",
        "PERMSG",
        "EMAIL",
        "MOBILE",
    ]
    missing = [key for key in required if user.get(key) in (None, "")]
    if missing:
        raise ValueError(f"Missing required user fields: {', '.join(missing)}")

    if not (user.get("DOB") or (user.get("DOB_YEAR") and user.get("DOB_MONTH") and user.get("DOB_DAY"))):
        raise ValueError("DOB is required as DOB or DOB_YEAR/DOB_MONTH/DOB_DAY")


def masked_step5_payload(service: Any) -> Dict[str, Any]:
    payload = service._step5_payload()
    payload["cred"] = "***MASKED***"
    return payload


def _record_step(history: list, name: str, result: Dict[str, Any]) -> None:
    history.append({"step": name, "result": result})


def _fail(name: str, result: Dict[str, Any], history: list) -> Dict[str, Any]:
    _record_step(history, name, result)
    return {
        "success": False,
        "failed_step": name,
        "error": result.get("error"),
        "message": result.get("message"),
        "code": result.get("code"),
        "history": history,
    }


def verify_login(user_details: Dict[str, Any], browser_type: str = "firefox") -> Dict[str, Any]:
    login_result = EPortalLoginStealth(
        userData={"PAN": user_details["PAN"], "PASSWORD": user_details["PASSWORD"]},
        browser_type=browser_type,
    ).login()
    return {
        "success": bool(login_result.get("success")),
        "error": login_result.get("error"),
        "cookie_count": len(login_result.get("cookies", [])) if login_result.get("success") else 0,
    }


def register_individual(
    user_details: Dict[str, Any],
    otp_provider: OtpProvider,
    options: Optional[RegistrationOptions] = None,
    on_step: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    options = options or RegistrationOptions()
    validate_user_details(user_details)
    history = []

    browser_obj = EPortalLoginStealth(
        userData={"PAN": user_details["PAN"]},
        browser_type=options.browser_type,
    )
    session_bootstrap = browser_obj.open_register()
    if not session_bootstrap.get("success"):
        browser_obj.close()
        return {
            "success": False,
            "failed_step": "open_register",
            "error": session_bootstrap.get("error"),
            "history": history,
        }

    service = registration_core.ePortalRegistrationApi(
        user_details,
        session_bootstrap["headers"],
        session_bootstrap["cookies"],
        browser_obj=browser_obj,
    )

    def run_step(name: str, fn: Callable[[], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        result = fn()
        _record_step(history, name, result)
        if on_step:
            on_step(name, result)
        if not result.get("success"):
            return _fail(name, result, history)
        return None

    failure = run_step("step1_validate_pan", service.step1_validate_pan)
    if failure:
        return failure

    if history[-1]["result"].get("aadhar_validation"):
        failure = run_step("aadhar_send_otp", service.aadhar_validator)
        if failure:
            return failure

        aadhaar_otp = otp_provider.get_aadhaar_otp(
            {
                "pan": service.pan,
                "transactionNo": service.transaction_no,
                "reqId": service.req_id,
            }
        )
        failure = run_step(
            "aadhar_validate_otp",
            lambda: service.step4_validate_otp(otp=aadhaar_otp, panadhar=True),
        )
        if failure:
            return failure

    failure = run_step("step2_validate_details", service.step2_validate_details)
    if failure:
        return failure

    failure = run_step("step3_send_contact_otps", service.step3_validate_contact)
    if failure:
        return failure

    mobile_otp, email_otp = otp_provider.get_contact_otps(
        {
            "pan": service.pan,
            "reqId": service.req_id,
            "mobTxn": service.mob_txn,
            "emailTxn": service.email_txn,
            "mobile": service.mobile,
            "email": service.email,
        }
    )
    failure = run_step(
        "step4_validate_contact_otps",
        lambda: service.step4_validate_otp(otp=mobile_otp, email_otp=email_otp),
    )
    if failure:
        return failure

    if not options.submit_final:
        return {
            "success": True,
            "dry_run": True,
            "reqId": service.req_id,
            "message": "Final Step 5 was not submitted. Set submit_final=True to submit.",
            "step5_payload": masked_step5_payload(service) if options.mask_payload else service._step5_payload(),
            "history": history,
        }

    final_result = service.step5_set_new_password()
    _record_step(history, "step5_set_password", final_result)
    if on_step:
        on_step("step5_set_password", final_result)
    if not final_result.get("success"):
        return _fail("step5_set_password", final_result, history)

    result = {
        "success": True,
        "reqId": service.req_id,
        "message": final_result.get("message", "Registration completed."),
        "history": history,
    }

    if options.verify_login:
        result["login_verification"] = verify_login(user_details, options.browser_type)

    return result
