import requests
import json
import base64
import logging
import time
import re
from datetime import datetime
from playwrite_login_with_session_cookie import EPortalLoginStealth
from http.cookies import SimpleCookie

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='eportal_registration.log',
    filemode='a'
)
logger = logging.getLogger(__name__)


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
        self.dob = user_details.get("DOB") if user_details else None
        self.gender = user_details.get("GENDER") if user_details else None
        self.residential_status = user_details.get("RESIDENT") if user_details else None
        self.pin_code = user_details.get("PIN") if user_details else None
        self.address = user_details.get("ADDRESS") if user_details else None
        self.new_password = user_details.get("PASSWORD") if user_details else None
        self.personal_message = user_details.get("PERMSG") if user_details else None
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

        for c in cookies:
            self.session.cookies.set(c["name"], c["value"])
        try:
            # build explicit Cookie header from initial cookiejar
            cookie_header = "; ".join([f"{c.name}={c.value}" for c in self.session.cookies])
            if cookie_header:
                self.session.headers.update({"Cookie": cookie_header})
        except Exception:
            pass

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
        print(self.session.headers)
        resp = self.session.post(url, json=json_data, headers=self.session.headers)
        # update cookies/headers from response
        try:
            self._update_cookies_from_response(resp)
        except Exception:
            pass
        return resp


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

            print(json.dumps(data, indent=2))

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
            print(payload)

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
                    result=self.get_districts(user_details.get("PIN"))
                    self.req_id=data.get("reqId")
                    if not result:
                        logger.error("No districts found for the given PIN code.")
                        return {'success': False, 'error': 'no_districts_found', 'step': 2}

                    result=self.get_states(user_details.get("PIN"))
                    if not result:
                        logger.error("No states found for the given PIN code.")
                        return {'success': False, 'error': 'no_states_found', 'step': 2}

                    result=self.get_localities(user_details.get("PIN"))
                    if not result:
                        logger.error("No localities found for the given PIN code.")
                        return {'success': False, 'error': 'no_localities_found', 'step': 2}
                    result=self.get_post_offices(user_details.get("PIN"))
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
                "priMobileNum": user_details.get("MOBILE"),
                "isdCd": "91",
                "priMobBelongsTo": 1,
                "priEmailRelationId": 1,
                "priEmailId": user_details.get("EMAIL"),
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

            print(json.dumps(payload, indent=2))

            response = self.post(url, json_data=payload, sn='indivRegistrationService')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            logger.info(f"Status: {response.status_code}")


            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 3}

            data = response.json()
            print(data)
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


                if code in ["EF00000","EF00015","status"] and response.status_code == 200:
                    self.tot=int(time.time() * 1000)
                    if panadhar:
                        if desc == "SUCCESS":
                            logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
                            self.aadhaarTxnId = data.get("aadhaarTxnId")
                            return {'success': True, 'error': None, 'step': 4}
                    logger.info(f"   ✓ [{msg_type}] {code}: {desc}")
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

    def step5_set_new_password(self):
        """STEP 5: Set New Password"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 5: Set New Password")
            logger.info("="*100)

            url = f"{self.base_url}/iec/registrationapi/validateOTP"
            headers = self.session.headers.copy()
            headers['sn'] = 'indivRegistrationService'

            payload = {
                "cred": base64.b64encode(self.new_password.encode()).decode(),
                "firstName": base64.b64encode(self.first_name.encode()).decode(),
                "isTrue": False,
                "lastName": base64.b64encode(self.last_name.encode()).decode(),
                "midName": base64.b64encode(self.middle_name.encode()).decode() if self.middle_name is not None else "",
                "reqId": self.req_id,
                "secAccessMsg": self.personal_message,
                "serviceName": "indivRegistrationService",
                "totRegTime": self.tot
            }

            print(json.dumps(payload, indent=2))

            response = self.post(url,json_data=payload,sn='indivRegistrationService')
            try:
                self._update_cookies_from_response(response)
            except Exception:
                pass
            print(response.json())
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 5}


            print(response.text())

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
            print(response.text)
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                logger.error("Failed to fetch post offices")
                return []

            data = response.json()
            print("[DEBUG] Post Offices Data:", json.dumps(data, indent=2))
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


if __name__ == "__main__":
    print("\n" + "="*100)
    print("ePortal Register Portal ")
    print("="*100)

    # Example user details (well-structured)
    user_details = {
        "PAN": "ABCDE1234F",
        "FIRSTNAME": "",
        "MIDDLENAME": "",
        "LASTNAME": "SANTHOSH",
        "DOB": "1991-05-20",
        "GENDER": "MALE",
        "RESIDENT": True,
        "ADDRESS": "Chack saugahna post parsedi dist bahraich uttar pradesh 271903",
        "PIN": "271903",
        "PASSWORD": "your-password-here",
        "CONFIRMPWD": "your-password-here",
        "PERMSG": "easyreturn.co.in",
        "EMAIL": "pwpattayalisting@gmail.com",     # required by step3_validate_contact
        "MOBILE": "7409985506"           # required by step3_validate_contact
    }

    # Load cookies and headers from a file (obtained via Playwright)


    browser_obj = EPortalLoginStealth(userData=user_details)
    data = browser_obj.open_register()


    service = ePortalRegistrationApi(user_details,data['headers'],data['cookies'])



    result = service.step1_validate_pan()

    print("Step 1 Result:", result)

    if not result['success']:
        print(f"❌ Step 1 Failed: {result['error']}")
        exit(1)

    print("✅ Step 1 Successful: PAN Validated")

    if result.get('aadhar_validation') == True:
        aadhar_result=service.aadhar_validator()
        if not aadhar_result['success']:
            print(f"❌ Aadhar Validation Failed: {aadhar_result['error']}: {aadhar_result.get('message')}")
            exit(1)

        otp = input("Enter the aadhar OTP:")

        validated_aadhar=service.step4_validate_otp(otp=otp,panadhar=True)

        if not validated_aadhar['success']:
            print(f"❌ Aadhar OTP Validation Failed: {validated_aadhar['error']}")
            exit(1)
        print("✅ Aadhar Validation Successful")

    result=service.step2_validate_details()

    if not result['success']:
        print(f"❌ Step 2 Failed: {result['error']}")
        exit(1)

    result=service.step3_validate_contact()

    if not result['success']:
        print(f"❌ Step 3 Failed: {result['error']}")
        exit(1)


    otp=input("Enter the mobile OTP:")
    email_otp=input("Enter the email OTP:")

    result=service.step4_validate_otp(otp=otp,email_otp=email_otp)

    print("OTP Validation Result:", result)

    if not result['success']:
        print(f"❌ Step 4 Failed: {result['error']}")
        exit(1)


    result=service.step5_set_new_password()

    if not result['success']:
        print(f"❌ Step 5 Failed: {result['error']}")
        exit(1)
