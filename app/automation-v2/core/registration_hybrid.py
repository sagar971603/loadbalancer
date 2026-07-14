import requests
import json
import base64
import logging
import os
import time
import re
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from core.playwrite_login_with_session_cookie import EPortalLoginStealth
from http.cookies import SimpleCookie

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='eportal_registration.log',
    filemode='a'
)
logger = logging.getLogger(__name__)
FINAL_SUBMIT = False
CURL_BIN = os.getenv("EP_PORTAL_CURL") or shutil.which("curl") or shutil.which("curl.exe")
MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def normalize_dob(user_details):
    if user_details.get("DOB"):
        return user_details["DOB"]
    year = user_details.get("DOB_YEAR")
    month = user_details.get("DOB_MONTH")
    day = user_details.get("DOB_DAY")
    if not (year and month and day):
        return None
    month_num = MONTHS.get(str(month).strip().upper(), str(month).strip().zfill(2))
    return f"{year}-{month_num}-{str(day).zfill(2)}"


def normalize_residential_status(value):
    if value in {True, "true", "True", "1", "yes", "YES", "y", "Y"}:
        return "RES"
    if value in {False, "false", "False", "0", "no", "NO", "n", "N"}:
        return "NRI"
    return str(value).strip().upper() if value is not None else None


class _HeaderStore(dict):
    def __init__(self, pairs):
        super().__init__()
        self._all = {}
        for name, value in pairs:
            key = name.lower()
            self._all.setdefault(key, []).append(value)
            self[name] = value

    def get(self, name, default=None):
        return self._all.get(name.lower(), [default])[-1]

    def get_all(self, name):
        return self._all.get(name.lower(), [])


class _CurlResponse:
    def __init__(self, status_code, headers, text):
        self.status_code = status_code
        self.headers = headers
        self.text = text
        self.raw = type("Raw", (), {"headers": headers})()

    def json(self):
        return json.loads(self.text)


class ePortalRegistrationApi:
    """
    ePortal Forgot Password Service - FINAL VERSION

    Corrected email/mobile flow with:
    - mbTrans/eTrans transaction IDs in Step 2 & 3
    - "newCredential" field (not "pass") in Step 4
    - Proper error handling for EF00072, EF00073, EF00043, EF00239
    """

    def __init__(self,user_details: dict,headers: dict,cookies: list):
        self.pan = user_details.get("PAN") if user_details else None
        self.base_url = "https://eportal.incometax.gov.in"
        self.aadhar_validation=False

        #user details
        self.first_name = user_details.get("FIRSTNAME") if user_details else None
        self.last_name = user_details.get("LASTNAME") if user_details else None
        self.middle_name = user_details.get("MIDDLENAME") if user_details else None
        self.dob = normalize_dob(user_details) if user_details else None
        self.gender = user_details.get("GENDER") if user_details else None
        self.residential_status = normalize_residential_status(user_details.get("RESIDENT")) if user_details else None
        self.pin_code = user_details.get("PIN") if user_details else None
        self.address = user_details.get("ADDRESS") if user_details else None
        self.new_password = user_details.get("PASSWORD") if user_details else None
        self.personal_message = user_details.get("PERMSG") if user_details else None
        self.mobile = user_details.get("MOBILE") if user_details else None
        self.email = user_details.get("EMAIL") if user_details else None
        self.req_id = None
        self.transaction_no= None
        # Email/Mobile specific
        self.mb_trans = None
        self.e_trans = None
        self.role=None
        self.user_type=None
        # Aadhar specific
        self.autkn = None
        self.otp_source_flag = None
        self.aadhaarTxnId = None

        # Account info
        self.aadhar_linked = False
        self.otp_validated = False
        self.otp_method = None

        self.session = requests.Session()
        self.active_fillings=[]


        # Normalize headers to a plain dict (requests Headers or other mappings may be passed)
        if headers:
            try:
                clean_headers = {k: str(v) for k, v in dict(headers).items()}
                self.session.headers.update(clean_headers)
            except Exception:
                try:
                    self.session.headers.update(headers)
                except Exception:
                    pass
        for h in list(self.session.headers):
            if h.lower() in {
                "host",
                "sec-fetch-dest",
                "sec-fetch-mode",
                "sec-fetch-site",
                "sec-ch-ua",
                "sec-ch-ua-mobile",
                "sec-ch-ua-platform",
                "accept-encoding",
            }:
                self.session.headers.pop(h, None)

        for c in cookies:
            self.session.cookies.set(c["name"], c["value"])
        self.session.headers.pop("Cookie", None)

    def get(self, url):
        resp = self.session.get(url)
        # update cookies/headers from response
        try:
            self._update_cookies_from_response(resp)
        except Exception:
            pass
        return resp

    def post(self, url, json_data,sn=None):
        if sn is not None:
            self.session.headers.update({"sn": str(sn)})
        resp = self._curl_post(url, json_data)
        # update cookies/headers from response
        try:
            self._update_cookies_from_response(resp)
        except Exception:
            pass
        return resp

    def _curl_post(self, url, json_data):
        if not CURL_BIN:
            raise requests.RequestException("curl is required but was not found in PATH")

        cookie_header = "; ".join([f"{c.name}={c.value}" for c in self.session.cookies])
        headers = {
            "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept": self.session.headers.get("Accept", "application/json, text/plain, */*"),
            "Content-Type": "application/json",
            "Accept-Language": self.session.headers.get("Accept-Language", "en-IN,en;q=0.9"),
            "Origin": self.session.headers.get("Origin", "https://eportal.incometax.gov.in"),
            "Referer": self.session.headers.get("Referer", "https://eportal.incometax.gov.in/iec/foservices/"),
            "sn": self.session.headers.get("sn", ""),
        }
        if cookie_header:
            headers["Cookie"] = cookie_header

        cmd = [
            CURL_BIN,
            "-sS",
            "-i",
            "--compressed",
            "--http1.1",
            "--ipv4",
            "--tlsv1.2",
            "--connect-timeout",
            "15",
            "--max-time",
            "45",
            "--retry",
            "2",
            "--retry-delay",
            "1",
            "--retry-all-errors",
            "-X",
            "POST",
            url,
        ]
        for name, value in headers.items():
            if value:
                cmd.extend(["-H", f"{name}: {value}"])
        cmd.extend(["--data-raw", json.dumps(json_data, separators=(",", ":"))])

        result = None
        attempts = [cmd]
        attempts.append([part for part in cmd if part != "--ipv4"])
        attempts.append([part for part in cmd if part != "--tlsv1.2"])
        for attempt_cmd in attempts:
            for attempt in range(3):
                result = subprocess.run(attempt_cmd, capture_output=True, text=True)
                if result.returncode == 0 or result.stdout:
                    break
                if attempt < 2:
                    time.sleep(1)
            if result.returncode == 0 or result.stdout:
                break
        output = result.stdout or result.stderr
        if result.returncode != 0 and not result.stdout:
            raise requests.RequestException(output.strip() or f"curl exited {result.returncode}")

        normalized = output.replace("\r\n", "\n").strip()
        parts = normalized.split("\n\n")
        if len(parts) < 2:
            raise requests.RequestException(f"invalid curl response: {output[:200]}")

        body = parts[-1]
        header_block = next((part for part in reversed(parts[:-1]) if part.startswith("HTTP/")), parts[-2])
        header_lines = [line for line in header_block.split("\n") if line]
        status_line = next((line for line in header_lines if line.startswith("HTTP/")), "")
        status_code = int(status_line.split()[1]) if status_line else 0
        header_pairs = []
        for line in header_lines:
            if ":" in line:
                name, value = line.split(":", 1)
                header_pairs.append((name.strip(), value.strip()))
        response = _CurlResponse(status_code, _HeaderStore(header_pairs), body)
        response.raw_output = output
        return response


    def _update_cookies_from_response(self, response):
        """
        Robustly parse all Set-Cookie headers from the response and update the session's cookiejar and Cookie header.
        Mirrors the approach in eportal_login_enhanced.py for consistency and reliability.
        """
        if not response or not hasattr(response, 'headers'):
            return

        set_cookie_values = []

        # 1) Try to get all Set-Cookie values from the raw headers (urllib3/httplib expose get_all)
        try:
            raw = getattr(response, 'raw', None)
            if raw is not None:
                raw_headers = getattr(raw, 'headers', None)
                if raw_headers is not None and hasattr(raw_headers, 'get_all'):
                    set_cookie_values = raw_headers.get_all('Set-Cookie') or []
        except Exception:
            set_cookie_values = []

        # 2) Fallback to response.headers which may contain a single (possibly combined) header
        if not set_cookie_values:
            header_val = response.headers.get('Set-Cookie')
            if header_val:
                # Use regex to split combined Set-Cookie header into separate cookie strings.
                # This handles cases where cookies contain commas.
                set_cookie_values = re.split(r', (?=[^\s=]+=)', header_val)

        if not set_cookie_values:
            return

        # Parse and set cookies
        for sc in set_cookie_values:
            try:
                cookie = SimpleCookie()
                cookie.load(sc)
                for name, morsel in cookie.items():
                    value = morsel.value
                    domain = morsel['domain'] if morsel['domain'] else None
                    path = morsel['path'] if morsel['path'] else None
                    secure = True if morsel['secure'] else False
                    try:
                        if domain or path:
                            self.session.cookies.set(name, value, domain=domain, path=path, secure=secure)
                        else:
                            self.session.cookies.set(name, value)
                    except Exception:
                        self.session.cookies.set(name, value)
            except Exception:
                continue

        self.session.headers.pop("Cookie", None)




    def apply_external_session(self, cookies: list = None, headers: dict = None):
        """Apply cookies and headers from an external browser session (Playwright) to this requests.Session.

        cookies: list of cookie dicts as returned by Playwright's context.cookies(), each with
                 at minimum 'name' and 'value' and optionally 'domain' and 'path'.
        headers: dict of headers (will be merged into session.headers)
        """
        # Merge headers first so subsequent requests include browser-like headers
        if headers:
            try:
                # Only copy string values
                clean_headers = {k: str(v) for k, v in headers.items() if v is not None}
                self.session.headers.update(clean_headers)
            except Exception:
                pass

        # Merge cookies into the session cookie jar
        if cookies:
            for c in cookies:
                try:
                    name = c.get('name') if isinstance(c, dict) else None
                    value = c.get('value') if isinstance(c, dict) else None
                    domain = c.get('domain') if isinstance(c, dict) else None
                    path = c.get('path') if isinstance(c, dict) else None
                    if not name or value is None:
                        continue
                    # Use requests' cookiejar set method; include domain/path when available
                    if domain or path:
                        self.session.cookies.set(name, value, domain=domain, path=path)
                    else:
                        self.session.cookies.set(name, value)
                except Exception:
                    try:
                        # fallback simple set
                        self.session.cookies.set(c.get('name'), c.get('value'))
                    except Exception:
                        pass
        return True




    def step1_validate_pan(self):
        """STEP 1: Submit PAN"""
        try:
            self.tot=int(time.time() * 1000)
            logger.info("\n" + "="*100)
            logger.info("STEP 1: Validate PAN")
            logger.info("="*100)

            url = f"{self.base_url}/iec/registrationapi/saveEntity"
            headers = self.session.headers.copy()
            headers['sn'] = 'checkPanDetailsService'
            success=False

            payload = {
                "userId": self.pan,
                "serviceName": "checkPanDetailsService"
            }

            response = self.post(url,json_data=payload,sn='checkPanDetailsService')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            data=response.json()

            # print(json.dumps(data, indent=2))

            logger.info(f"\n📋 Messages:")
            messages = data.get('messages', [])
            for msg in messages:
                code = msg.get('code')
                msg_type = msg.get('type')
                desc = msg.get('desc')

                if code == "EF01227" and response.status_code == 200:
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
                    self.req_id=data.get("reqId")
                    self.aadhar_validation=True

                if code == "EF00000" and response.status_code == 200 or (code == "EF00049" and response.status_code == 200):
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
                    success=True

                else:
                    if msg_type == "ERROR":
                        logger.error(f"   ✗ [{msg_type}] {code}: {desc}")
                        return {'success': False, 'error': 'pan_validation_failed', 'message': desc, 'step': 1}
                    else:
                        logger.info(f"   → [{msg_type}] {code}: {desc}")

            if success:
                return {'success': True, 'aadhar_validation': self.aadhar_validation}

        except Exception as e:
            logger.error(f"Step 1 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 1}




    def aadhar_validator(self):

        """STEP 1A: Aadhar Validation - Extracts autkn"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 1A: Aadhar Validation")
            logger.info("="*100)

            url = f"{self.base_url}/iec/registrationapi/saveEntity"
            headers = self.session.headers.copy()
            headers['sn'] = 'panAadhaarVerification'

            payload = {
                "serviceName": "panAadhaarVerification",
                "userId": self.pan
            }

            response = self.post(url, json_data=payload, sn='panAadhaarVerification')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 1}

            data = response.json()
            logger.info(f"Response: {json.dumps(data, indent=2)[:300]}")
            messages = data.get('messages', [])
            logger.info(f"\n📋 Messages:")
            for msg in messages:
                code = msg.get('code')
                msg_type = msg.get('type')
                desc = msg.get('desc')

                if desc == "SUCCESS" and response.status_code == 200:
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
                    self.transaction_no=data.get("transactionNo")
                    return {'success': True, 'error': None, 'step': 1}

                else:
                    if msg_type == "ERROR":
                        logger.error(f"   ✗ [{msg_type}] {code}: {desc}")
                        return {'success': False, 'error': 'aadhar_validation_failed', 'message': desc, 'step': 1}
        except Exception as e:
            logger.error(f"Step 1A Error: {e}")
            return {'success': False, 'error': str(e), 'step': 1}




    def step2_validate_details(self):
        """STEP 2: Request Aadhar OTP - Extracts autkn"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 2: Validate User Details")
            logger.info("="*100)



            url = f"{self.base_url}/iec/registrationapi/saveEntity"
            headers = self.session.headers.copy()
            headers['sn'] = 'indivRegistrationService'

            payload = {
                "dateOfBirth": self.dob,
                "firstName": base64.b64encode(self.first_name.encode()).decode(),
                "isTrue": True,
                "lastName": base64.b64encode(self.last_name.encode()).decode(),
                "midName": base64.b64encode(self.middle_name.encode()).decode() if self.middle_name else "",
                "residentialStatusCd": self.residential_status,
                "serviceName": "indivRegistrationService",
                "userGender": "M" if self.gender.lower() == "male" else "F",
                "userId": self.pan
            }
            # print(payload)

            response = self.post(url, json_data=payload, sn='indivRegistrationService')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 2}

            data = response.json()

            messages = data.get('messages', [])
            logger.info(f"\n📋 Messages:")
            for msg in messages:
                code = msg.get('code')
                msg_type = msg.get('type')
                desc = msg.get('desc')

                if code == "EF00000" and response.status_code == 200 or (code == "EF00049" and response.status_code == 200):
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
                    result=self.get_districts(self.pin_code)
                    self.req_id=data.get("reqId")
                    if not result:
                        logger.error("No districts found for the given PIN code.")
                        return {'success': False, 'error': 'no_districts_found', 'step': 2}

                    result=self.get_states(self.pin_code)
                    if not result:
                        logger.error("No states found for the given PIN code.")
                        return {'success': False, 'error': 'no_states_found', 'step': 2}

                    result=self.get_localities(self.pin_code)
                    if not result:
                        logger.error("No localities found for the given PIN code.")
                        return {'success': False, 'error': 'no_localities_found', 'step': 2}
                    result=self.get_post_offices(self.pin_code)
                    if not result:
                        logger.error("No post offices found for the given PIN code.")
                        return {'success': False, 'error': 'no_post_offices_found', 'step': 2}
                    return {'success': True}
                else:
                    if msg_type == "ERROR":
                        logger.error(f"   ✗ [{msg_type}] {code}: {desc}")
                        return {'success': False, 'error': 'pan_validation_failed', 'message': desc, 'step': 1}
                    else:
                        logger.info(f"   → [{msg_type}] {code}: {desc}")

        except Exception as e:
            logger.error(f"Step 2 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 2}

    def step3_validate_contact(self):
        """STEP 3: Validate Contact Details"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 3: Validate Contact Details")
            logger.info("="*100)

            url = f"{self.base_url}/iec/registrationapi/saveEntity"
            headers = self.session.headers.copy()
            headers['sn'] = 'IndivRegistrationService'
            payload = {
                "serviceName": "indivRegistrationService",
                "reqId": self.req_id,
                "priMobileNum": self.mobile,
                "isdCd": "91",
                "priMobBelongsTo": 1,
                "priEmailRelationId": 1,
                "priEmailId": self.email,
                "addrLine1Txt": self.address,
                "addrLine2Txt": "",
                "addrLine3Txt": self.post_office_name,
                "addrLine4Txt": self.locality,
                "addrLine5Txt": self.district,
                "pinCd": self.pin_code,
                "zipCd": None,
                "countryCd": 91,
                "landlineNo": None,
                "stateCd": self.state_code,
                "foreignStateDesc": None,
                "isTrue": False
            }

            response = self.post(url, json_data=payload, sn='indivRegistrationService')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            logger.info(f"Status: {response.status_code}")


            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 3}

            data = response.json()
            messages = data.get('messages', [])
            logger.info(f"\n📋 Messages:")
            for msg in messages:
                code = msg.get('code')
                msg_type = msg.get('type')
                desc = msg.get('desc')

                if code == "EF00000" and response.status_code == 200:
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
                    print(f"   ✓ [{msg_type}] {code}: {desc}")
                    self.mob_txn=data.get("mobTxn")
                    self.email_txn=data.get("emailTxn")
                    return {'success': True}
                else:
                    if msg_type == "ERROR":
                        logger.error(f"   ✗ [{msg_type}] {code}: {desc}")
                        return {'success': False, 'error': 'contact_validation_failed', 'message': desc, 'step': 3}
                    else:
                        logger.info(f"   → [{msg_type}] {code}: {desc}")

        except Exception as e:
            logger.error(f"Step 3 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 3}


    def step4_validate_otp(self,otp=None,email_otp=None,panadhar=False):
        """STEP 4: Validate OTP"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 4: Validate OTP")
            logger.info("="*100)

            url = f"{self.base_url}/iec/registrationapi/validateOTP"
            headers = self.session.headers.copy()
            headers['sn'] = 'indivRegistrationService' if not panadhar else 'panAadhaarVerification'

            if not panadhar:
                payload = {
                "serviceName": "indivRegistrationService",
                "reqId": self.req_id,
                "mobTxn": self.mob_txn,
                "emailTxn": self.email_txn,
                "mobileOtp": otp,
                "emailOtp": email_otp,
                "isTrue": True
            }


            else:
                payload = {
                    "serviceName": "panAadhaarVerification",
                    "userId": self.pan,
                    "transactionNo": self.transaction_no,
                    "mobileOtp": otp
                }

            response = self.post(url, json_data=payload, sn='indivRegistrationService' if not panadhar else 'panAadhaarVerification')


            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 4}

            data = response.json()
            logger.info(f"Response: {json.dumps(data, indent=2)[:300]}")
            messages = data.get('messages', [])
            logger.info(f"\n📋 Messages:")
            for msg in messages:
                code = msg.get('code')
                msg_type = msg.get('type')
                desc = msg.get('desc')


                if panadhar and code == "status" and response.status_code == 200:
                    if desc == "SUCCESS":
                        logger.info(f"   [{msg_type}] {code}: {desc}")
                        self.aadhaarTxnId = data.get("aadhaarTxnId")
                        return {'success': True, 'error': None, 'step': 4}
                    return {'success': False, 'error': 'otp_validation_failed', 'message': desc, 'step': 4}

                if code in ["EF00000","EF00015","status"] and response.status_code == 200:
                    logger.info(f"   [{msg_type}] {code}: {desc}")
                    return {'success': True, 'error': None, 'step': 4}

                else:
                    if msg_type == "ERROR":
                        logger.error(f"   ✗ [{msg_type}] {code}: {desc}")
                        return {'success': False, 'error': 'otp_validation_failed', 'message': desc, 'step': 4}
                    else:

                        logger.info(f"   → [{msg_type}] {code}: {desc}")
                        return {'success': False, 'error': 'otp_validation_incomplete', 'message': desc, 'step': 4}

        except Exception as e:
            logger.error(f"Step 4 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 4}

    def _step5_payload(self):
        return {
            "isTrue": False,
            "serviceName": "indivRegistrationService",
            "reqId": self.req_id,
            "totRegTime": int(self.tot),
            "firstName": base64.b64encode(self.first_name.encode()).decode(),
            "lastName": base64.b64encode(self.last_name.encode()).decode(),
            "midName": base64.b64encode(self.middle_name.encode()).decode() if self.middle_name is not None else "",
            "cred": base64.b64encode(self.new_password.encode()).decode(),
            "secAccessMsg": self.personal_message,
        }

    def _parse_step5_result(self, status_code, text):
        if status_code != 200:
            return {
                'success': False,
                'error': 'request_failed',
                'status_code': status_code,
                'raw_text': text[:1000],
                'step': 5
            }

        data = json.loads(text)
        messages = data.get('messages', [])
        logger.info("\nMessages:")
        for msg in messages:
            code = msg.get('code')
            msg_type = msg.get('type')
            desc = msg.get('desc')

            if code == "EF00000":
                logger.info(f"   [{msg_type}] {code}: {desc}")
                return {'success': True, 'message': desc, 'step': 5}

            if msg_type == "ERROR":
                logger.error(f"   [{msg_type}] {code}: {desc}")
                return {'success': False, 'error': 'password_set_failed', 'message': desc, 'code': code, 'step': 5}

            return {'success': False, 'error': 'password_set_incomplete', 'message': desc, 'code': code, 'step': 5}

        return {'success': False, 'error': 'no_messages', 'step': 5}

    def _sync_cookies_to_browser(self, browser_obj):
        cookies = []
        for c in self.session.cookies:
            cookies.append({
                "name": c.name,
                "value": c.value,
                "url": self.base_url,
                "secure": True,
            })
        if cookies:
            browser_obj.context.add_cookies(cookies)

    def step5_set_new_password_browser(self, browser_obj):
        """STEP 5: Set password using the live browser context."""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 5: Set New Password via browser")
            logger.info("="*100)

            countries = self.get_countries()
            if not countries:
                logger.error("Failed to fetch countries.")
                return {'success': False, 'error': 'failed_to_fetch_countries', 'step': 5}

            payload = self._step5_payload()
            self._sync_cookies_to_browser(browser_obj)

            result = browser_obj.page.evaluate(
                """
async ({payload}) => {
  const res = await fetch('https://eportal.incometax.gov.in/iec/registrationapi/validateOTP', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json, text/plain, */*',
      'sn': 'indivRegistrationService'
    },
    body: JSON.stringify(payload),
    credentials: 'include'
  });
  const text = await res.text();
  return {status: res.status, text};
}
""",
                {"payload": payload},
            )

            try:
                Path(__file__).with_name("step5_last_response.txt").write_text(
                    result.get("text", ""),
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception:
                pass

            logger.info(f"Status: {result['status']}")
            return self._parse_step5_result(result["status"], result.get("text", ""))

        except Exception as e:
            logger.error(f"Step 5 Browser Error: {e}")
            return {'success': False, 'error': str(e), 'step': 5}

    def step5_set_new_password(self):
        """STEP 5: Set New Password"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 5: Set New Password")
            logger.info("="*100)

            countries = self.get_countries()
            if not countries:
                logger.error("Failed to fetch countries.")
                return {'success': False, 'error': 'failed_to_fetch_countries', 'step': 5}

            url = f"{self.base_url}/iec/registrationapi/validateOTP"
            headers = self.session.headers.copy()
            headers['sn'] = 'indivRegistrationService'

            # print(f"Headers: {self.session.headers}")

            payload = self._step5_payload()

            # print(json.dumps(payload, indent=2))




            response = self.post(url,json_data=payload,sn='indivRegistrationService')
            try:
                Path(__file__).with_name("step5_last_response.txt").write_text(
                    getattr(response, "raw_output", getattr(response, "text", "")),
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception:
                pass
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {
                    'success': False,
                    'error': 'request_failed',
                    'status_code': response.status_code,
                    'raw_text': getattr(response, 'text', '')[:1000],
                    'step': 5
                }


            # print(response.text())

            data = response.json()
            messages = data.get('messages', [])
            logger.info(f"\n📋 Messages:")
            for msg in messages:
                code = msg.get('code')
                msg_type = msg.get('type')
                desc = msg.get('desc')

                if code == "EF00000" and response.status_code == 200:
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
                    return {'success': True}
                else:
                    if msg_type == "ERROR":
                        logger.error(f"   ✗ [{msg_type}] {code}: {desc}")
                        return {'success': False, 'error': 'password_set_failed', 'message': desc, 'step': 5}
                    else:

                        return {'success': False, 'error': 'password_set_incomplete', 'message': desc, 'step': 5}
                        logger.info(f"   → [{msg_type}] {code}: {desc}")

        except Exception as e:
            logger.error(f"Step 5 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 5}


    def get_post_offices(self, pin):
        """Fetch post offices for a given PIN code"""
        try:
            logger.info("\n" + "="*100)
            logger.info(f"Fetching Post Offices for PIN: {pin}")
            logger.info("="*100)

            headers = self.session.headers.copy()
            headers['sn'] = 'post_office'

            payload = {
                "tokenName": "post_office",
                "requiredColumns": [
                    "post_office_cd",
                    "post_office_name"
                ],
                "orderBy": [
                    ["post_office_name", "asc"]
                ],
                "distinctColumnName": "post_office_cd",
                "includeTokenName": "pin_cd",
                "includeDependentField": {
                "pin_cd": self.pin_code
                }
            }


            url = f"{self.base_url}/iec/master/getDetails/join"
            response = self.post(url, json_data=payload, sn='post_office')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            # print(response.text)
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                logger.error("Failed to fetch post offices")
                return []

            data = response.json()
            post_offices = data.get('data', [])
            if post_offices:
                self.post_office_name = post_offices[0]['post_office_name']
                logger.info(f"Selected post office: {self.post_office_name}")
                return post_offices


        except Exception as e:
            logger.error(f"Error fetching post offices: {e}")
            return []


    def get_districts(self,pin_code):
        """Fetch districts for a given state code"""
        try:
            logger.info("\n" + "="*100)
            logger.info(f"Fetching Districts for State Code: {pin_code}")
            logger.info("="*100)

            headers = self.session.headers.copy()
            headers['sn'] = 'district'

            payload = {
                "tokenName": "district",
                "requiredColumns": [
                    "district_cd",
                    "district_desc"
                ],
                "orderBy": [
                    ["district_desc", "asc"]
                ],
                "distinctColumnName": "district_cd",
                "includeTokenName": "pin_cd",
                "includeDependentField": {
                    "pin_cd": self.pin_code
                }
            }

            url = f"{self.base_url}/iec/master/getDetails/join"
            response = self.post(url, json_data=payload, sn='district')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                logger.error("Failed to fetch districts")
                return []

            data = response.json()
            districts = data.get('data', [])
            self.district = districts[0]['district_desc'] if districts else None
            return districts

        except Exception as e:
            logger.error(f"Error fetching districts: {e}")
            return []


    def get_localities(self,pin_code):
        try:
            logger.info("\n" + "="*100)
            logger.info(f"Fetching Localities for PIN Code: {pin_code}")
            logger.info("="*100)

            headers = self.session.headers.copy()
            headers['sn'] = 'locality'

            payload = {
                "tokenName": "locality",
                "requiredColumns": [
                    "locality_cd",
                    "locality_name"
                ],
                "orderBy": [
                    ["locality_name", "asc"]
                ],
                "distinctColumnName": "locality_cd",
                "includeTokenName": "pin_cd",
                "includeDependentField": {
                    "pin_cd": self.pin_code
                }
            }

            url = f"{self.base_url}/iec/master/getDetails/join"
            response = self.post(url, json_data=payload, sn='locality')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                logger.error("Failed to fetch localities")
                return []

            data = response.json()
            localities = data.get('data', [])
            self.locality = localities[0]['locality_name'] if localities else None
            return localities
        except Exception as e:
            logger.error(f"Error fetching localities: {e}")
            return []

    def get_states(self,pin_code):
        """Fetch all states"""
        try:
            logger.info("\n" + "="*100)
            logger.info("Fetching States")
            logger.info("="*100)

            headers = self.session.headers.copy()
            headers['sn'] = 'state'
            payload = {
                "tokenName": "state",
                "requiredColumns": [
                    "state_cd",
                    "state_desc"
                ],
                "orderBy": [
                    ["state_desc", "asc"]
                ],
                "distinctColumnName": "state_cd",
                "includeTokenName": "pin_cd",
                "includeDependentField": {
                    "pin_cd": self.pin_code
                }
            }

            url = f"{self.base_url}/iec/master/getDetails/join"
            response = self.post(url, json_data=payload, sn='state')
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                logger.error("Failed to fetch states")
                return []

            data = response.json()
            states = data.get('data', [])
            self.state_code= states[0]['state_cd'] if states else None
            return states

        except Exception as e:
            logger.error(f"Error fetching states: {e}")
            return []

    def get_countries(self):
        """Fetch all countries"""
        try:
            logger.info("\n" + "="*100)
            logger.info("Fetching Countries")
            logger.info("="*100)

            headers = self.session.headers.copy()
            headers['sn'] = 'country'

            payload = {
                "tokenName": "country",
                "requiredColumns": [
                    "country_name",
                    "country_cd"
                ]
            }



            url = f"{self.base_url}/iec/master/getDetails"
            response = self.post(url, json_data=payload, sn='country')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                logger.error("Failed to fetch countries")
                return []

            data = response.json()
            countries = data.get('data', [])
            return countries

        except Exception as e:
            logger.error(f"Error fetching countries: {e}")
            return []


# if __name__ == "__main__":
#     print("\n" + "="*100)
#     print("ePortal Register Portal ")
#     print("="*100)

#     # Example user details (well-structured)
#     user_details = {
#         "PAN": "ABCDE1234F",
#         "FIRSTNAME": "",
#         "MIDDLENAME": "",
#         "LASTNAME": "SANTHOSH",
#         "DOB_YEAR": "1991",
#         "DOB_MONTH": "MAY",
#         "DOB_DAY": "20",
#         "GENDER": "MALE",
#         "RESIDENT": True,
#         "ADDRESS": "Chack saugahna post parsedi dist bahraich uttar pradesh 271903",
#         "PIN": "271903",
#         "PASSWORD": "your-password-here",
#         "CONFIRMPWD": "your-password-here",
#         "PERMSG": "easyreturn.co.in",
#         "EMAIL": "pwpattayalisting@gmail.com",     # required by step3_validate_contact
#         "MOBILE": "9541616388"           # required by step3_validate_contact
#     }

#     # Load cookies and headers from a file (obtained via Playwright)


#     browser_obj = EPortalLoginStealth(userData=user_details)
#     data = browser_obj.open_register()


#     service = ePortalRegistrationApi(user_details,data['headers'],data['cookies'])



#     result = service.step1_validate_pan()

#     print("Step 1 Result:", result)

#     if not result['success']:
#         print(f"❌ Step 1 Failed: {result['error']}")
#         exit(1)

#     print("✅ Step 1 Successful: PAN Validated")

#     if result.get('aadhar_validation') == True:
#         aadhar_result=service.aadhar_validator()
#         if not aadhar_result['success']:
#             print(f"❌ Aadhar Validation Failed: {aadhar_result['error']}: {aadhar_result.get('message')}")
#             exit(1)

#         otp = input("Enter the aadhar OTP:")

#         validated_aadhar=service.step4_validate_otp(otp=otp,panadhar=True)

#         if not validated_aadhar['success']:
#             print(f"❌ Aadhar OTP Validation Failed: {validated_aadhar['error']}")
#             exit(1)
#         print("✅ Aadhar Validation Successful")

#     result=service.step2_validate_details()

#     if not result['success']:
#         print(f"❌ Step 2 Failed: {result['error']}")
#         exit(1)

#     result=service.step3_validate_contact()

#     if not result['success']:
#         print(f"❌ Step 3 Failed: {result['error']}")
#         exit(1)


#     otp=input("Enter the mobile OTP:")
#     email_otp=input("Enter the email OTP:")

#     result=service.step4_validate_otp(otp=otp,email_otp=email_otp)

#     print("OTP Validation Result:", result)

#     if not result['success']:
#         print(f"❌ Step 4 Failed: {result['error']}")
#         exit(1)

#     if not FINAL_SUBMIT:
#         masked_payload = {
#             "isTrue": False,
#             "serviceName": "indivRegistrationService",
#             "reqId": service.req_id,
#             "totRegTime": int(service.tot),
#             "firstName": base64.b64encode(service.first_name.encode()).decode(),
#             "lastName": base64.b64encode(service.last_name.encode()).decode(),
#             "midName": base64.b64encode(service.middle_name.encode()).decode() if service.middle_name is not None else "",
#             "cred": "***MASKED***",
#             "secAccessMsg": service.personal_message,
#         }
#         print("DRY RUN: final Step 5 was not submitted.")
#         print(json.dumps(masked_payload, indent=2))
#         exit(0)

#     result=service.step5_set_new_password_browser(browser_obj)

#     if not result['success']:
#         print(f"❌ Step 5 Failed: {result['error']}")
#         exit(1)
