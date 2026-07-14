import requests
import json
import base64
import logging
import time
from datetime import datetime
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='forget-pwd.log',
    filemode='a'
)
logger = logging.getLogger(__name__)


class ePortalForgotPassword:
    """
    ePortal Forgot Password Service - FINAL VERSION

    Corrected email/mobile flow with:
    - mbTrans/eTrans transaction IDs in Step 2 & 3
    - "newCredential" field (not "pass") in Step 4
    - Proper error handling for EF00072, EF00073, EF00043, EF00239
    """

    def __init__(self, pan):
        self.pan = pan
        self.base_url = "https://eportal.incometax.gov.in"
        self.session = requests.Session()
        self.req_id = None
        self.today_date = datetime.now().strftime("%Y-%m-%d")

        # Email/Mobile specific
        self.mb_trans = None
        self.e_trans = None
        self.role=None
        self.user_type=None
        # Aadhar specific
        self.autkn = None
        self.otp_source_flag = None

        # Account info
        self.aadhar_linked = False
        self.otp_validated = False
        self.otp_method = None

        self.setup_headers()


    def setup_headers(self):
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
        """STEP 1: Submit PAN"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 1: Submit PAN")
            logger.info("="*100)

            url = f"{self.base_url}/iec/loginapi/login"
            headers = self.session.headers.copy()
            headers['sn'] = 'forgotPwdService'

            payload = {
                "entity": self.pan,
                "serviceName": "forgotPwdService"
            }

            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 1}

            data = response.json()
            # print(data)
            logger.info(f"Raw Step1 Response: {json.dumps(data, indent=2)[:500]}")

            # Core identifiers
            self.req_id = data.get('reqId')
            self.role = data.get('role')
            self.user_type = data.get('userType')
            self.adhar_linked_with_pan=data.get("aadhaarLinkedWithUserId")

            if self.adhar_linked_with_pan and self.adhar_linked_with_pan == "N":
                return {
                    'success': False,
                    'error': 'aadhaar_not_linked',
                    'step': 1,
                    'messages': [{'code': 'EF00239', 'type': 'ERROR', 'desc': 'Aadhaar not linked with PAN'}]
                }

            # Parse messages
            messages = data.get('messages', [])
            parsed_messages = []
            blocking_errors = []
            warnings = []

            for msg in messages:
                code = msg.get('code')
                desc = msg.get('desc')
                mtype = msg.get('type')
                parsed_messages.append({'code': code, 'type': mtype, 'desc': desc})
                logger.info(f"[{mtype}] {code}: {desc}")

                if code == "EF00035":
                    blocking_errors.append({'code': code, 'reason': 'user_id_not_exist', 'desc': desc})
                # elif code == "EF500023":
                #     blocking_errors.append({'code': code, 'reason': 'request_not_authenticated', 'desc': desc})
                elif code == "EF00239":
                    # treat as warning (invalid email/mobile) - not blocking for PAN submission
                    warnings.append({'code': code, 'reason': 'invalid_email_or_mobile', 'desc': desc})

            if not self.req_id:
                return {
                    'success': False,
                    'error': 'no_reqid',
                    'step': 1,
                    'messages': parsed_messages
                }

            if blocking_errors:
                return {
                    'success': False,
                    'error': 'blocking_errors',
                    'step': 1,
                    'req_id': self.req_id,
                    'role': self.role,
                    'user_type': self.user_type,
                    'messages': parsed_messages,
                    'details': blocking_errors,
                    'warnings': warnings
                }

            return {
                'success': True,
                'req_id': self.req_id,
                'role': self.role,
                'user_type': self.user_type,
                'messages': parsed_messages,
                'warnings': warnings
            }
            # if not self.req_id:
            #     return {'success': False, 'error': 'no_reqid', 'step': 1}

            # self.aadhar_linked = data.get('aadhaarLinkedWithUserId') == "Y"

            # logger.info(f"✅ STEP 1 SUCCESS | ReqId: {self.req_id}")
            # return {'success': True}

        except Exception as e:
            logger.error(f"Step 1 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 1}


    def step2_request_otp_aadhaar(self):
        """STEP 2: Request Aadhar OTP - Extracts autkn"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 2: Request Aadhar OTP")
            logger.info("="*100)

            if not self.req_id:
                return {'success': False, 'error': 'no_reqid', 'step': 2}

            url = f"{self.base_url}/iec/loginapi/login"
            headers = self.session.headers.copy()
            headers['sn'] = 'forgotPwdService'

            payload = {
                "entity": self.pan,
                "otpSourceFlag": "A",
                "reqId": self.req_id,
                "serviceName": "forgotPwdService"
            }

            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 2}

            data = response.json()


            # print(data)
            self.autkn = data.get('autkn')




            if not self.autkn:
                return {'success': False, 'error': 'no_autkn', 'step': 2}


            self.otp_source_flag = data.get('otpSourceFlag', 'E')


            logger.info(f"✅ STEP 2 SUCCESS | OTP Method: Aadhar | autkn: {self.autkn[:30]}...")
            return {'success': True}

        except Exception as e:
            logger.error(f"Step 2 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 2}

    def step3_verify_otp_aadhar(self, otp):
        """STEP 3: Verify Aadhar OTP"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 3: Verify Aadhar OTP")
            logger.info("="*100)

            if not self.req_id or not self.autkn:
                return {'success': False, 'error': 'missing_session', 'step': 3}

            url = f"{self.base_url}/iec/loginapi/login"
            headers = self.session.headers.copy()
            headers['sn'] = 'forgotPwdService'

            payload = {
                "autkn": self.autkn,
                "entity": self.pan,
                "entityType": "PAN",
                "otp": otp,
                "otpSourceFlag": self.otp_source_flag,
                "reqId": self.req_id,
                "role": self.role,
                "serviceName": "forgotPwdService",
                "uidValdtnFlg": "true"
            }

            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            logger.info(f"Status: {response.status_code}")
            logger.info(f"Response: {json.dumps(response.json(), indent=2)[:500]}")
            success = False

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 3}

            data = response.json()

            messages = data.get('messages', [])
            for msg in messages:
                code = msg.get('code')
                desc = msg.get('desc')
                msg_type = msg.get('type')

                if code == "EF00000":
                    success=True
                elif code == "EF00006":
                    logger.error("❌ Wrong Aadhar OTP")
                    return {'success': False, 'error': 'wrong_otp_aadhar', 'message': desc, 'can_retry': True, 'step': 3}
                elif code == "EF00074":
                    logger.error("❌ Aadhar OTP Expired")
                    return {'success': False, 'error': 'otp_expired_aadhar', 'message': desc, 'can_retry': True, 'step': 3}
                elif code == "EF00239":
                    pass


            if success:
                otp_validation_flag = data.get('otpValdtnFlg')
                logger.info(f"OTP Validation Flag: {otp_validation_flag}")

                if otp_validation_flag == "true":
                    logger.info(f"✅ STEP 3 SUCCESS | OTP Verified")
                    self.otp_validated = True
                    return {'success': True}
                else:
                    logger.error(f"❌ OTP Validation Failed")
                    return {'success': False, 'error': 'otp_validation_failed', 'step': 3}

            if not success:
                logger.error(f"❌ OTP Verification Failed - No EF00000")
                return {'success': False, 'error': 'otp_verification_failed', 'step': 3}



        except Exception as e:
            logger.error(f"Step 3 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 3}

    def set_password_aadhar(self, new_password):
        """STEP 4: Set new password - Aadhar"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 4: Set New Password")
            logger.info("="*100)

            if not self.otp_validated:
                return {'success': False, 'error': 'otp_not_validated', 'step': 4}

            if not self.req_id:
                return {'success': False, 'error': 'missing_session', 'step': 4}

            url = f"{self.base_url}/iec/loginapi/login"
            headers = self.session.headers.copy()
            headers['sn'] = 'forgotPwdService'

            encoded_password = base64.b64encode(new_password.encode()).decode()

            payload = {
                "entity": self.pan,
                "newCredential": encoded_password,
                "otpSourceFlag": self.otp_source_flag,
                "reqId": self.req_id,
                "role": self.role,
                "serviceName": "forgotPwdService",
                "uidValdtnFlg": "true",
                "userType": self.user_type
            }

            logger.info(f"POST {url}")
            logger.info(f"Password reset using newCredential field")
            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 4}

            data = response.json()
            # print(data)
            logger.info(f"Response: {json.dumps(data, indent=2)[:500]}")

            # Parse messages - look for EF00043
            messages = data.get('messages', [])
            success = False

            for msg in messages:
                code = msg.get('code')
                desc = msg.get('desc')
                msg_type = msg.get('type')

                logger.info(f"[{msg_type}] {code}: {desc}")

                # ✅ NEW: Check for EF00043 success
                if code == "EF00043":
                    logger.info("✅ Password updated successfully")
                    return {'success': True, 'message': 'Password reset successfully', 'step': 4, 'mssage': desc}

                # ✅ IGNORE EF00239 - password still works
                elif code == "EF00239":
                    logger.warning(f"⚠️ Profile warning (ignored): {desc}")
                    continue



                else:
                    error_message=f"❌ Unexpected message code during password reset: {code}"
                    error_desc=desc
                    logger.error(f"❌ Password reset failed - no EF00043")
                    return {'success': False, 'error': error_desc, 'step': 4}


            if success:
                logger.info(f"✅ STEP 4 SUCCESS | Password")

                return {'success': True, 'message': 'Password reset successfully'}
            else:
                logger.error(f"❌ Password reset failed - no EF00043")
                return {'success': False, 'error': error_desc, 'step': 4}


        except Exception as e:
            logger.error(f"Step 4 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 4}


    def step2_request_otp_email_mobile(self,dob="1993-08-09"):
        """STEP 2: Request Email/Mobile OTP - Extracts mbTrans and eTrans"""
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 2: Request Email/Mobile OTP")
            logger.info("="*100)

            if not self.req_id:
                return {'success': False, 'error': 'no_reqid', 'step': 2}

            url = f"{self.base_url}/iec/loginapi/login"
            headers = self.session.headers.copy()
            headers['sn'] = 'forgotPwdService'

            payload = {
                "date": dob,
                "entity": self.pan,
                "reqId": self.req_id,
                "serviceName": "forgotPwdService"
            }

            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 2}

            data = response.json()

            # print(data)

            # Extract transaction IDs
            self.mb_trans = data.get('mbTrans')
            self.e_trans = data.get('eTrans')

            if not self.mb_trans or not self.e_trans:
                logger.error("❌ Missing transaction IDs")
                return {'success': False, 'error': 'no_trans_ids', 'step': 2}

            otpGenerationFlag = data.get('otpGenerationFlag')
            if otpGenerationFlag != "true":
                logger.error("❌ OTP generation failed")
                return {'success': False, 'error': 'otp_not_generated', 'step': 2}

            logger.info(f"✅ STEP 2 SUCCESS")
            logger.info(f"   mbTrans: {self.mb_trans}")
            logger.info(f"   eTrans: {self.e_trans}")

            return {'success': True, 'mb_trans': self.mb_trans, 'e_trans': self.e_trans}

        except Exception as e:
            logger.error(f"Step 2 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 2}

    def step3_verify_otp_email_mobile(self, otp, email_otp):
        """
        STEP 3: Verify Email/Mobile OTP
        ✅ Uses mbTrans/eTrans transaction IDs
        ✅ Handles EF00072/EF00073 errors (wrong OTP)
        """
        try:
            logger.info("\n" + "="*100)

            logger.info("="*100)

            if not self.req_id or not self.mb_trans or not self.e_trans:
                return {'success': False, 'error': 'missing_session', 'step': 3}

            url = f"{self.base_url}/iec/loginapi/login"
            headers = self.session.headers.copy()
            headers['sn'] = 'forgotPwdService'



            payload = {
                "eTrans": self.e_trans,
                "emailOtp": email_otp,
                "entity": self.pan,
                "mbTrans": self.mb_trans,
                "otp": otp,
                "otpSourceFlag": "E",
                "reqId": self.req_id,
                "serviceName": "forgotPwdService",
                "uidValdtnFlg": "true"
            }


            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 3}

            data = response.json()
            # print(data)
            logger.info(f"Response: {json.dumps(data, indent=2)[:500]}")

            # Parse messages
            messages = data.get('messages', [])

            for msg in messages:
                code = msg.get('code')
                desc = msg.get('desc')
                msg_type = msg.get('type')

                logger.info(f"[{msg_type}] {code}: {desc}")

                # ✅ NEW: Handle EF00072/EF00073
                if code == "EF00072":
                    logger.error("❌ Wrong Mobile OTP")
                    return {
                        'success': False,
                        'error': 'wrong_otp_mobile',
                        'message': desc,
                        'can_retry': True,
                        'step': 3
                    }

                elif code == "EF00073":
                    logger.error("❌ Wrong Email OTP")
                    return {
                        'success': False,
                        'error': 'wrong_otp_email',
                        'message': desc,
                        'can_retry': True,
                        'step': 3
                    }

            # Check OTP validation flag
            otp_validation_flag = data.get('otpValdtnFlg')

            if otp_validation_flag == "true":
                logger.info(f"✅ STEP 3 SUCCESS | OTP Verified")
                self.otp_validated = True

                return {'success': True}
            else:
                logger.error(f"❌ OTP Validation Failed")
                return {
                    'success': False,
                    'error': 'otp_validation_failed',
                    'step': 3
                }

        except Exception as e:
            logger.error(f"Step 3 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 3}

    def step4_set_new_password_email_mobile(self, new_password):
        """
        STEP 4: Set new password - Email/Mobile
        ✅ Uses "newCredential" field (NOT "pass"!)
        ✅ Includes otpSourceFlag, uidValdtnFlg, userType
        ✅ Checks for EF00043 success code
        ✅ Ignores EF00239 error
        """
        try:
            logger.info("\n" + "="*100)
            logger.info("STEP 4: Set New Password")
            logger.info("="*100)

            if not self.otp_validated:
                return {'success': False, 'error': 'otp_not_validated', 'step': 4}

            if not self.req_id:
                return {'success': False, 'error': 'missing_session', 'step': 4}

            url = f"{self.base_url}/iec/loginapi/login"
            headers = self.session.headers.copy()
            headers['sn'] = 'forgotPwdService'

            encoded_password = base64.b64encode(new_password.encode()).decode()

            # ✅ CORRECTED: Use "newCredential" for email/mobile
            otpSourceFlag =  "E"

            payload = {
                "entity": self.pan,
                "newCredential": encoded_password,  # ✅ NOT "pass"!
                "otpSourceFlag": otpSourceFlag,      # E or M
                "reqId": self.req_id,
                "role": "IN",
                "serviceName": "forgotPwdService",
                "uidValdtnFlg": "true",
                "userType": "IND"
            }

            logger.info(f"POST {url}")
            logger.info(f"Password reset using newCredential field")
            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            logger.info(f"Status: {response.status_code}")

            if response.status_code != 200:
                return {'success': False, 'error': 'request_failed', 'step': 4}

            data = response.json()
            logger.info(f"Response: {json.dumps(data, indent=2)[:500]}")
            # print(data)

            # Parse messages - look for EF00043
            messages = data.get('messages', [])
            success = False

            for msg in messages:
                code = msg.get('code')
                desc = msg.get('desc')
                msg_type = msg.get('type')

                logger.info(f"[{msg_type}] {code}: {desc}")

                # ✅ NEW: Check for EF00043 success
                if code == "EF00043":
                    logger.info("✅ Password updated successfully")
                    return {'success': True, 'message': 'Password reset successfully', 'step': 4, 'mssage': desc}

                # ✅ IGNORE EF00239 - password still works
                elif code == "EF00239":
                    logger.warning(f"⚠️ Profile warning (ignored): {desc}")
                    continue

            if success:
                logger.info(f"✅ STEP 4 SUCCESS | Password Reset Completed")
                return {'success': True, 'message': 'Password reset successfully'}
            else:
                logger.error(f"❌ Password reset failed - no EF00043")
                return {'success': False, 'error': 'password_reset_failed', 'step': 4}

        except Exception as e:
            logger.error(f"Step 4 Error: {e}")
            return {'success': False, 'error': str(e), 'step': 4}


    def request_otp_email_mobile(self, dob="1993-08-09"):
        """Complete Email/Mobile OTP validation flow"""
        try:
            logger.info("\n" + "="*100)
            logger.info("Email/Mobile OTP Validation Flow")
            logger.info("="*100)

            result = self.step1_submit_pan()
            if not result['success']:
                return result

            time.sleep(1)

            result = self.step2_request_otp_email_mobile(dob="1993-08-09")
            print(f"Result from the otp response is :{result}")
            if not result['success']:
                return result

            time.sleep(1)

            logger.info("\n" + "="*100)
            logger.info("✅ MOBILE OTP SEND SUCCESSFUL!")
            logger.info("="*100)

            return {
                'success': True,
                'message': 'Password reset successfully',
                'method': 'Mobile OTP'
            }

        except Exception as e:
            logger.error(f"Error: {e}")
            return {'success': False, 'error': str(e)}

    def validate_and_set_password_email_mobile(self, otp, email_otp, new_password):
        """Complete Email/Mobile OTP validation and password reset flow"""
        try:
            logger.info("\n" + "="*100)
            logger.info("Email/Mobile OTP Validation and Password Reset Flow")
            logger.info("="*100)

            result = self.step3_verify_otp_email_mobile(otp, email_otp)
            if not result['success']:
                return result

            time.sleep(1)

            result = self.step4_set_new_password_email_mobile(new_password)
            if not result['success']:
                return result

            logger.info("\n" + "="*100)
            logger.info("✅ PASSWORD RESET SUCCESSFUL!")
            logger.info("="*100)

            return {
                'success': True,
                'message': 'Password reset successfully',
                'method': 'Email/Mobile OTP'
            }

        except Exception as e:
            logger.error(f"Error: {e}")
            return {'success': False, 'error': str(e)}


# if __name__ == "__main__":
#     print("\n" + "="*100)
#     print("ePortal Forgot Password - Email/Mobile OTP (FINAL VERSION)")
#     print("="*100)

#     pan = input("Enter PAN: ").strip()

#     if not pan:
#         print("PAN required!")
#         exit(1)

#     service = ePortalForgotPassword(pan)

#     result = service.step1_submit_pan()
#     if not result['success']:
#         print(f"❌ Step 1 Failed: {result['error']}")
#         exit(1)

#     print("\n" + "="*100)
#     print("OTP Validation Options:")
#     print("="*100)
#     print("1. Email/MOBILE OTP")
#     print("2. Adhaar OTP")

#     choice = input("\nSelect method (1/2): ").strip()

#     if choice == "1":
#         result = service.request_otp_email_mobile(dob="1993-08-09")

#         if not result['success']:
#             print(f"❌ OTP Request Failed: {result['error']}")
#             exit(1)
#         else:
#             print("✅ OTP sent successfully to Email/Mobile")
#             mobile_otp = input("Enter Mobile OTP: ").strip()
#             email_otp = input("Enter Email OTP: ").strip()
#             new_password = input("Enter New Password: ").strip()

#             result = service.validate_and_set_password_email_mobile(mobile_otp, email_otp, new_password)

#             print(result)

#             if not result['success']:
#                 print(f"❌ OTP Validation Failed: {result['error']}")
#                 exit(1)
#             else:
#                 print("✅ Password reset successfully via Email/Mobile OTP")

#     elif choice == "2":
#         result = service.step2_request_otp_aadhaar()



#         if not result['success']:
#             print(f"❌ OTP Request Failed: {result['error']}")
#             exit(1)
#         else:
#             print("✅ OTP sent successfully to Email/Mobile")
#             mobile_otp = input("Enter Mobile OTP: ").strip()

#             new_password = input("Enter New Password: ").strip()

#             result = service.step3_verify_otp_aadhar(mobile_otp)



#             if not result['success']:

#                 print(f"❌ OTP Validation Failed: {result['error']}")
#                 exit(1)
#             else:
#                 print("✅ OTP validation successful via Aadhar OTP")

#             result = service.set_password_aadhar(new_password)
#             if not result['success']:
#                 print(f"❌ Password Reset Failed: {result['error']}")
#                 exit(1)
#             else:
#                 print("✅ Password reset successfully via Aadhar OTP")
