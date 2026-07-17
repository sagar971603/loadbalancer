"""
Framework-neutral API wrapper example.

Your real API should replace the in-memory store with DB/Redis/job state.
This file is intentionally not a running web server; it shows the integration pattern.
"""

from registration_orchestrator import RegistrationOptions, register_individual


class StoredOtpProvider:
    def __init__(self, otp_store, attempt_id):
        self.otp_store = otp_store
        self.attempt_id = attempt_id

    def get_aadhaar_otp(self, context):
        self.otp_store[self.attempt_id]["aadhaar_context"] = context
        return self.otp_store[self.attempt_id]["aadhaar_otp"]

    def get_contact_otps(self, context):
        self.otp_store[self.attempt_id]["contact_context"] = context
        return (
            self.otp_store[self.attempt_id]["mobile_otp"],
            self.otp_store[self.attempt_id]["email_otp"],
        )


def run_registration_job(attempt_id, user_details, otp_store, submit_final=False):
    return register_individual(
        user_details,
        StoredOtpProvider(otp_store, attempt_id),
        RegistrationOptions(
            submit_final=submit_final,
            verify_login=submit_final,
        ),
    )
