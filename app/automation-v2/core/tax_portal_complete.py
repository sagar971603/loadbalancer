# Playwright version of TaxPortalAutomation + AES Download
# COMPLETE NETWORK MONITORING for ALL UI ACTIONS + OTP + PASSWORD

import os
import time
import uuid
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import tempfile
import shutil
import platform

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    filename="error.log",
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
uuid_str = str(uuid.uuid4())
NETWORK_DIR = os.path.join(os.path.dirname(__file__))
os.makedirs(NETWORK_DIR, exist_ok=True,mode=755)

class NetworkMonitor:
    """Complete network monitoring for request/response capture (Selenium version)"""
    def __init__(self, driver):
        self.driver = driver
        self.captured_data: List[Dict] = []
        self._patch_network_capture()

    def _patch_network_capture(self):
        # Inject JS to hook XHR and fetch, and send data to window._networkMonitorLog (exclude static assets)
        js_patch = """
        if (!window._networkMonitorLog) {
            window._networkMonitorLog = [];
            (function() {
                var isStatic = function(url) {
                    return /\.(css|js|jpg|jpeg|png|gif|svg|ico|pdf|woff|woff2|ttf|eot|json)(\\?|$)/i.test(url);
                };
                var origOpen = XMLHttpRequest.prototype.open;
                var origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url) {
                    this._url = url;
                    this._method = method;
                    return origOpen.apply(this, arguments);
                };
                XMLHttpRequest.prototype.send = function(body) {
                    var xhr = this;
                    var url = xhr._url || "";
                    var method = xhr._method || "";
                    if (!isStatic(url)) {
                        xhr.addEventListener('loadend', function() {
                            try {
                                var respText = xhr.responseText;
                                window._networkMonitorLog.push({
                                    timestamp: new Date().toISOString(),
                                    url: url,
                                    method: method,
                                    status: xhr.status,
                                    response: respText
                                });
                                // Limit log size
                                if (window._networkMonitorLog.length > 120) {
                                    window._networkMonitorLog = window._networkMonitorLog.slice(-100);
                                }
                            } catch(e) {}
                        });
                    }
                    return origSend.apply(this, arguments);
                };
                XMLHttpRequest.prototype._networkMonitorPatched = true;
            })();
        }
        """
        try:
            self.driver.execute_script(js_patch)
        except Exception:
            pass

    def _collect_browser_log(self):
        # Pull all captured network logs from browser and merge
        try:
            logs = self.driver.execute_script("return window._networkMonitorLog || []")
            self.driver.execute_script("window._networkMonitorLog = []")
            for entry in logs:
                # Parse response body if JSON-like
                try:
                    resp_body = json.loads(entry.get('response') or "")
                except Exception:
                    resp_body = entry.get('response')
                self.captured_data.append({
                    'timestamp': entry.get('timestamp'),
                    'url': entry.get('url', ''),
                    'method': entry.get('method', 'GET'),
                    'response': {
                        'status': entry.get('status', 0),
                        'body': resp_body
                    }
                })
        except Exception:
            pass

    def _is_business_api(self, entry: Dict) -> bool:
        try:
            url = (entry.get('url') or '').lower()
            body = entry.get('response', {}).get('body')
            # Match Playwright logic: registration APIs and OTP endpoints OR body with 'messages'
            if 'registrationapi' in url or 'validateotp' in url or 'setpassword' in url or 'saveentity' in url:
                return True
            if isinstance(body, dict) and 'messages' in body:
                return True
        except Exception:
            pass
        return False

    def get_all_data(self) -> List[Dict]:
        self._collect_browser_log()
        return self.captured_data

    def get_api_data(self) -> List[Dict]:
        self._collect_browser_log()
        return [d for d in self.captured_data if 'registrationapi' in (d.get('url') or '').lower()]

    def get_business_api_data(self) -> List[Dict]:
        self._collect_browser_log()
        return [d for d in self.captured_data if self._is_business_api(d)]

    def get_otp_data(self) -> List[Dict]:
        self._collect_browser_log()
        return [d for d in self.captured_data if 'validateotp' in (d.get('url') or '').lower()]

    def get_register_data(self) -> List[Dict]:
        self._collect_browser_log()
        return [d for d in self.captured_data if 'register' in (d.get('url') or '').lower()]

    def get_endpoint_data(self, endpoint: str) -> List[Dict]:
        self._collect_browser_log()
        return [d for d in self.captured_data if endpoint.lower() in (d.get('url') or '').lower()]

    def wait_for_business_api(self, since_count: int, timeout_sec: float = 12.0, poll_sec: float = 0.4) -> Optional[Dict]:
        """Wait for a new 'business API' entry since a given total captured count, similar to Playwright's networkidle usage."""
        deadline = time.time() + timeout_sec
        last_len = len(self.get_all_data())
        while time.time() < deadline:
            self._collect_browser_log()
            all_len = len(self.captured_data)
            if all_len > since_count or all_len > last_len:
                biz = self.get_business_api_data()
                if biz:
                    return biz[-1]
                last_len = all_len
            time.sleep(poll_sec)
        return None

    def print_summary(self):
        biz = self.get_business_api_data()
        otp_data = self.get_otp_data()
        register_data = self.get_register_data()
        print(f"\n{'='*80}")
        print("NETWORK MONITORING SUMMARY")
        print(f"{'='*80}")
        print(f"Total Requests Captured: {len(self.captured_data)}")
        print(f"API Calls: {len(biz)}")
        print(f"OTP Requests: {len(otp_data)}")
        print(f"Register Requests: {len(register_data)}")
        print(f"{'='*80}\n")
        if otp_data:
            print("OTP Request Details:")
            for otp in otp_data:
                print(f"  Status: {otp['response'].get('status', '')}")
                print(f"  URL: {otp['url']}")
                print(f"  Response: {otp['response'].get('body', '')}\n")

class TaxPortalRegistrationSelenium:
    def __init__(self, userData: dict, resetTime: int = 240):
        self.userData = userData
        self.resetInterval = resetTime
        self.registerUrl = "https://eportal.incometax.gov.in/iec/foservices/#/pre-login/register"
        self.uid = str(uuid.uuid4())
        self.sessionStartTime = time.time()
        self.driver = None
        self.network_monitor: Optional[NetworkMonitor] = None
        self.reg_with_uidai = False
        self.profile_dir = None

    def initialize(self):
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--start-maximized")
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

        is_windows = platform.system().lower() == "windows"
        if is_windows:
            self.profile_dir = tempfile.mkdtemp(prefix="chrome-profile-")
            options.add_argument(f"--user-data-dir={self.profile_dir}")
        else:
            self.profile_dir = None  # avoid profile lock issues on Linux

        try:
            self.driver = webdriver.Chrome(options=options)
        except Exception as e:
            logger.warning(f"Chrome failed (profile:{self.profile_dir}), retrying without user-data-dir: {e}")
            self.profile_dir = None
            options = webdriver.ChromeOptions()
            options.add_argument("--start-maximized")
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
            self.driver = webdriver.Chrome(options=options)

        self.network_monitor = NetworkMonitor(self.driver)
        logger.info("✅ Selenium initialized")

    def close(self):
        if self.network_monitor:
            self.network_monitor.print_summary()
        if self.driver:
            self.driver.quit()
        if self.profile_dir:
            shutil.rmtree(self.profile_dir, ignore_errors=True)
        logger.info("✅ Selenium closed - network data kept in memory")

    def wait_for_page_load(self, timeout: int = 10):
        WebDriverWait(self.driver, timeout).until(
            lambda driver: driver.execute_script('return document.readyState') == 'complete'
        )

    def wait_for_invisibility(self, selector: str, timeout: int = 20):
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, selector))
            )
        except Exception:
            pass

    def registerPanValidate(self):
        try:
            logger.info(f"📝 Step 1: PAN Validation for PAN: {self.userData['PAN']}")
            self.driver.get(self.registerUrl)
            self.wait_for_page_load()
            # Re-inject network hooks after navigation
            self.network_monitor._patch_network_capture()

            toggle_btn = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.ID, 'mat-button-toggle-0'))
            )
            toggle_btn.click()


            pan_input = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[class*="mat-mdc-input-element"]'))
            )
            # pan_input.clear()
            pan_input.send_keys(self.userData['PAN'])

            validate_btn = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'button[class*="w98"]'))
            )
            # prev_total = len(self.network_monitor.get_all_data())
            validate_btn.click()

            time.sleep(.5)

            api_response = self.network_monitor.get_api_data()[-1]
            if not api_response:
                return {"success": False, "response": "No network data captured"}

            if api_response["response"].get("status") != 200:
                return {"success": False, "data": api_response["response"].get("body")}

            messages = api_response["response"]["body"].get("messages", [])
            for message in messages:
                if message.get('code') in ["EF00001", "EF00049"]:
                    logger.info(f"📝 Step 2: Select Registration Type")
                    radio_buttons = self.driver.find_elements(By.CLASS_NAME, 'mdc-radio__native-control')
                    radio_buttons[0].click()
                    time.sleep(0.8)
                    next_btn = self.driver.find_element(By.CSS_SELECTOR, 'button[class*="large-button-primary"]')
                    next_btn.click()
                    return {"success": True, "response": api_response["response"]["body"]}


            return {"success": False, "response": api_response["response"]["body"]}
        except Exception as e:
            logger.error(f"Error in PAN validation: {str(e)}")
            return {"success": False, "response": f"unexpected error occured with exception {e}"}

    def registerSecondStep(self, register_type: int = 1):
        try:
            # prev_total = len(self.network_monitor.get_all_data())
            # # More robust wait (align with Playwright): wait longer and fallback to last captured API
            api_response = self.network_monitor.get_api_data()[-1]
            if not api_response:
                return {"success": False, "response": "No network data captured"}

            if api_response["response"].get("status") != 200:
                return {"success": False, "data": api_response["response"].get("body")}

            messages = api_response["response"]["body"].get("messages", [])
            for message in messages:
                if message.get('code') in ["EF00001", "EF00049"]:
                    return {"success": True, "response": api_response["response"]["body"]}
            return {"success": False, "response": api_response["response"]["body"]}
        except Exception as e:
            logger.error(f"Error in second step: {str(e)}")
            return {"success": False, "response": f"unexpected error occured with exception {e}"}

    def registerUidaiStep(self):
        """Step 3: UIDAI verification (replica of Playwright logic)"""
        try:
            logger.info(f"📝 Step 3: UIDAI Verification")
            # self.wait_for_page_load()
            time.sleep(0.6)
            checkbox = None
            try:
                checkbox = self.driver.find_element(By.CSS_SELECTOR, ".mdc-checkbox__native-control")
            except Exception:
                pass
            if not checkbox:
                return {"success": True, "uidai": False}

            self.reg_with_uidai = True
            checkbox.click()
            time.sleep(.5)

            prev_total = len(self.network_monitor.get_all_data())
            try:
                sso_button = self.driver.find_element(By.ID, "checkBoxId")
                sso_button.click()
            except Exception:
                pass

            api_response = self.network_monitor.wait_for_business_api(prev_total, timeout_sec=12)
            if not api_response:
                return {"success": False, "response": "No network data captured"}

            if api_response["response"].get("status") != 200:
                return {"Uidai": True, "success": False, "data": api_response["response"].get("body")}

            messages = api_response["response"]["body"].get("messages", [])
            for message in messages:
                if message.get('desc') == "SUCCESS" or message.get("code") in ["EF00001", "EF00049"]:
                    return {"Uidai": True, "success": True, "response": api_response["response"]["body"]}
            return {"success": False, "response": api_response["response"]["body"]}
        except Exception as e:
            logger.error(f"Error in UIDAI step: {str(e)}")
            return {"success": False, "error": f"unexpected error occured with exception {e}"}

    def registerThirdStep(self):
        """Step 4: Personal Details"""
        try:
            logger.info(f"📝 Step 4: Personal Details")
            time.sleep(0.8)
            self.wait_for_invisibility('.customLoaderBackdrop')
            self.driver.find_element(By.ID, 'lastName').send_keys(self.userData["LASTNAME"])
            if self.userData.get("FIRSTNAME"):
                self.driver.find_element(By.ID, 'firstName').send_keys(self.userData["FIRSTNAME"])
            if self.userData.get("MIDDLENAME"):
                self.driver.find_element(By.ID, 'middleName').send_keys(self.userData["MIDDLENAME"])

            self.choose_year(self.userData["DOB_YEAR"])
            self.choose_month(self.userData["DOB_MONTH"])
            self.choose_day(self.userData["DOB_DAY"])

            time.sleep(0.6)
            gender_element = 'mat-radio-4-input' if self.userData['GENDER'] == "MALE" else 'mat-radio-5-input'
            self.driver.find_element(By.ID, gender_element).click()
            res_element = 'mat-radio-2-input' if self.userData["RESIDENT"] else 'mat-radio-3-input'
            self.driver.find_element(By.ID, res_element).click()

            time.sleep(1.2)
            next_btn = self.driver.find_element(By.CSS_SELECTOR, 'button[class*="large-button-primary"]')
            prev_total = len(self.network_monitor.get_all_data())
            next_btn.click()

            api_response = self.network_monitor.wait_for_business_api(prev_total, timeout_sec=12)
            if not api_response:
                return {"success": False, "response": "No network data captured"}
            if api_response["response"].get("status") != 200:
                return {"success": False, "data": api_response["response"].get("body")}

            messages = api_response["response"]["body"].get("messages", [])
            for message in messages:
                if message.get('code') in ["EF00001", "EF00049", "EF00000"]:
                    return {"success": True, "response": api_response["response"]["body"]}
            return {"success": False, "response": api_response["response"]["body"]}
        except Exception as e:
            logger.error(f"Error in personal details: {str(e)}")
            return {"success": False, "response": f"unexpected error occured with exception {e}"}

    def registerFourthStep(self):
        """Step 5: Contact Details"""
        try:
            logger.info(f"📝 Step 5: Contact Details")
            self.wait_for_page_load()
            self.driver.find_element(By.ID, 'phone').send_keys(self.userData["MOBILE"])
            time.sleep(0.6)
            selects = self.driver.find_elements(By.CLASS_NAME, 'mat-mdc-select-value')
            if len(selects) >= 2:
                selects[1].click()
                options = self.driver.find_elements(By.CLASS_NAME, 'mat-mdc-option')
                if len(options) > 3:
                    options[3].click()
            self.driver.find_element(By.ID, 'mat-input-6').send_keys(self.userData["EMAIL"])
            selects = self.driver.find_elements(By.CLASS_NAME, 'mat-mdc-select-value')
            if len(selects) >= 3:
                selects[2].click()
                options = self.driver.find_elements(By.CLASS_NAME, 'mat-mdc-option')
                if len(options) > 3:
                    options[3].click()
            self.driver.find_element(By.ID, 'mat-input-7').send_keys(self.userData['ADDRESS'])
            self.driver.find_element(By.ID, 'pincode').send_keys(self.userData['PIN'])

            time.sleep(1.2)
            for _ in range(4):
                focused = self.driver.switch_to.active_element
                focused.send_keys(Keys.TAB)
                time.sleep(0.6)
                focused = self.driver.switch_to.active_element
                focused.send_keys(Keys.ENTER)
                time.sleep(0.6)
            focused.send_keys(Keys.ENTER)
            time.sleep(1)
            next_btn = self.driver.find_element(By.CSS_SELECTOR, 'button[class*="large-button-primary"]')
            # prev_total = len(self.network_monitor.get_all_data())
            next_btn.click()

            time.sleep(2)

            api_response = self.network_monitor.get_api_data()[-1]
            if not api_response:
                return {"success": False, "response": "No network data captured"}
            if api_response["response"].get("status") != 200:
                return {"success": False, "data": api_response["response"].get("body")}

            messages = api_response["response"]["body"].get("messages", [])
            for message in messages:
                if message.get('code') in ["EF00000", "EF00049"]:
                    WebDriverWait(self.driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, "otp-input")))

                    return {"success": True, "response": api_response["response"]["body"]}
            return {"success": False, "response": api_response["response"]["body"]}
        except Exception as e:
            logger.error(f"Error in contact details: {str(e)}")
            self.driver.save_screenshot(f"contact_error_{self.uid}.png")
            return {"success": False, "response": f"unexpected error occured with exception {e}"}

    # Ensure helper methods exist (replica of Playwright)
    def choose_year(self, year: str):
        try:
            date_picker = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.CLASS_NAME, 'dtIcon')))
            date_picker.click()
            time.sleep(0.6)
            period_btn = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.CLASS_NAME, 'mat-calendar-period-button')))
            period_btn.click()
            time.sleep(0.6)
            not_selected = True
            while not_selected:
                opts = self.driver.find_elements(By.CLASS_NAME, 'mat-calendar-body-cell-content')
                for o in opts:
                    if o.text.strip() == str(year):
                        o.click()
                        not_selected = False
                        break
                if not_selected:
                    self.driver.find_element(By.CLASS_NAME, 'mat-calendar-previous-button').click()
                    time.sleep(0.4)
        except Exception as e:
            logger.error(f"Error selecting year {year}: {str(e)}")
            raise e

    def choose_month(self, month: str):
        try:
            opts = self.driver.find_elements(By.CLASS_NAME, 'mat-calendar-body-cell-content')
            for o in opts:
                if o.text.strip() == month:
                    o.click()
                    time.sleep(0.6)
                    break
        except Exception as e:
            logger.error(f"Error selecting month {month}: {str(e)}")
            raise e

    def choose_day(self, day: str):
        try:
            opts = self.driver.find_elements(By.CLASS_NAME, 'mat-calendar-body-cell-content')
            for o in opts:
                if o.text.strip() == day:
                    o.click()
                    time.sleep(0.6)
                    break
        except Exception as e:
            logger.error(f"Error selecting day {day}: {str(e)}")
            raise e

    def get_error(self, registerLast: bool = False, ignore_warning: bool = True):
        try:
            selector = '.errorfield' if registerLast else 'mat-error'
            errors = []
            error_elements = self.driver.find_elements(By.CSS_SELECTOR, selector) or self.driver.find_elements(By.CLASS_NAME, 'mat-mdc-error')
            for err in error_elements:
                try:
                    flex = err.find_element(By.CLASS_NAME, 'd-flex')
                    error_text = flex.text
                    if ignore_warning and "Error" in error_text:
                        errors.append(error_text)
                    elif not ignore_warning:
                        errors.append(error_text)
                except Exception:
                    pass
            if errors:
                return {"success": False, "status": "error", "details": " ".join(errors), "has_error": True, "uuid": self.uid}
            return {"success": True, "status": "error", "details": "", "has_error": False, "uuid": self.uid}
        except Exception as e:
            logger.error(f"Error in get_error: {str(e)}")
            return {"success": False, "status": "error", "details": "Some error occurred, please try again later.", "report": str(e), "has_error": False, "uuid": self.uid}

    def check_modal_errors(self):
        try:
            error_element = None
            try:
                error_element = self.driver.find_element(By.CLASS_NAME, 'error-nomargin')
            except Exception:
                pass
            if error_element:
                try:
                    flex = error_element.find_element(By.CLASS_NAME, 'd-flex')
                    error = flex.text
                    return {"success": False, "status": "error", "details": error, "has_error": True, "uuid": self.uid}
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"No modal errors found: {str(e)}")
        return {"success": False, "status": "error", "details": "no error in portal. try after sometime", "has_error": False, "uuid": self.uid}

    def registerUser(self, register_type: int = 1):
        """Complete registration flow - Steps 1-5 (Playwright parity)"""
        try:
            logger.info(f"🚀 STARTING REGISTRATION FLOW for PAN: {self.userData['PAN']}")
            self.initialize()
            self.driver.get(self.registerUrl)
            self.wait_for_page_load()

            res = self.registerPanValidate()
            if not res.get("success"):
                return {"success": False, "response": res}

            resp = self.registerSecondStep(register_type=register_type)
            if not resp.get("success"):
                return {"success": False, "response": resp}

            status = self.registerUidaiStep()
            if not status.get("success"):
                return {"success": False, "data": status}

            if self.reg_with_uidai and status.get("success"):
                return {"success": True, "data": status, "uuid": self.uid, "message": "Otp send to the registered no with aadhar"}

            if not self.reg_with_uidai and status.get("success"):
                rsp = self.registerThirdStep()
                if not rsp.get("success"):
                    return {"success": False, "data": rsp}
                rsp = self.registerFourthStep()
                if not rsp.get("success"):
                    return {"success": False, "data": rsp, "uuid": self.uid}
                return {"success": True, "data": resp, "uuid": self.uid, "Msg": "OTP has been send to the email and mobile"}
        except Exception as e:
            logger.error(f"Error during registration: {e}")
            return {"success": False, "error": str(e), "uuid": self.uid}
        # finally:
        #     self.close()

    def registerContinue(self, mobile_otp: str = None, email_otp: str = None, adhaar_otp: str = None):
        """Complete registration with OTP/password - Steps 6-7 (Playwright parity)"""
        try:
            logger.info(f"🔄 CONTINUING REGISTRATION with OTP for PAN: {self.userData.get('PAN')}")
            if self.reg_with_uidai and adhaar_otp:
                otp_result = self.input_otp(adhaar_otp, "", register=True)
                if not otp_result.get("success"):
                    return otp_result
                rsp = self.registerThirdStep()
                if not rsp.get("success"):
                    return {"success": False, "data": rsp}
                rsp = self.registerFourthStep()
                if not rsp.get("success"):
                    return {"success": False, "data": rsp, "uuid": self.uid}
                return {"success": True, "data": rsp, "uuid": self.uid, "Msg": "OTP has been send to the email and mobile"}

            if mobile_otp and email_otp:
                otp_result = self.input_otp(mobile_otp, email_otp, register=True)
                if not otp_result.get("success"):
                    return otp_result

                # Wait for the continue button to be clickable before clicking
                try:
                    cont_btn = WebDriverWait(self.driver, 15).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[class*="large-button-primary"]'))
                    )
                    cont_btn.click()
                except Exception as e:
                    pass
                    logger.error(f"Continue button not available or not clickable: {e}")
                    # return {"success": False, "error": "Continue button not available or not clickable", "uuid": self.uid}





                pwd_result = self.set_password(register=False)
                if not pwd_result.get("success"):
                    return pwd_result

                if self.reg_with_uidai:
                    self.reg_with_uidai = False



                return {"success": True, "uuid": self.uid, "message": "Registration completed successfully"}
        except Exception as e:
            logger.error(f"Error in registerContinue: {e}")
            try:
                self.driver.save_screenshot(f"continue_error_{self.uid}.png")
            except Exception:
                pass
            return {"success": False, "error": str(e), "uuid": self.uid}

    def input_otp(self, mobile_otp: str, email_otp: str, register: bool = False):
        """Step 6: Input OTP (mobile/email)"""
        try:
            logger.info(f"🔐 Step 6: Inputting OTP for PAN: {self.userData.get('PAN')}")
            input_fields = self.driver.find_elements(By.CLASS_NAME, 'otp-input')
            for i in range(min(6, len(input_fields))):
                input_fields[i].click()
                input_fields[i].send_keys(mobile_otp[i])
                time.sleep(0.1)
            if len(email_otp) != 0:
                for i in range(6, min(12, len(input_fields))):
                    idx = i - 6
                    if idx < len(email_otp):
                        input_fields[i].click()
                        input_fields[i].send_keys(email_otp[idx])
                        time.sleep(0.1)
            time.sleep(0.4)
            try:
                # Wait for the verify button to be clickable before clicking
                btn= 'floatRight' if register else 'large-button-primary'
                verify_btn = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, btn))
                )
                verify_btn.click()
            except Exception:
                return {"success": False, "error": "Verify button not available or not clickable", "uuid": self.uid}
            time.sleep(.5)
            self.wait_for_invisibility('.customLoaderBackdrop', timeout=30)
            prev_total = len(self.network_monitor.get_all_data())
            api_response = self.network_monitor.get_otp_data()[-1]
            if not api_response:
                return {"success": False, "response": "No network data captured"}
            if api_response["response"].get("status") != 200:
                return {"success": False, "data": api_response["response"].get("body")}
            messages = api_response["response"]["body"].get("messages", [])
            for m in messages:

                if self.reg_with_uidai:

                    if  m.get("code") == "OTP VALIDATED":
                        return {"success": True, "response": api_response["response"]["body"]}




                if m.get('code') in ["EF00000", "EF00049", "EF00015"]:
                    if register:
                        try:
                            # Wait for the verify button to be clickable before clicking
                            verify_btn = WebDriverWait(self.driver, 10).until(
                                EC.element_to_be_clickable((By.CLASS_NAME, 'large-button-primary'))
                            )
                            verify_btn.click()
                        except Exception:
                            pass


                    return {"success": True, "response": api_response["response"]["body"]}
            return {"success": False, "response": api_response["response"]["body"]}
        except Exception as e:
            logger.error(f"Error in input_otp: {e}")
            return {"success": False, "error": str(e)}

    def set_password(self, register: bool = False):
        """Step 7: Set password and personal message"""
        try:
            logger.info(f"🔐 Step 7: Setting Password for PAN: {self.userData.get('PAN')}")
            pwd_element = 'setpassword'
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.ID, pwd_element)))
            self.driver.find_element(By.ID, pwd_element).send_keys(self.userData['PASSWORD'])
            time.sleep(0.3)
            self.driver.find_element(By.ID, 'confirmPassword').send_keys(self.userData['CONFIRMPWD'])
            time.sleep(0.3)
            self.driver.find_element(By.ID, 'personalmessage').send_keys(self.userData['PERMSG'])
            time.sleep(0.8)
            prev_total = len(self.network_monitor.get_all_data())
            self.driver.find_element(By.CLASS_NAME, 'large-button-primary').click()
            time.sleep(1.5)
            self.wait_for_invisibility('.customLoaderBackdrop', timeout=30)
            api_response = self.network_monitor.wait_for_business_api(prev_total, timeout_sec=8)
            if not api_response:
                return {"success": False, "response": "No network data captured"}
            if api_response["response"].get("status") != 200:
                return {"success": False, "data": api_response["response"].get("body")}
            messages = api_response["response"]["body"].get("messages", [])
            for m in messages:
                if m.get('code') in ["EF00001", "EF00049", "EF00000", "EF00015"]:
                    return {"success": True, "response": api_response["response"]["body"]}
            return {"success": False, "response": api_response["response"]["body"]}
        except Exception as e:
            logger.error(f"Error in set_password: {e}")
            try:
                self.driver.save_screenshot(f"password_error_{self.uid}.png")
            except Exception:
                pass
            return {"success": False, "error": str(e), "uuid": self.uid}

# def main():
#     """Entry point mirroring the Playwright script"""
#     user_data = {
#         "PAN": "ABCDE1234F",
#         "LASTNAME": "LAD",
#         "FIRSTNAME": "ANJALI",
#         "MIDDLENAME": "SATISH",
#         "DOB_YEAR": "1981",
#         "DOB_MONTH": "JUL",
#         "DOB_DAY": "6",
#         "GENDER": "FEMALE",
#         "RESIDENT": "true",
#         "MOBILE": "9086025119",
#         "EMAIL": "pwpattayalisting@gmail.com",
#         "ADDRESS": "VIRAR",
#         "PIN": "401305",
#         "PASSWORD": "your-password-here",
#         "CONFIRMPWD": "your-password-here",
#         "PERMSG": "easyreturn.co.in"
#     }

#     automation = TaxPortalRegistrationSelenium(user_data)
#     result = automation.registerUser()
#     print("\nRegistration Result:")
#     print(json.dumps(result, indent=2))
#     if not result.get("success"):
#         return{"success": False, "response": result
#                }

#     mobile_otp = input("Enter Mobile OTP (6 digits): ")
#     email_otp = input("Enter Email OTP (6 digits): ")
#     result_continue = automation.registerContinue(mobile_otp, email_otp)
#     print("\nContinue Registration Result:")
#     print(json.dumps(result_continue, indent=2))


# if __name__ == "__main__":
#     main()
