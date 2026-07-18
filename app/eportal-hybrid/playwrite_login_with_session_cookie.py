STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', {
  get: () => undefined
});

// Spoof plugins
Object.defineProperty(navigator, 'plugins', {
  get: () => [1,2,3,4,5],
});

// Spoof languages
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en'],
});

// Spoof Chrome object
window.chrome = {
  runtime: {},
};

// Permissions fix
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters)
);

// WebGL fingerprint patch
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
  if (parameter === 37445) return 'Intel Inc.';
  if (parameter === 37446) return 'Intel Iris OpenGL';
  return getParameter(parameter);
};
"""

from email.mime import base
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import random
import time
import json
import os
import threading
from http.cookies import SimpleCookie
from requests.utils import dict_from_cookiejar

_PROXY_POOL = tuple(
    proxy.strip()
    for proxy in os.getenv("EGRESS_PROXY_POOL", "").split(",")
    if proxy.strip()
)
_PROXY_MAX_ACTIVE = int(os.getenv("EGRESS_MAX_ACTIVE", "5"))
_PROXY_WAIT_SECONDS = float(os.getenv("EGRESS_SLOT_WAIT_SECONDS", "90"))
_proxy_active = {proxy: 0 for proxy in _PROXY_POOL}
_proxy_condition = threading.Condition()


def _acquire_proxy():
    if not _PROXY_POOL:
        return None
    deadline = time.monotonic() + _PROXY_WAIT_SECONDS
    with _proxy_condition:
        while True:
            proxy = min(_PROXY_POOL, key=lambda item: (_proxy_active[item], _PROXY_POOL.index(item)))
            if _proxy_active[proxy] < _PROXY_MAX_ACTIVE:
                _proxy_active[proxy] += 1
                return proxy
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("All outgoing Registration IP slots are busy")
            _proxy_condition.wait(remaining)


def _release_proxy(proxy):
    if not proxy:
        return
    with _proxy_condition:
        _proxy_active[proxy] = max(0, _proxy_active.get(proxy, 0) - 1)
        _proxy_condition.notify()


def proxy_slot_status():
    with _proxy_condition:
        return {
            proxy: {"active": _proxy_active[proxy], "limit": _PROXY_MAX_ACTIVE}
            for proxy in _PROXY_POOL
        }

# -----------------------------
#  STEALTH PATCH
# -----------------------------
STEALTH_JS = """ 
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Spoof plugins
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });

// Spoof languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

// Chrome object spoof
window.chrome = { runtime: {} };

// Permissions fix
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters);
"""


class EPortalLoginStealth:
    def __init__(self, userData, retries=3, speed: float = 1.0, browser_type: str = None):
        """Create a stealth login helper.

        speed: multiplier for how fast human-like delays should be.
        browser_type: 'firefox', 'chromium', or 'webkit'. Default is 'firefox'.
        """
        self.userData = userData
        self.retries = retries
        self.speed = max(0.25, float(speed))
        self.page = None
        self.context = None
        self.playwright = None
        self.browser = None
        self.proxy_server = _acquire_proxy()
        self._proxy_released = False
        self.url = "https://eportal.incometax.gov.in/iec/foservices/#/login"
        # Browser selection: argument > env > default
        self.browser_type = (browser_type or os.getenv("PLAYWRIGHT_BROWSER", "firefox")).lower()

    def delay(self, a=0.08, b=0.18):
        """Human-like delay scaled by self.speed (higher -> faster)."""
        # divide by speed to make higher speed values reduce wait time
        actual = random.uniform(a, b) / self.speed
        time.sleep(actual)

    def type_like_human(self, locator, text):
        """Type characters with small per-key delays scaled by speed."""
        for ch in text:
            # per-key delay in ms; divide by speed to make typing faster when requested
            per_key_ms = int(random.uniform(8, 20) / self.speed)
            # Playwright's locator.type expects delay in milliseconds per char
            locator.type(ch, delay=per_key_ms)
        # small pause after typing
        self.delay(a=0.04, b=0.08)

    def init_browser(self):
        self.playwright = sync_playwright().start()

        # Multi-browser support
        browser_type = self.browser_type
        if browser_type == "firefox":
            self.browser = self.playwright.firefox.launch(
                headless=True,
                proxy={"server": self.proxy_server} if self.proxy_server else None,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--start-maximized"
                ]
            )
        elif browser_type == "webkit":
            self.browser = self.playwright.webkit.launch(
                headless=True,
                proxy={"server": self.proxy_server} if self.proxy_server else None,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--start-maximized"
                ]
            )
        else:  # Default to chromium if not firefox/webkit
            self.browser = self.playwright.chromium.launch(
                headless=True,
                proxy={"server": self.proxy_server} if self.proxy_server else None,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--start-maximized"
                ]
            )

        self.context = self.browser.new_context(
            viewport={"width": 1500, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        self.page = self.context.new_page()
        self.page.add_init_script(STEALTH_JS)

    def close(self):
        """Close the Playwright browser resources owned by this helper."""
        for obj in (self.context, self.browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
        if not self._proxy_released:
            _release_proxy(self.proxy_server)
            self._proxy_released = True

    def login(self):
        try:
            if not self.page:
                self.init_browser()

            # navigate with a shorter timeout; still wait for networkidle but cap wait
            self.page.goto(self.url, wait_until="domcontentloaded", timeout=15000)
            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                # continue even if networkidle not reached within timeout
                pass

            # -------------------------
            #  ENTER PAN
            # -------------------------
            pan = self.page.locator("#panAdhaarUserId")
            pan.wait_for(timeout=15000)
            self.type_like_human(pan, self.userData["PAN"])

            self.delay()
            self.page.locator(".marTop16").click()
            # shorter post-click pause
            self.delay(a=0.06, b=0.12)

            # # -------------------------
            # #  SELECT PASSWORD LOGIN
            # # -------------------------
            # try:
            #     self.delay(0.5, 1.5)
            #     radios = self.page.locator(".mdc-radio__native-control").all()
            #     if len(radios) > 1:
            #         radios[1].click()
            #         self.delay()
            # except:
            #     pass

            # -------------------------
            #  CHECK PASSWORD CHECKBOX
            # -------------------------
            self.delay(0.5, 1.5)
            checkbox = self.page.locator("#passwordCheckBox-input")
            checkbox.wait_for(timeout=8000)
            if not checkbox.is_checked():
                checkbox.click()

            self.delay()

            # -------------------------
            #  ENTER PASSWORD
            # -------------------------
            try:
                pwd = self.page.locator("#loginPasswordField")
                pwd.wait_for(timeout=15000)
            except PlaywrightTimeoutError:
                radios = self.page.locator(".mat-radio-outer-circle").all()
                if len(radios) > 1:
                    radios[1].click()
                pwd = self.page.locator("#loginPasswordField")
                pwd.wait_for(timeout=15000)

            self.type_like_human(pwd, self.userData["PASSWORD"])
            self.delay()

            # -------------------------
            #  CLICK LOGIN
            # -------------------------
            login_btn = self.page.locator("button.large-button-primary")
            login_btn.wait_for(timeout=15000)
            
            login_btn.click()

            # small delay after click
            self.delay(a=0.06, b=0.12)

            # Retry authentication error
            try:
                err = self.page.locator("mat-error").inner_text()
                if "not authenticated" in err.lower():
                    for i in range(self.retries):
                        login_btn.click()
                        # faster retry gap
                        self.delay(a=1, b=1.5)
            except:
                pass

            # Handle multi-session popups
            try:
                self.delay(1.0, 2.0) 
                ms = self.page.locator(".primaryBtnMargin")
                ms.wait_for(timeout=1500)
                ms.click()
            except:
                pass

            # -------------------------
            # CONFIRM LOGIN SUCCESS
            # -------------------------
            self.page.locator(".userNameVal").wait_for(timeout=15000)

            cookies = self.context.cookies()

            print("🎉 Login successful!")
            print(f"🍪 Retrieved {len(cookies)} cookies from session.")
            for c in cookies:
                  print(f" - {c['name']}: {c['value']}")
            # ensure User-Agent is a string (not the context object)
            try:
                user_agent = self.user_agent
            except AttributeError:
                # fallback: ask the page for navigator.userAgent
                try:
                    user_agent = self.page.evaluate("() => navigator.userAgent")
                except:
                    user_agent = ""

            # Build Cookie header from Playwright cookies (preserve order as returned)
            cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies]) if cookies else ""

            headers = {
                "User-Agent": user_agent,
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "Connection": "keep-alive",
                "Origin": "https://eportal.incometax.gov.in",
                "Referer": "https://eportal.incometax.gov.in/iec/foservices/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                # include Cookie header explicitly to match browser requests
                "Cookie": cookie_header,
            }

            return {
                "success": True,
                "cookies": cookies,
                "headers": headers,
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_register(self):
        """Open the registration (pre-login) page and return cookies+headers for API use.

        This mirrors `login()` but navigates to the register URL so you can perform
        registration-related API calls using the same session cookies/headers.
        """
        try:
            if not self.page:
                self.init_browser()

            register_url = "https://eportal.incometax.gov.in/iec/foservices/#/pre-login/register"

            # Navigate to the registration page
            self.page.goto(register_url, wait_until="domcontentloaded", timeout=15000)
            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # small human-like pause
            time.sleep(0.12 / self.speed)

            cookies = self.context.cookies()

            # get a reliable user agent string from the page
            try:
                user_agent = self.page.evaluate("() => navigator.userAgent")
            except Exception:
                user_agent = ""

            cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies]) if cookies else ""

            headers = {
                "User-Agent": user_agent,
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "Connection": "keep-alive",
                "Origin": "https://eportal.incometax.gov.in",
                "Referer": "https://eportal.incometax.gov.in/iec/foservices/",
                "host": "eportal.incometax.gov.in",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "Cookie": cookie_header,
            }

            return {"success": True, "cookies": cookies, "headers": headers}

        except Exception as e:
            return {"success": False, "error": str(e)}

        
import requests

class EPortalAPISession:
    def __init__(self, cookies, headers):
        self.session = requests.Session()
        self.active_fillings=[]

        
        self.session.headers.update(headers)

        for c in cookies:
            self.session.cookies.set(c["name"], c["value"])

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
        resp = self.session.post(url, json=json_data)
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

        set_cookie = response.headers.get('Set-Cookie')
        if not set_cookie:
            return

        cookie = SimpleCookie()
        # requests may combine multiple Set-Cookie values into a single header string;
        # SimpleCookie can parse multiple cookies when loaded from that string.
        cookie.load(set_cookie)

        for name, morsel in cookie.items():
            value = morsel.value
            domain = morsel['domain'] if morsel['domain'] else None
            path = morsel['path'] if morsel['path'] else None
            secure = True if morsel['secure'] else False
            try:
                # preserve domain/path when present
                if domain or path:
                    self.session.cookies.set(name, value, domain=domain, path=path, secure=secure)
                else:
                    self.session.cookies.set(name, value)
            except Exception:
                # fallback to simple set
                self.session.cookies.set(name, value)

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

        response = self.post(url, json_data=payload,sn="")
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
        response = self.post(url, json_data=payload, sn="") 

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
        response = self.post(step_1_url, json_data=payload,sn="eVerifyReturnPostLoginService")

        #if fails return the response code 
        
        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}", "raw_text": response.text}
        


        try:
            data = response.json()
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
        response = self.post(step_1_url, json_data=payload,sn="everifyReturnPostLoginRevisedValidation")

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


        response = self.post(url, json_data=payload,sn="verifyOtpUsingAadhar")

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
        
        response = self.post(url, json_data=payload, sn="verifyOtpUsingAadhar")
        
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


