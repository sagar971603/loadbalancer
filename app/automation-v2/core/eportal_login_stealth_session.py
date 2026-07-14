"""
ePortal Login - STEALTH SESSION VERSION
✅ Automatic cookie handling
✅ No manual cookie injection
✅ Anti-bot detection
✅ Production ready
"""
import logging
import json
import base64
import time
# utilities
import math
import traceback
import re
from http.cookies import SimpleCookie
# curl_cffi may be installed and provides its own exceptions; prefer its ConnectionError

logging.basicConfig(
    filename='eportal_stealth_session.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
import requests




class ePortalLoginStealth:
    """
    ePortal Login - STEALTH SESSION VERSION

    ✅ Automatic cookie handling
    ✅ No manual extraction
    ✅ Anti-detection headers
    ✅ Production ready
    """

    def __init__(self, user):
        self.pan = user["PAN"]
        self.password = user["PASSWORD"]
        self.base_url = "https://eportal.incometax.gov.in"

        # Use requests.Session for HTTP calls and cookie handling
        self.session = requests.Session()

        self.active_fillings = []

        # Step 1 response fields
        self.req_id = None
        self.entity = None
        self.entity_type = None
        self.role = None
        self.email = None
        self.client_ip = None
        self.mobile_no = None
        self.uid_valdtn_flg = None
        self.aadhaar_mobile_validated = None
        self.sec_accs_msg = None
        self.sec_login_options = None
        self.dto_service = None
        self.exempted_pan = None
        self.user_consent = None
        self.img_byte = None
        self.errors = []

        self.setup_headers()

    def setup_headers(self):
        """Setup stealth headers"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'Content-Type': 'application/json',
            'Referer': 'https://eportal.incometax.gov.in/iec/foservices/',
            'Sec-CH-UA': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            'Sec-CH-UA-Mobile': '?1',
            'Sec-CH-UA-Platform': '"Android"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Origin': 'https://eportal.incometax.gov.in',
            'Connection': 'keep-alive'
        })

    def step1_submit_pan(self):
        """STEP 1: Submit PAN - Cookies captured automatically"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 1: Submit PAN - STEALTH SESSION")
            logger.info("="*100)

            url = f"{self.base_url}/iec/loginapi/login"

            # Ensure headers is a plain dict (some Session.header copies may return a Headers object)
            try:
                headers = dict(self.session.headers.copy())
            except Exception:
                # fallback: coerce via items()
                headers = {k: str(v) for k, v in getattr(self.session, 'headers', {}).items()}
            headers['sn'] = 'wLoginService'

            payload = {
                "entity": self.pan,
                "serviceName": "wLoginService"
            }

            logger.info(f"\n📤 REQUEST")
            logger.info(f"   URL: {url}")
            logger.info(f"   Method: POST")

            # ✅ Cookies automatically captured from Set-Cookie headers!
            response = self._safe_post(url, json=payload, headers=headers, timeout=30, verify=True, allow_redirects=True)

            logger.info(f"\n📥 RESPONSE")
            logger.info(f"   Status: {response.status_code} ✓")
            logger.info(f"   Cookies captured: {len(list(self.session.cookies))} ✓")

            data = response.json()



            # Check success
            messages = data.get('messages', [])
            has_success = False

            logger.info(f"\n📋 Messages:")
            for msg in messages:
                code = msg.get('code')
                msg_type = msg.get('type')
                desc = msg.get('desc')

                if code == "EF00000":
                    has_success = True
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")

                else:
                    logger.error(f"   ✗ [{msg_type}] {code}: {desc}")

            if not has_success:
                logger.error("❌ EF00000 not found")
                return False

            # Save fields from Step 1
            self.errors = data.get('errors', [])
            self.req_id = data.get('reqId')
            self.entity = data.get('entity')
            self.entity_type = data.get('entityType')
            self.role = data.get('role')
            self.uid_valdtn_flg = data.get('uidValdtnFlg')
            self.aadhaar_mobile_validated = data.get('aadhaarMobileValidated')
            self.sec_accs_msg = data.get('secAccssMsg')
            self.sec_login_options = data.get('secLoginOptions')
            self.dto_service = data.get('dtoService')
            self.exempted_pan = data.get('exemptedPan')
            self.user_consent = data.get('userConsent')
            self.img_byte = data.get('imgByte')

            if not self.req_id:
                logger.error("❌ No reqId")
                return False

            logger.info(f"\n✅ STEP 1 SUCCESS")
            logger.info(f"   ReqId: {self.req_id}")
            logger.info(f"   Entity: {self.entity}")
            logger.info(f"   Cookies: {len(list(self.session.cookies))} ✓")

            return True

        except Exception as e:
            logger.error(f"❌ Step 1 Exception: {e}")
            import traceback
            traceback.print_exc()
            return False

    def step_3_option_login(self):
        """STEP 3: Option Login - Cookies sent automatically from Step 1 and Step 2"""

        print("\n" + "="*100)
        print("Third Step: Option Login - AUTOMATIC COOKIES")

        payload = {
                "aadhaarMobileValidated": "false",
                "clientIp": self.client_ip,
                "dtoService": "LOGIN",
                "email": self.email,
                "entity": self.entity,
                "entityType": self.entity_type,
                "errors": self.errors,
                "exemptedPan": False,
                "lastLoginSuccessFlag": True,
                "mobileNo": self.mobile_no,
                "otpGenerationFlag": True,
                "otpValdtnFlg": True,
                "pass": None,
                "passValdtnFlg": True,
                "remark": "Continue",
                "reqId": self.req_id,
                "role": self.role,
                "secAccssMsg": "",
                "secLoginOptions": "",
                "serviceName": "loginService",
                "uidValdtnFlg": True,
                "userConsent": "N",
                "userType": "IND"
            }

        url = f"{self.base_url}/iec/loginapi/login"

        try:
            headers = dict(self.session.headers.copy())
        except Exception:
            headers = {k: str(v) for k, v in getattr(self.session, 'headers', {}).items()}

        headers['sn'] = 'loginService'  # ✅ Different from Step 1

        response = self._safe_post(url, json=payload, headers=headers, timeout=30, verify=True, allow_redirects=True)

        if response.status_code == 200:
            data=response.json()
            messages=data.get('messages', [])
            for message in messages:
                code=message.get("code")
                print("code:",code)
                if code=="EF00000":
                    print("✅ STEP 3 SUCCESS")

                    return True

        return False








    def step2_verify_password(self,repeat_req=False):
        """STEP 2: Verify Password - Cookies sent automatically from Step 1"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 2: Verify Password - AUTOMATIC COOKIES")
            logger.info("="*100)

            if not self.req_id or not self.entity:
                logger.error("❌ Missing reqId or entity")
                return False

            url = f"{self.base_url}/iec/loginapi/login"
            existing_session = False

            # Fresh headers with Step 2 'sn' value
            try:
                headers = dict(self.session.headers.copy())
            except Exception:
                headers = {k: str(v) for k, v in getattr(self.session, 'headers', {}).items()}
            headers['sn'] = 'loginService'  # ✅ Different from Step 1

            encoded_password = base64.b64encode(
                self.password.encode()
            ).decode()


            payload = {
                "errors": self.errors,
                "reqId": self.req_id,
                "entity": self.entity,
                "entityType": self.entity_type,
                "role": self.role,
                "uidValdtnFlg": self.uid_valdtn_flg,
                "aadhaarMobileValidated": self.aadhaar_mobile_validated,
                "secAccssMsg": self.sec_accs_msg,
                "secLoginOptions": self.sec_login_options,
                "dtoService": self.dto_service,
                "exemptedPan": self.exempted_pan,
                "userConsent": self.user_consent,
                "imgByte": self.img_byte,
                "pass": encoded_password,
                "passValdtnFlg": None,
                "otpGenerationFlag": None,
                "otp": None,
                "otpValdtnFlg": None,
                "otpSourceFlag": None,
                "contactPan": None,
                "contactMobile": None,
                "contactEmail": None,
                "email": None,
                "mobileNo": None,
                "forgnDirEmailId": None,
                "imagePath": None,
                "serviceName": "loginService"
            }

            logger.info(f"\n📤 REQUEST")
            logger.info(f"   URL: {url}")
            logger.info(f"   sn: {headers['sn']}")
            logger.info(f"   ReqId: {payload['reqId']}")
            logger.info(f"   Cookies: {len(list(self.session.cookies))} ✓ (auto-sent)")

            # ✅ Cookies automatically sent from Step 1!
            response = self._safe_post(url, json=payload, headers=headers, timeout=30, verify=True, allow_redirects=True)

            logger.info(f"\n📥 RESPONSE")
            logger.info(f"   Status: {response.status_code} ✓")

            data = response.json()


            # Parse messages
            messages = data.get('messages', [])
            has_success = False
            has_error = False



            logger.info(f"\n📋 Messages:")
            for msg in messages:
                code = msg.get('code')
                msg_type = msg.get('type')
                desc = msg.get('desc')

                if code == "EF00000":
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
                    has_success = True
                    self.client_ip = data.get("clientIp")
                    self.mobile_no = data.get("mobileNo")
                    self.email = data.get("email")

                if code == "EF00177":
                    existing_session = True



                elif code == "EF500023":
                    logger.error(f"   ✗ [{msg_type}] {code}: {desc}")
                    has_error = True

                else:
                    if msg_type == "ERROR":
                        logger.error(f"   ✗ [{msg_type}] {code}: {desc}")
                        has_error = True
                    else:
                        logger.info(f"   → [{msg_type}] {code}: {desc}")

            if has_error :
                logger.error("❌ Error in response")
                return {"success":False,"existing_session":False}

            if not has_success:
                logger.error("❌ EF00000 not found")
                return {"success":False,"existing_session":False}

            if existing_session and has_success:
                return {"existing_session": True,"success":True}



            logger.info(f"\n✅ STEP 2 SUCCESS")
            logger.info(f"   passValdtnFlg: {data.get('passValdtnFlg')} ✓")


            return {"existing_session":False,"success":True}

        except Exception as e:
            logger.error(f"❌ Step 2 Exception: {e}")
            import traceback
            traceback.print_exc()
            return False

    def login(self):
        """Complete login flow"""
        try:
            logger.info("\n" + "="*100)
            logger.info("ePortal Login - STEALTH SESSION (Auto Cookies)")
            logger.info(f"PAN: {self.pan}")
            logger.info("="*100)
            success = False



            if not self.step1_submit_pan():
                logger.error("\n❌ Login failed at Step 1")
                return {'success': False, 'error': 'Step 1 failed', 'step': 1}



            for i in range(6):
                time.sleep(i)
                resp=self.step2_verify_password()
                if resp.get("success") and not resp.get("existing_session"):
                    success = True
                    break


            if resp.get("existing_session"):
                for i in range(6):
                    time.sleep(i)
                    resp=self.step_3_option_login()
                    if resp:
                        success = True
                        break

                if not resp:
                    logger.error("\n❌ Login failed at Step 3")
                    return {'success': False, 'error': 'Step 3 failed', 'step': 3}


            logger.info("\n" + "="*100)
            logger.info("✅ LOGIN SUCCESSFUL!")
            logger.info("="*100)
            logger.info(f"ReqId: {self.req_id}")
            logger.info(f"User: {self.entity}")
            print(f"Cookies: {len(list(self.session.cookies))} ✓")
            print("headers:", self.session.headers)
            logger.info(f"Cookies: {len(list(self.session.cookies))} ✓")
            logger.info("="*100)

            return {
                'success': True,
                'req_id': self.req_id,
                'user_id': self.entity,
                'cookies': dict((c.name, c.value) for c in self.session.cookies),
                'session': self.session  # ✅ Use for API calls
            }

        except Exception as e:
            logger.error(f"❌ Login Exception: {e}")
            return {'success': False, 'error': str(e)}


    def _update_cookies_from_response(self, response):
        """Parse Set-Cookie from response headers and merge into session cookiejar.

        Also updates the session headers 'Cookie' to reflect the current cookiejar so
        subsequent requests that send explicit headers match what the server expects.
        """
        if not response or not hasattr(response, 'headers'):
            return
        # Attempt to collect all Set-Cookie header values. Some servers send multiple
        # Set-Cookie headers (preferred), while others may combine them into a single
        # header string which can break naive parsing. Try multiple strategies.
        set_cookie_values = []

        # 1) Try to get all Set-Cookie values from the raw headers (urllib3/httplib expose get_all)
        try:
            raw = getattr(response, 'raw', None)
            if raw is not None:
                # raw.headers may be an HTTPMessage with get_all
                raw_headers = getattr(raw, 'headers', None)
                if raw_headers is not None and hasattr(raw_headers, 'get_all'):
                    set_cookie_values = raw_headers.get_all('Set-Cookie') or []
        except Exception:
            set_cookie_values = []

        # 2) Fallback to response.headers which may contain a single (possibly combined) header
        if not set_cookie_values:
            header_val = response.headers.get('Set-Cookie')
            if header_val:
                # Try to split combined Set-Cookie header into separate cookie strings.
                # Splitting on ', ' is naive because cookie values may contain commas; use
                # a regex to split where a comma is followed by a token that looks like a cookie-name.
                parts = re.split(r', (?=[^\s=]+=)', header_val)
                set_cookie_values = parts

        if not set_cookie_values:
            return

        # Parse each Set-Cookie header value separately and merge into cookiejar
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
                        # fallback to simple set
                        self.session.cookies.set(name, value)
            except Exception:
                # If any single Set-Cookie string fails to parse, skip it and continue
                continue

        # rebuild explicit Cookie header from cookiejar (order from cookiejar)
        try:
            cookie_header = "; ".join([f"{c.name}={c.value}" for c in self.session.cookies])
            if cookie_header:
                self.session.headers.update({"Cookie": cookie_header})
        except Exception:
            pass


    def get_prefill_data(self,year,pan):
        url = "https://eportal.incometax.gov.in/iec/efileprocessingapi/auth/saveEntity"
        payload={
            "ay":year,
            "pan":pan,
            "serviceName": "taxDepositService"



        }

        response = self._safe_post(url, json_data=payload,sn="")

        return response.json()

    def get_itr_status(self,pan):
        url = "https://eportal.incometax.gov.in/iec/servicesapi/auth/getEntity"
        payload = {
            "header": {"formName": "FO-006-ITRST"},
            "serviceName": "itrStatusServiceShort",
            "entityNum": pan
        }

        response = self.post(url, json_data=payload)
        return response.json()

    def get_download_itr_file(self,pan,ackn_no):
        url="https://eportal.incometax.gov.in/iec/itrweb/auth/v0.1/returns/downloadfile"
        payload={
            "ackNum": ackn_no,
            "loggedInUserId": pan
        }
        response = self._safe_post(url, json_data=payload, sn="")

        if response.status_code == 200:
            return response.json()
        else:
            return {"success": False, "error": f"HTTP {response.status_code}"}


    def get_active_verify_filings(self, pan=None):
        """Retrieve active EVC filings for a PAN.

        pan: optional PAN to query; if omitted, a default loggedInUserId is used.
        The method saves the full JSON response to data/everify_active_filings_<ts>.json and
        returns a structured dict with success, saved, file, activeList and messages.
        """
          # endpoint used by browser requests
        step_1_url = "https://eportal.incometax.gov.in/iec/servicesapi/auth/getEntity"

        payload = {
            "header": {"formName": "FO-016-EVRTN"},
            "serviceName": "eVerifyReturnPostLoginService",
            "entityNum": pan
        }
        response = self._safe_post(step_1_url, json_data=payload,sn="eVerifyReturnPostLoginService")

        #if fails return the response code

        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}", "raw_text": response.text}



        try:
            data = response.json()
            print(json.dumps(data, indent=4))
        except Exception:
            # Non-JSON response
            return {"success": False, "error": "invalid_json_response", "raw_text": response.text}


        messages = data.get("messages", [])
        success = False


        for m in messages:

            code = m.get("code")
            print(code)
            if code == "EF40003":
                success = True
                active_list_raw = data.get("activeList", [])
                # Convert JSON string to list if needed
                if isinstance(active_list_raw, str):
                    try:
                        self.active_fillings = json.loads(active_list_raw)
                    except json.JSONDecodeError:
                        self.active_fillings = []
                else:
                    self.active_fillings = active_list_raw if isinstance(active_list_raw, list) else []

            if success:
                print(data)
                return {
                    "success": True,
                    "data": data,
                    "messages": messages,
                }

            else:
                return {
                    "success": False,
                    "data": data,
                    "messages": messages,
                }

    def revise_active_efillings(self,year=None):
        """Retrieve active EVC filings for a PAN.

        pan: optional PAN to query; if omitted, a default loggedInUserId is used.
        The method saves the full JSON response to data/everify_active_filings_<ts>.json and
        returns a structured dict with success, saved, file, activeList and messages.
        """
          # endpoint used by browser requests
        step_1_url = "https://eportal.incometax.gov.in/iec/servicesapi/auth/getEntity"
        # Find the matching active filing by year, with robust error handling
        pan = None

        for filling in self.active_fillings:


            if filling and filling.get("assmentYear") == year:
                pan = filling.get("entityNum")

                break



        # If no matching filing found, raise an error or return early
        if not pan:
            return {
            "success": False,
            "error": f"No active filing found for year {year}",
            "messages": []
            }


        payload = {
            "assmentYear": str(year),
            "entityNum": pan,
            "serviceName": "everifyReturnPostLoginRevisedValidation"
        }
        response = self._safe_post(step_1_url, json_data=payload,sn="everifyReturnPostLoginRevisedValidation")

        try:
            data = response.json()

            print(json.dumps(data, indent=4))
        except Exception:
            # Non-JSON response
            return {"success": False, "error": "invalid_json_response", "raw_text": response.text}

        messages = data.get("messages", [])
        success = False
        for m in messages:
            code = m.get("code")
            mtype = m.get("type", "")
            desc = m.get("desc", "")
            if code == "EF40003" :
                success = True
                break

            if success:
                print(data)
                return {
                    "success": True,
                    "data": data,
                    "messages": messages,
                }

            else:
                return {
                    "success": False,
                    "data": data,
                    "messages": messages,
                }
    def check_adhaar_linked(self,pan):
        url = "https://eportal.incometax.gov.in/iec/verificationservices/auth/getEntity"
        payload = {
            "serviceName": "verifyOtpUsingAadhar",
            "header": {"formName": "FO-091-EVERI"},
            "loggedInUserId": pan
        }


        response = self._safe_post(url, json_data=payload,sn="verifyOtpUsingAadhar")

        if response.status_code != 200:
            return {
            "success": False,
            "error": f"HTTP {response.status_code}",
            "raw_text": response.text
            }

        try:
            data = response.json()
        except Exception:
            return {
            "success": False,
            "error": "invalid_json_response",
            "raw_text": response.text
            }

        messages = data.get("messages", [])
        success = False

        for m in messages:
            code = m.get("code")
            if code == "AADHAR_PAN_LINKAGE_CONSTANT":
                success = True
                break

        return {
            "success": success,
            "data": data,
            "messages": messages
        }


    def send_otp_aadhaar(self, pan):
        url = "https://eportal.incometax.gov.in/iec/verificationservices/auth/getEntity"
        payload = {
            "serviceName": "verifyOtpUsingAadhar",
            "header": {"formName": "FO-091-EVERI"},
            "loggedInUserId": pan
        }

        response = self._safe_post(url, json_data=payload, sn="verifyOtpUsingAadhar")

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}",
                "raw_text": response.text
            }

        try:
            data = response.json()
        except Exception:
            return {
                "success": False,
                "error": "invalid_json_response",
                "raw_text": response.text
            }

        messages = data.get("messages", [])
        success = "SUCCESS" in data.get("status", "")  # Check if status is SUCCESS

        return {
            "success": success,
            "data": data,
            "messages": messages
        }




    def _safe_post(self, url,**kwargs):
        """Perform POST with retries on connection errors.

        Uses CurlConnectionError when available; falls back to requests.ConnectionError.
        Returns the response on success or raises the last exception after retries.
        """
        max_retries = 3
        backoff_factor = 2
        delay = 1  # initial delay in seconds

        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                if "sn" in kwargs:
                    self.session.headers.update({"sn": str(kwargs['sn'])})

                print("Making POST request to:", url, "with headers=", self.session.headers)
                if "json_data" in kwargs:
                    response = self.session.post(url, json=kwargs['json_data'])
                else:
                    response = self.session.post(url,**kwargs)

                print("Response Status Code:", response.status_code)
                    # Update cookies from response
                if response is not None and response.status_code == 200:
                    self._update_cookies_from_response(response)
                return response
            except (requests.ConnectionError) as e:
                last_exception = e
                logger.warning(f"Connection error on attempt {attempt}/{max_retries}: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= backoff_factor  # exponential backoff
                else:
                    logger.error(f"Max retries reached. Failing POST to {url}")
                    raise last_exception

    def _update_cookies_from_response(self, response):

        """Parse Set-Cookie headers and merge into session cookiejar; update explicit Cookie header."""
        if not response or not hasattr(response, 'headers'):
            return

        # Collect Set-Cookie header values (support multiple headers or combined header)
        set_cookie_values = []
        try:
            raw = getattr(response, 'raw', None)
            if raw is not None:
                raw_headers = getattr(raw, 'headers', None)
                if raw_headers is not None and hasattr(raw_headers, 'get_all'):
                    set_cookie_values = raw_headers.get_all('Set-Cookie') or []
        except Exception:
            set_cookie_values = []

        if not set_cookie_values:
            header_val = response.headers.get('Set-Cookie')
            if header_val:
                parts = re.split(r', (?=[^\s=]+=)', header_val)
                set_cookie_values = parts

        if not set_cookie_values:
            return

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
                        try:
                            self.session.cookies.set(name, value)
                        except Exception:
                            pass
            except Exception:
                continue

        # rebuild explicit Cookie header from cookiejar
        try:
            cookie_header = "; ".join([f"{c.name}={c.value}" for c in self.session.cookies])
            if cookie_header:
                # ensure headers is a dict-like object
                try:
                    self.session.headers.update({"Cookie": cookie_header})
                except Exception:
                    try:
                        # if session exposes an inner requests.Session
                        inner = getattr(self.session, 'session', None)
                        if inner is not None:
                            inner.headers.update({"Cookie": cookie_header})
                    except Exception:
                        pass
        except Exception:
            pass


if __name__ == "__main__":
    user = {
        "PAN": "ABCDE1234F",
        "PASSWORD": "your-password-here"
    }

    api =ePortalLoginStealth(user)
    auth = api.login()


    if auth["success"]:
        time.sleep(2)
        res=api.get_prefill_data(year=2025,pan="ABCDE1234F")
        print(json.dumps(res, indent=4))

        res=api.get_active_verify_filings(pan="ABCDE1234F")
        print(json.dumps(res, indent=4))
        res=api.revise_active_efillings(year=2025)
        print(json.dumps(res, indent=4))
        resp=api.check_adhaar_linked(pan="ABCDE1234F")
        print(json.dumps(resp, indent=4))
        otp_resp=api.send_otp_aadhaar(pan="ABCDE1234F")
        print(json.dumps(otp_resp, indent=4))


    # r=api.get_download_itr_file("ABCDE1234F",ackn_no="120942390131025")
    # print(json.dumps(r, indent=4))
