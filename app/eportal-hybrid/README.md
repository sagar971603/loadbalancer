# ePortal Registration Production Bundle

This bundle contains the working registration flow verified with:

- final Step 5 success
- post-registration login success
- fixed `residentialStatusCd: "RES"`

## Files

```text
registration_orchestrator.py          Public service API for developer wrapper
registration_core.py                  Low-level ePortal registration engine
playwrite_login_with_session_cookie.py Browser session bootstrap helper
run_registration_cli.py               Manual CLI test runner
api_environment_diagnostics.py        Compare shell vs API runtime environment
ubuntu_curl_diagnostics.py            Curl/network diagnostic helper
requirements.txt                      Python dependencies
.env.example                          Transport env settings
sample_user_details.json              Dummy input template
```

Important spelling:

```text
playwrite_login_with_session_cookie.py
```

It is `playwrite`, not `playwright`.

## Install

Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip curl
python3 -m pip install -r requirements.txt
python3 -m playwright install firefox
python3 -m playwright install-deps firefox
```

Windows:

```powershell
py -m pip install -r requirements.txt
py -m playwright install firefox
```

## Transport

Default transport:

```bash
EP_PORTAL_HTTP_TRANSPORT=curl_cffi
```

Why:

- plain Python `requests` was rejected by the portal
- subprocess curl worked directly but failed under some API wrappers
- `curl_cffi` stays inside the Python process and impersonates browser TLS/HTTP behavior

Fallbacks:

```bash
export EP_PORTAL_HTTP_TRANSPORT=auto
export EP_PORTAL_HTTP_TRANSPORT=curl
```

Optional:

```bash
export EP_PORTAL_CURL_CFFI_IMPERSONATE=chrome120
export EP_PORTAL_CURL=/usr/bin/curl
```

## Developer Integration

Import:

```python
from registration_orchestrator import RegistrationOptions, register_individual
```

Implement OTP provider:

```python
class ApiOtpProvider:
    def get_aadhaar_otp(self, context):
        # Called only when Aadhaar OTP is required.
        # Return OTP from your DB/queue/websocket/API state.
        return aadhaar_otp

    def get_contact_otps(self, context):
        # Return mobile_otp, email_otp.
        return mobile_otp, email_otp
```

Run:

```python
result = register_individual(
    user_details,
    ApiOtpProvider(),
    RegistrationOptions(
        submit_final=True,
        verify_login=True,
    ),
)
```

Safe dry run:

```python
result = register_individual(
    user_details,
    ApiOtpProvider(),
    RegistrationOptions(submit_final=False),
)
```

Dry run completes through mobile/email OTP validation and returns masked Step 5 payload without submitting final registration.

## Required User Details

```json
{
  "PAN": "...",
  "FIRSTNAME": "...",
  "MIDDLENAME": "",
  "LASTNAME": "...",
  "DOB": "YYYY-MM-DD",
  "GENDER": "MALE or FEMALE",
  "RESIDENT": true,
  "ADDRESS": "...",
  "PIN": "...",
  "PASSWORD": "...",
  "CONFIRMPWD": "...",
  "PERMSG": "...",
  "EMAIL": "...",
  "MOBILE": "..."
}
```

Alternative DOB format:

```json
{
  "DOB_YEAR": "1969",
  "DOB_MONTH": "JUL",
  "DOB_DAY": "15"
}
```

## OTP Contexts

Aadhaar OTP callback receives:

```json
{
  "pan": "...",
  "transactionNo": "...",
  "reqId": "..."
}
```

Contact OTP callback receives:

```json
{
  "pan": "...",
  "reqId": "...",
  "mobTxn": "...",
  "emailTxn": "...",
  "mobile": "...",
  "email": "..."
}
```

## Manual CLI Test

Edit a copy of `sample_user_details.json`, then:

Dry run:

```bash
python3 run_registration_cli.py --user-json user_details.json
```

Real final submit:

```bash
python3 run_registration_cli.py --user-json user_details.json --submit-final --verify-login
```

## Critical Verified Fix

Wrong earlier value:

```json
"residentialStatusCd": true
```

Correct working value:

```json
"residentialStatusCd": "RES"
```

The core maps:

```text
RESIDENT=True  -> RES
RESIDENT=False -> NRI
```

## Diagnostics

If script works directly but fails under API:

```bash
python3 api_environment_diagnostics.py
```

Run it once directly and once inside the API process. Compare:

- Python executable
- PATH
- proxy environment variables
- `curl_cffi_available`
- transport variables

If curl fallback is used and failing:

```bash
python3 ubuntu_curl_diagnostics.py
```

## Success Criteria

Treat registration as complete only when:

1. Step 5 returns success.
2. Recommended: login verification succeeds.

SMS/email confirmation alone is not enough.
