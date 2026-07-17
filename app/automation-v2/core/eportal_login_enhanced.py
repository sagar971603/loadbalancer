"""
ePortal Login - Session Manager

A robust, production-ready client for ePortal login and API operations.
Handles authentication, session management, cookie propagation, and API calls
with automatic retries and comprehensive error handling.

Author: Sundeep Singh
Version: 2.0.0
"""
# ...existing code...

import logging
import json
     
from urllib.parse import urlencode

import base64
import time
from typing import Dict, List, Optional, Any, Union, Tuple
from dataclasses import dataclass, field
from enum import Enum
import re
from http.cookies import SimpleCookie

# HTTP client
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading
import os
from datetime import datetime

# curl_cffi for TLS fingerprint impersonation (AIS WAF bypass)
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
    print("[DEBUG] curl_cffi is available, AIS requests will use curl_cffi for TLS impersonation")
except ImportError:
    print("[DEBUG] curl_cffi not available, AIS requests will use standard requests library")
    HAS_CURL_CFFI = False
    cffi_requests = None

# ...existing code...
# Conditional curl_cffi support



# ============================================================================
# Logging Configuration
# ============================================================================

logging.basicConfig(
    filename='eportal_stealth_session.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

_PROXY_POOL = tuple(
    proxy.strip()
    for proxy in os.getenv("EGRESS_PROXY_POOL", "").split(",")
    if proxy.strip()
)
_proxy_index = 0
_proxy_lock = threading.Lock()
_PROXY_UNSET = object()


def _next_proxy() -> Optional[str]:
    global _proxy_index
    if not _PROXY_POOL:
        return None
    with _proxy_lock:
        proxy = _PROXY_POOL[_proxy_index % len(_PROXY_POOL)]
        _proxy_index += 1
        return proxy


# ============================================================================
# Custom Exceptions
# ============================================================================

class EPortalError(Exception):
    """Base exception for ePortal operations."""
    pass


class AuthenticationError(EPortalError):
    """Raised when authentication fails."""
    pass


class SessionError(EPortalError):
    """Raised when session-related operations fail."""
    pass


class APIError(EPortalError):
    """Raised when API calls fail."""
    
    def __init__(self, message: str, status_code: Optional[int] = None, 
                 response_data: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class NetworkError(EPortalError):
    """Raised when network operations fail after retries."""
    pass


# ============================================================================
# Enumerations
# ============================================================================

class LoginStep(Enum):
    """Login flow steps."""
    SUBMIT_PAN = 1
    VERIFY_PASSWORD = 2
    OPTION_LOGIN = 3


class MessageCode(Enum):
    """Common ePortal message codes."""
    SUCCESS = "EF00000"
    ENTITY_SUCCESS = "EF40003"
    EXISTING_SESSION = "EF00177"
    AUTHENTICATION_ERROR = "EF500023"
    EVERIFY_SUCCESS = "EF40003"
    AADHAAR_LINKED = "AADHAR_PAN_LINKAGE_CONSTANT"
    VALID_AADHAAR_NUMBER = "VALID_AADHAAR_NUMBER"
    AADHAAR_LINK_VALIDATION_SUCCESS = "EF40126"  # add this


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class EPortalConfig:
    """Configuration settings for ePortal client."""
    
    # Base URL
    base_url: str = "https://eportal.incometax.gov.in"
    
    # Retry configuration
    max_retries: int = 3
    backoff_factor: float = 2.0
    initial_delay: float = 1.0
    timeout: int = 30
    
    # HTTP Headers
    user_agent: str = 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36'
    accept: str = 'application/json, text/plain, */*'
    accept_encoding: str = 'gzip, deflate, br, zstd'
    accept_language: str = 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7'
    content_type: str = 'application/json'
    referer: str = 'https://eportal.incometax.gov.in/iec/foservices/'
    
    # Service names
    login_service: str = "loginService"
    wlogin_service: str = "wLoginService"
    
    # Endpoints
    login_endpoint: str = "/iec/loginapi/login"
    get_entity_endpoint: str = "/iec/servicesapi/auth/getEntity"
    save_entity_endpoint: str = "/iec/efileprocessingapi/auth/saveEntity"
    verification_endpoint: str = "/iec/verificationservices/auth/getEntity"
    download_endpoint: str = "/iec/itrweb/auth/v0.1/returns/downloadfile"
    dsc_config_endpoint: str = "/iec/dscservices/auth/saveEntity"
    verification_endpoint_save:str ="/iec/verificationservices/auth/saveEntity"
    everify_otp_validate_endpoint: str = "/iec/verificationservices/auth/validateOTP"
    challan_history_endpoint: str = "/iec/paymentapi/auth/challan/paymenthistory"
    challan_download_endpoint: str = "/iec/paymentapi/auth/challan/paymentdetails"
    aes_redirection_endpoint : str = "/iec/utilityservicesapi/auth/v0.1/redirectionView26AS" 
    ais_redirection_endpoint : str = "/iec/servicesapi/auth/saveEntity"


# ======


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class UserCredentials:
    """User authentication credentials."""
    pan: str
    password: str
    
    def __post_init__(self):
        """Validate credentials on initialization."""
        if not self.pan or not self.password:
            raise ValueError("PAN and password are required")
        self.pan = self.pan.upper().strip()


@dataclass
class AuthState:
    """Authentication session state."""
    req_id: Optional[str] = None
    entity: Optional[str] = None
    entity_type: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    client_ip: Optional[str] = None
    mobile_no: Optional[str] = None
    uid_valdtn_flg: Optional[bool] = None
    aadhaar_mobile_validated: Optional[str] = None
    sec_accs_msg: Optional[str] = None
    sec_login_options: Optional[str] = None
    dto_service: Optional[str] = None
    exempted_pan: Optional[bool] = None
    user_consent: Optional[str] = None
    img_byte: Optional[str] = None
    errors: List[Dict[str, Any]] = field(default_factory=list)
    user_type: Optional[str] = None
    
    def is_authenticated(self) -> bool:
        """Check if authentication state is valid."""
        return self.req_id is not None and self.entity is not None


@dataclass
class APIResponse:
    """Standardized API response wrapper."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    messages: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None
    status_code: Optional[int] = None
    
    def has_code(self, code: str) -> bool:
        """Check if response contains a specific message code."""
        return any(msg.get('code') == code for msg in self.messages)
    
    def get_message_by_code(self, code: str) -> Optional[Dict[str, str]]:
        """Retrieve message by code."""
        for msg in self.messages:
            if msg.get('code') == code:
                return msg
        return None


# ============================================================================
# Main Client Class
# ============================================================================

class EPortalClient:
    """
    Professional ePortal client with session management.
    
    Provides authentication, session handling, and API methods for
    interacting with the Income Tax ePortal.
    
    Features:
        - Automatic cookie and session management
        - Retry logic with exponential backoff
        - Comprehensive error handling
        - Type-safe API methods
        - Structured logging
        
    Example:
        >>> credentials = UserCredentials(pan="ABCDE1234F", password="secret")
        >>> client = EPortalClient(credentials)
        >>> result = client.login()
        >>> if result['success']:
        ...     filings = client.get_active_verify_filings(credentials.pan)
    """
    
    def __init__(self, credentials: Union[UserCredentials, Dict[str, str]],
                 config: Optional[EPortalConfig] = None,
                 proxy_url: Optional[str] = _PROXY_UNSET):
        """Initialize ePortal client.
        
        Args:
            credentials: User credentials (UserCredentials object or dict with PAN/PASSWORD)
            config: Optional configuration override
            
        Raises:
            ValueError: If required credentials are missing
        """
        # Handle both dict and UserCredentials input
        if isinstance(credentials, dict):
            self.credentials = UserCredentials(
                pan=credentials.get("PAN", ""),
                password=credentials.get("PASSWORD", "")
            )
        else:
            self.credentials = credentials
        
        self.config = config or EPortalConfig()
        self.auth_state = AuthState()
        self.proxy_url = _next_proxy() if proxy_url is _PROXY_UNSET else proxy_url
        self.proxies = (
            {"http": self.proxy_url, "https": self.proxy_url}
            if self.proxy_url else {}
        )

        # HTTP session
        self.session = self._create_session()

        # AIS session
        self.ais_session = self._create_ais_session()
        self.ais_access_token: Optional[str] = None
        self.ais_access_url: Optional[str] = None
        self.ais_final_url: Optional[str] = None

        
        # Active filings cache
        self.active_fillings: List[Dict[str, Any]] = []

        self.aadhaar_transaction_id: Optional[str] = None

        self.payments: List[Dict[str, Any]] = []

        
        logger.info(f"EPortalClient initialized for PAN: {self.credentials.pan[:4]}****")
    def _create_ais_session(self) -> requests.Session:
        """Create a clean AIS session. Do not inherit ePortal origin/fetch defaults."""
        session = requests.Session()
        session.proxies.update(self.proxies)

        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        session.headers.update({
            "User-Agent": self.config.user_agent,
            "Accept-Encoding": self.config.accept_encoding,
            "Accept-Language": self.config.accept_language,
            "Connection": "keep-alive",
        })
        return session
    # ...existing code...

    def _copy_cookies_to_ais_session(self) -> None:
        """
        Copy ALL ePortal cookies into AIS session jar.
        Seeds them for both original domain AND ais.insight.gov.in.
        Clears AIS jar first to avoid stale cookies.
        Also builds a flat dict for curl_cffi usage.
        """
        try:
            self.ais_session.cookies.clear()
            self._ais_cookie_dict: Dict[str, str] = {}

            for cookie in self.session.cookies:
                # Keep original domain cookie
                try:
                    self.ais_session.cookies.set(
                        cookie.name,
                        cookie.value,
                        domain=cookie.domain,
                        path=cookie.path or "/",
                        secure=cookie.secure,
                        expires=cookie.expires,
                    )
                except Exception:
                    self.ais_session.cookies.set(cookie.name, cookie.value)

                # Also seed for AIS domain
                try:
                    self.ais_session.cookies.set(
                        cookie.name,
                        cookie.value,
                        domain="ais.insight.gov.in",
                        path="/",
                        secure=True,
                    )
                except Exception:
                    pass

                # Flat dict for curl_cffi
                if cookie.name:
                    self._ais_cookie_dict[cookie.name] = cookie.value

            cookie_count = len(list(self.ais_session.cookies))
            logger.info(f"Copied {cookie_count} cookies to AIS session jar")
            print(f"[DEBUG] Copied {cookie_count} cookies to AIS session")
            print(f"[DEBUG] AIS jar cookies: { {c.name: c.value[:20]+'...' for c in self.ais_session.cookies} }")

        except Exception as e:
            logger.error(f"Failed to copy cookies to AIS session: {e}", exc_info=True)

    def _update_ais_cookies_from_response(self, response) -> None:
        """Parse Set-Cookie from AIS response and force-store into ais_session jar + flat dict."""
        if not response or not hasattr(response, "headers"):
            return

        # Absorb response.cookies (works for both requests and curl_cffi)
        resp_cookies = getattr(response, "cookies", None)
        if resp_cookies:
            if isinstance(resp_cookies, dict):
                for name, value in resp_cookies.items():
                    self._ais_cookie_dict[name] = value
                    try:
                        self.ais_session.cookies.set(name, value, domain="ais.insight.gov.in", path="/", secure=True)
                    except Exception:
                        try:
                            self.ais_session.cookies.set(name, value)
                        except Exception:
                            pass
            else:
                for cookie in resp_cookies:
                    try:
                        self.ais_session.cookies.set_cookie(cookie)
                        if hasattr(cookie, 'name') and hasattr(cookie, 'value'):
                            self._ais_cookie_dict[cookie.name] = cookie.value
                    except Exception:
                        try:
                            self.ais_session.cookies.set(cookie.name, cookie.value)
                            self._ais_cookie_dict[cookie.name] = cookie.value
                        except Exception:
                            pass

        # Manual Set-Cookie parsing for edge cases
        set_cookie_values = []
        try:
            raw = getattr(response, "raw", None)
            raw_headers = getattr(raw, "headers", None) if raw is not None else None
            if raw_headers is not None and hasattr(raw_headers, "get_all"):
                set_cookie_values = raw_headers.get_all("Set-Cookie") or []
        except Exception:
            pass

        if not set_cookie_values:
            header_val = response.headers.get("Set-Cookie")
            if header_val:
                set_cookie_values = re.split(r', (?=[^\s=]+=)', header_val)

        for sc in set_cookie_values:
            try:
                cookie = SimpleCookie()
                cookie.load(sc)
                for name, morsel in cookie.items():
                    value = morsel.value
                    domain = morsel["domain"] if morsel["domain"] else "ais.insight.gov.in"
                    path = morsel["path"] if morsel["path"] else "/"
                    secure = bool(morsel["secure"])
                    self._ais_cookie_dict[name] = value
                    try:
                        self.ais_session.cookies.set(name, value, domain=domain, path=path, secure=secure)
                    except Exception:
                        self.ais_session.cookies.set(name, value)
            except Exception:
                continue

    def _get_merged_ais_cookie_dict(self) -> Dict[str, str]:
        """Return merged cookie dict from ePortal session + AIS session + flat dict."""
        merged: Dict[str, str] = {}
        for cookie in self.session.cookies:
            if cookie.name:
                merged[cookie.name] = cookie.value
        for cookie in self.ais_session.cookies:
            if cookie.name:
                merged[cookie.name] = cookie.value
        merged.update(getattr(self, "_ais_cookie_dict", {}))
        return merged

    # ...existing code...

    def _safe_ais_request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Any:
        """
        Perform AIS request with retries.
        Uses curl_cffi with Chrome TLS impersonation to bypass CloudFront WAF.
        Falls back to requests if curl_cffi is not installed.
        """
        max_retries = self.config.max_retries
        delay = self.config.initial_delay
        last_exception = None

        if not hasattr(self, "_ais_cookie_dict"):
            self._ais_cookie_dict = {}

        for attempt in range(1, max_retries + 1):
            try:
                req_headers: Dict[str, str] = {
                    "User-Agent": self.config.user_agent,
                    "Accept-Encoding": self.config.accept_encoding,
                    "Accept-Language": self.config.accept_language,
                    "Connection": "keep-alive",
                }

                if headers:
                    req_headers.update(headers)

                allow_redirects = kwargs.pop("allow_redirects", True)

                # Build merged cookie dict from BOTH ePortal + AIS
                merged_cookies = self._get_merged_ais_cookie_dict()

                print(f"[DEBUG] AIS {method.upper()} {url[:100]}... (attempt {attempt})")
                print(f"[DEBUG] AIS using {'curl_cffi' if HAS_CURL_CFFI else 'requests (WARNING: may get 425)'}")
                print(f"[DEBUG] AIS merged cookie count: {len(merged_cookies)}")
                print(f"[DEBUG] AIS merged cookie names: {list(merged_cookies.keys())}")

                if HAS_CURL_CFFI:
                    # ── curl_cffi path: real Chrome TLS fingerprint ──
                    # Remove Cookie from headers — pass via cookies= param instead
                    # curl_cffi merges cookies= into the request automatically
                    explicit_cookie = req_headers.pop("Cookie", None)

                    # If caller built an explicit Cookie header, parse it into the dict
                    if explicit_cookie:
                        for pair in explicit_cookie.split(";"):
                            pair = pair.strip()
                            if "=" in pair:
                                k, v = pair.split("=", 1)
                                merged_cookies[k.strip()] = v.strip()

                    cffi_kwargs: Dict[str, Any] = {
                        "headers": req_headers,
                        "cookies": merged_cookies,
                        "timeout": self.config.timeout,
                        "allow_redirects": allow_redirects,
                        "impersonate": "chrome",
                    }

                    if json_data is not None:
                        cffi_kwargs["json"] = json_data

                    # Pass through any extra kwargs (data=, params=, etc.)
                    for k, v in kwargs.items():
                        if k not in cffi_kwargs:
                            cffi_kwargs[k] = v

                    response = cffi_requests.request(
                        method.upper(),
                        url,
                        **cffi_kwargs,
                    )

                    # Absorb cookies from response into our tracking dict + jar
                    self._update_ais_cookies_from_response(response)
                    if hasattr(response, "history") and response.history:
                        for h in response.history:
                            self._update_ais_cookies_from_response(h)

                    print(f"[DEBUG] AIS response: status={response.status_code}")
                    if hasattr(response, "url"):
                        print(f"[DEBUG] AIS final url: {str(response.url)[:120]}")
                    return response

                else:
                    # ── Fallback: standard requests (will likely get 425) ──
                    cookie_header = req_headers.pop("Cookie", None)

                    request_kwargs: Dict[str, Any] = {
                        "timeout": self.config.timeout,
                        "allow_redirects": allow_redirects,
                        **kwargs,
                    }
                    if json_data is not None:
                        request_kwargs["json"] = json_data

                    response = requests.request(
                        method.upper(),
                        url,
                        headers=req_headers,
                        cookies=merged_cookies,
                        **request_kwargs,
                    )

                    self._update_ais_cookies_from_response(response)
                    if hasattr(response, "history"):
                        for h in response.history:
                            self._update_ais_cookies_from_response(h)

                    print(f"[DEBUG] AIS response: status={response.status_code}, url={response.url[:100]}")
                    return response

            except Exception as e:
                last_exception = e
                logger.warning(f"AIS request error on attempt {attempt}/{max_retries}: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= self.config.backoff_factor
                else:
                    raise NetworkError(f"AIS request failed for {url}") from e

        raise NetworkError(f"AIS request failed after {max_retries} attempts") from last_exception


    def _create_session(self) -> requests.Session:
        """Create and configure HTTP session with retry logic.
        
        Returns:
            Configured requests.Session instance
        """
        session = requests.Session()
        session.proxies.update(self.proxies)
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        # Set default headers
        default_headers = {
            'User-Agent': self.config.user_agent,
            'Accept': self.config.accept,
            'Accept-Encoding': self.config.accept_encoding,
            'Accept-Language': self.config.accept_language,
            'Content-Type': self.config.content_type,
            'Referer': self.config.referer,
            'Sec-CH-UA': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            'Sec-CH-UA-Mobile': '?1',
            'Sec-CH-UA-Platform': '"Android"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Origin': 'https://eportal.incometax.gov.in',
            'Connection': 'keep-alive'
        }
        session.headers.update(default_headers)
        
        return session
    
    # ========================================================================
    # Core HTTP Methods
    # ========================================================================
    
    def _safe_post(self, url: str, json_data: Optional[Dict] = None, 
                   sn: Optional[str] = None, **kwargs) -> requests.Response:
        """Perform POST request with retry logic and cookie management.
        
        Args:
            url: Target URL
            json_data: JSON payload
            sn: Service name header
            **kwargs: Additional requests.post arguments
            
        Returns:
            requests.Response object
            
        Raises:
            NetworkError: If all retries are exhausted
            APIError: If server returns error response
        """
        max_retries = self.config.max_retries
        delay = self.config.initial_delay
        last_exception = None
        
        # Set service name header if provided
        if sn is not None:
            self.session.headers.update({"sn": str(sn)})
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"POST {url} (attempt {attempt}/{max_retries})")
                
                if json_data is not None:
                    response = self.session.post(url, json=json_data, 
                                                timeout=self.config.timeout, **kwargs)
                else:
                    response = self.session.post(url, timeout=self.config.timeout, **kwargs)
                
                logger.debug(f"Response status: {response.status_code}")
                
                # Update cookies from response
                if response.status_code == 200:
                    self._update_cookies_from_response(response)
                
                return response
                
            except ( requests.ConnectionError) as e:
                last_exception = e
                logger.warning(f"Connection error on attempt {attempt}/{max_retries}: {e}")
                
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= self.config.backoff_factor
                else:
                    raise NetworkError(f"Max retries reached for {url}") from e
            
            except requests.Timeout as e:
                last_exception = e
                logger.warning(f"Timeout on attempt {attempt}/{max_retries}")
                
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= self.config.backoff_factor
                else:
                    raise NetworkError(f"Request timeout for {url}") from e
        
        if last_exception:
            raise NetworkError(f"Request failed after {max_retries} attempts") from last_exception
    
    def _update_cookies_from_response(self, response: requests.Response) -> None:
        """Parse Set-Cookie headers and update session cookiejar.
        
        Args:
            response: HTTP response object
        """
        if not response or not hasattr(response, 'headers'):
            return
        
        # Collect Set-Cookie header values
        set_cookie_values = []
        
        try:
            raw = getattr(response, 'raw', None)
            if raw is not None:
                raw_headers = getattr(raw, 'headers', None)
                if raw_headers is not None and hasattr(raw_headers, 'get_all'):
                    set_cookie_values = raw_headers.get_all('Set-Cookie') or []
        except Exception:
            pass
        
        if not set_cookie_values:
            header_val = response.headers.get('Set-Cookie')
            if header_val:
                parts = re.split(r', (?=[^\s=]+=)', header_val)
                set_cookie_values = parts
        
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
                    secure = bool(morsel['secure'])
                    
                    try:
                        if domain or path:
                            self.session.cookies.set(name, value, domain=domain, 
                                                    path=path, secure=secure)
                        else:
                            self.session.cookies.set(name, value)
                    except Exception:
                        self.session.cookies.set(name, value)
            except Exception as e:
                logger.debug(f"Failed to parse cookie: {e}")
                continue
        
        # Rebuild explicit Cookie header
        try:
            cookie_header = "; ".join([f"{c.name}={c.value}" for c in self.session.cookies])
            if cookie_header:
                self.session.headers.update({"Cookie": cookie_header})
        except Exception as e:
            logger.debug(f"Failed to rebuild Cookie header: {e}")
    
    def _parse_response(self, response: requests.Response) -> APIResponse:
        """Parse JSON response into standardized format.
        
        Args:
            response: HTTP response object
            
        Returns:
            APIResponse object
        """
        try:
            data = response.json()
            messages = data.get('messages', [])
            
            # Check for success
            has_success = any(msg.get('code') == MessageCode.SUCCESS.value for msg in messages)
            
            return APIResponse(
                success=has_success,
                data=data,
                messages=messages,
                status_code=response.status_code
            )
        except json.JSONDecodeError:
            return APIResponse(
                success=False,
                error="Invalid JSON response",
                status_code=response.status_code
            )

    def _safe_parse_json(self, response: requests.Response):
        """Safely parse JSON from a response.

        Returns a tuple: (data, error_message). If parsing succeeds, error_message is None.
        If parsing fails, data is None and error_message contains a concise description.
        """
        try:
            data = response.json()
            return data, None
        except Exception as e:
            # Try to capture raw text for debugging but avoid very large payloads
            raw = None
            try:
                raw = response.text
                if raw and len(raw) > 1000:
                    raw = raw[:1000] + '...'
            except Exception:
                raw = None

            err = f"Invalid JSON response: {str(e)}"
            if raw:
                err = f"{err}; raw_response={raw}"
            return None, err
    
    # ========================================================================
    # Authentication Flow
    # ========================================================================
    
    def _step1_submit_pan(self) -> Dict[str, Any]:
        """Execute Step 1: Submit PAN for initial authentication.
        
        Returns:
            Dict with 'success' bool and optional 'error' string
            
        Raises:
            AuthenticationError: If PAN is rejected
        """
        logger.info("=" * 80)
        logger.info("STEP 1: Submit PAN")
        logger.info("=" * 80)
        
        url = f"{self.config.base_url}{self.config.login_endpoint}"
        
        # Prepare headers
        headers = dict(self.session.headers.copy())
        headers['sn'] = self.config.wlogin_service
        
        payload = {
            "entity": self.credentials.pan,
            "serviceName": self.config.wlogin_service
        }
        
        try:
            response = self._safe_post(url, json_data=payload, headers=headers)
            api_response = self._parse_response(response)
            
            if not api_response.success:
                # Extract error messages
                error_messages = []
                for msg in api_response.messages:
                    if msg.get('code') != MessageCode.SUCCESS.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
                error_text = '; '.join(error_messages) if error_messages else 'PAN validation failed'
                logger.error(f"PAN validation failed: {error_text}")
                return {'success': False, 'error': error_text}
            
            # Extract authentication state
            data = api_response.data
            self.auth_state.errors = data.get('errors', [])
            self.auth_state.req_id = data.get('reqId')
            self.auth_state.entity = data.get('entity')
            self.auth_state.entity_type = data.get('entityType')
            self.auth_state.role = data.get('role')
            self.auth_state.uid_valdtn_flg = data.get('uidValdtnFlg')
            self.auth_state.aadhaar_mobile_validated = data.get('aadhaarMobileValidated')
            self.auth_state.sec_accs_msg = data.get('secAccssMsg')
            self.auth_state.sec_login_options = data.get('secLoginOptions')
            self.auth_state.dto_service = data.get('dtoService')
            self.auth_state.exempted_pan = data.get('exemptedPan')
            self.auth_state.user_consent = data.get('userConsent')
            self.auth_state.img_byte = data.get('imgByte')
            
            if not self.auth_state.req_id:
                logger.error("No reqId received")
                return {'success': False, 'error': 'No reqId received from server'}
            
            logger.info(f"✓ Step 1 SUCCESS - ReqId: {self.auth_state.req_id}")
            return {'success': True}
            
        except Exception as e:
            logger.error(f"Step 1 failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
    
    def _step2_verify_password(self) -> Dict[str, bool]:
        """Execute Step 2: Verify password.
        
        Returns:
            Dict with 'success' and 'existing_session' flags
            
        Raises:
            AuthenticationError: If password is incorrect
        """
        logger.info("=" * 80)
        logger.info("STEP 2: Verify Password")
        logger.info("=" * 80)
        
        if not self.auth_state.req_id or not self.auth_state.entity:
            logger.error("Missing reqId or entity")
            return {"success": False, "existing_session": False}
        
        url = f"{self.config.base_url}{self.config.login_endpoint}"
        
        headers = dict(self.session.headers.copy())
        headers['sn'] = self.config.login_service
        
        encoded_password = base64.b64encode(self.credentials.password.encode()).decode()
        
        payload = {
            "errors": self.auth_state.errors,
            "reqId": self.auth_state.req_id,
            "entity": self.auth_state.entity,
            "entityType": self.auth_state.entity_type,
            "role": self.auth_state.role,
            "uidValdtnFlg": self.auth_state.uid_valdtn_flg,
            "aadhaarMobileValidated": self.auth_state.aadhaar_mobile_validated,
            "secAccssMsg": self.auth_state.sec_accs_msg,
            "secLoginOptions": self.auth_state.sec_login_options,
            "dtoService": self.auth_state.dto_service,
            "exemptedPan": self.auth_state.exempted_pan,
            "userConsent": self.auth_state.user_consent,
            "imgByte": self.auth_state.img_byte,
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
            "serviceName": self.config.login_service
        }
        
        try:
            response = self._safe_post(url, json_data=payload, headers=headers)
            api_response = self._parse_response(response)
            
            # Check for existing session
            has_existing_session = api_response.has_code(MessageCode.EXISTING_SESSION.value)
            
            if not api_response.success:
                # Extract error messages
                error_messages = []
                for msg in api_response.messages:
                    if msg.get('code') != MessageCode.SUCCESS.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
                error_text = '; '.join(error_messages) if error_messages else 'Password verification failed'
                logger.error(f"Password verification failed: {error_text}")
                return {"success": False, "existing_session": False, "error": error_text}
            
            # Update auth state
            data = api_response.data
            self.auth_state.client_ip = data.get("clientIp")
            self.auth_state.mobile_no = data.get("mobileNo")
            self.auth_state.email = data.get("email")
            self.auth_state.user_type = data.get("userType")
            
            logger.info("✓ Step 2 SUCCESS")
            
            return {
                "success": True,
                "existing_session": has_existing_session
            }
            
        except Exception as e:
            logger.error(f"Step 2 failed: {e}", exc_info=True)
            return {"success": False, "existing_session": False, "error": str(e)}
    
    def _step3_option_login(self) -> Dict[str, Any]:
        """Execute Step 3: Continue with existing session.
        
        Returns:
            Dict with 'success' bool and optional 'error' string
        """
        logger.info("=" * 80)
        logger.info("STEP 3: Option Login - Continue Existing Session")
        logger.info("=" * 80)
        
        payload = {
            "aadhaarMobileValidated": "false",
            "clientIp": self.auth_state.client_ip,
            "dtoService": "LOGIN",
            "email": self.auth_state.email,
            "entity": self.auth_state.entity,
            "entityType": self.auth_state.entity_type,
            "errors": self.auth_state.errors,
            "exemptedPan": False,
            "lastLoginSuccessFlag": True,
            "mobileNo": self.auth_state.mobile_no,
            "otpGenerationFlag": True,
            "otpValdtnFlg": True,
            "pass": None,
            "passValdtnFlg": True,
            "remark": "Continue",
            "reqId": self.auth_state.req_id,
            "role": self.auth_state.role,
            "secAccssMsg": "",
            "secLoginOptions": "",
            "serviceName": self.config.login_service,
            "uidValdtnFlg": True,
            "userConsent": "N",
            "userType": "IND"
        }
        
        url = f"{self.config.base_url}{self.config.login_endpoint}"
        
        headers = dict(self.session.headers.copy())
        headers['sn'] = self.config.login_service
        
        try:
            response = self._safe_post(url, json_data=payload, headers=headers)
            api_response = self._parse_response(response)
            
            if api_response.success:
                logger.info("✓ Step 3 SUCCESS")
                return {'success': True}
            
            # Extract error messages
            error_messages = []
            for msg in api_response.messages:
                if msg.get('code') != MessageCode.SUCCESS.value:
                    desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                    if desc:
                        error_messages.append(desc)
            error_text = '; '.join(error_messages) if error_messages else 'Session continuation failed'
            logger.error(f"Step 3 failed: {error_text}")
            return {'success': False, 'error': error_text}
            
        except Exception as e:
            logger.error(f"Step 3 failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
    
    def login(self) -> Dict[str, Any]:
        """Execute complete login flow.
        
        Returns:
            Dictionary with login result for API exposure:
                - success: bool
                - req_id: str (if successful)
                - user_id: str (if successful)
                - cookies: dict (if successful)
                - headers: dict (if successful)
                - error: str (if failed)
                - step: int (if failed)
                - message: str (description of result)
        """
        logger.info("=" * 80)
        logger.info(f"ePortal Login - PAN: {self.credentials.pan[:4]}****")
        logger.info("=" * 80)
        
        try:
            # Step 1: Submit PAN
            step1_result = self._step1_submit_pan()
            if not step1_result.get('success'):
                error_msg = step1_result.get('error', 'PAN validation failed')
                return {
                    'success': False,
                    'error': error_msg,
                    'message': f'Step 1 failed: {error_msg}',
                    'step': 1
                }
            
            # Step 2: Verify Password (with retries)
            success = False
            resp = None
            
            for i in range(6):
                time.sleep(i)
                resp = self._step2_verify_password()
                if resp.get("success") and not resp.get("existing_session"):
                    success = True
                    break
            
            # Step 3: Handle existing session
            step3_error = None
            if resp and resp.get("existing_session"):
                for i in range(6):
                    time.sleep(i)
                    step3_result = self._step3_option_login()
                    if step3_result.get('success'):
                        success = True
                        
                        break
                    step3_error = step3_result.get('error', 'Session continuation failed')
                
                if not success:
                    error_msg = step3_error or 'Could not continue with existing session'
                    return {
                        'success': False,
                        'error': error_msg,
                        'message': f'Step 3 failed: {error_msg}',
                        'step': 3
                    }
            
            if not success:
                # Get error from last attempt
                error_msg = resp.get('error', 'Password verification failed') if resp else 'Password verification failed'
                return {
                    'success': False,
                    'error': error_msg,
                    'message': f'Step 2 failed: {error_msg}',
                    'step': 2
                }
            
            # Login successful
            logger.info("=" * 80)
            logger.info("✓ LOGIN SUCCESSFUL!")
            logger.info("=" * 80)
            logger.info(f"ReqId: {self.auth_state.req_id}")
            logger.info(f"User: {self.auth_state.entity}")
            logger.info(f"Cookies: {len(list(self.session.cookies))}")
            logger.info("=" * 80)

            # start a background daemon thread to extend the session every 5 minutes
            if getattr(self, "_stop_extender_event", None) is None:
                self._stop_extender_event = threading.Event()

            # if extender already running, don't start another
            thread_alive = getattr(self, "_extender_thread", None)
            if not (thread_alive and thread_alive.is_alive()):
                def _extender_loop():
                    interval = getattr(self.config, "session_extend_interval", 300)  # seconds (default 5 mins)
                    while not self._stop_extender_event.wait(interval):
                        try:
                            self.extent_session(self.credentials.pan)
                        except Exception as e:
                            logger.error(f"Session extender error: {e}", exc_info=True)

                t = threading.Thread(
                    target=_extender_loop,
                    name=f"ePortalSessionExtender-{self.credentials.pan}",
                    daemon=True
                )
                self._extender_thread = t
                t.start()
            
            return {
                'success': True,
                'message': 'Login successful',
                'req_id': self.auth_state.req_id,
                'user_id': self.auth_state.entity,
                'role': self.auth_state.role,
                'cookies': dict((c.name, c.value) for c in self.session.cookies),
                'headers': dict(self.session.headers)
            }
            
        except Exception as e:
            logger.error(f"Login exception: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'message': f'Login failed: {str(e)}'
            }

    def extent_session(self,pan) -> None:
        """Extend session with provided cookies and headers.
        """
        url=f"{self.config.base_url}/iec/loginapi/auth/extendSession"
        payload={"loggedInUserId": self.credentials.pan}
        try:
            response = self._safe_post(url, json_data=payload, sn="")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.info(f"Session extend returned non-JSON: {json_err}")
            else:
                logger.info(f"Session extended: {data}")
        except Exception as e:
            logger.error(f"Failed to extend session: {e}", exc_info=True)


    # ========================================================================
    # API Methods
    # ========================================================================
    
    def get_filling_data(self, year: int, pan: str) -> Dict[str, Any]:
        """Retrieve prefill data for a given assessment year.
        
        Args:
            year: Assessment year (e.g., 2025)
            pan: PAN number
            
        Returns:
            Dict with:
                - success: bool
                - data: dict (prefill data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """
        try:
            url = f"{self.config.base_url}{self.config.save_entity_endpoint}"
            payload = {
                "ay": year,
                "pan": pan,
                "serviceName": "taxDepositService"
            }
            
            response = self._safe_post(url, json_data=payload, sn="")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"get_filling_data JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve filling data: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            # Handle case where API returns a list instead of dict
            if isinstance(data, list):
                return {
                    'success': True,
                    'data': data,
                    'message': 'Prefill data retrieved successfully',
                    'error': None,
                    'messages': [],
                    'status_code': response.status_code
                }
            
            # Check if response indicates success
            messages = data.get('messages', [])
            has_success = any(msg.get('code') == 'EF00000' for msg in messages)
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != 'EF00000':
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success or response.status_code == 200,
                'data': data,
                'message': 'Prefill data retrieved successfully' if has_success else ('Prefill data request failed' if error_text else 'Prefill data request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"get_prefill_data failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve prefill data: {str(e)}'
            }
    
    def get_prefill_data(self, pan: str, assessment_year: int) -> Dict[str, Any]:
        """Retrieve prefill date information.
        
        Args:
            pan: PAN number
            assement_year: Assessment year
            
        Returns:
            Dict with:
                - success: bool
                - data: dict (prefill date data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """
        try:
            url = f"{self.config.base_url}/iec/itrweb/auth/v0.1/returns/getPrefillCurrentYr"
            payload = {
                "pan": pan,
                "assessmentYear": str(assessment_year)
            }
            
            response = self._safe_post(url, json_data=payload, sn="")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"get_prefill_data JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve prefill data: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            
            # Handle case where API returns a list instead of dict
            if isinstance(data, list):
                return {
                    'success': True,
                    'data': data,
                    'message': 'Prefill date retrieved successfully',
                    'error': None,
                    'messages': [],
                    'status_code': response.status_code
                }
            
            # Handle wrapper response with content field (common pattern in ePortal)
            if isinstance(data, dict) and 'content' in data and 'responseCode' in data:
                response_code = data.get('responseCode')
                response_desc = data.get('responseDesc', '')
                
                # Try to parse nested JSON content
                content = data.get('content')
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        content = {'raw': content}
                
                success = response_code == 0
                
                return {
                    'success': success,
                    'data': content,
                    'message': response_desc if response_desc else ('Prefill date retrieved successfully' if success else 'Prefill date request failed'),
                    'error': None if success else response_desc,
                    'messages': [{'code': 'EF00000', 'desc': response_desc}] if success else [{'code': 'ERROR', 'desc': response_desc}],
                    'status_code': response.status_code,
                    'trace_id': data.get('traceId')
                }
            
            messages = data.get('messages', [])
            has_success = any(msg.get('code') == 'EF00000' for msg in messages)
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != 'EF00000':
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success or response.status_code == 200,
                'data': data,
                'message': 'Prefill date retrieved successfully' if has_success else ('Prefill date request failed' if error_text else 'Prefill date request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"get_prefill_date failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve prefill date: {str(e)}'
            }

    def check_everify_otp(self, pan: str, otp: str, ackn_no: str, verify_now:bool=False) -> Dict[str, Any]:
        """
        Verify e-verification OTP.

        Args:
            pan: PAN number
            otp: One-Time Password  
            ackn_no: Acknowledgment number
        """

        try:

            if not verify_now:
        
                filling_data = None

                if not self.active_fillings:
                    self.get_active_verify_filings(pan)
                
                for filling in self.active_fillings:
        
                    if filling.get('ackNum') == str(ackn_no):
                        filling_data = filling
                        break

                if not filling_data:
                    return {
                        'success': False,   
                        'error': f'Filing with ackn_no {ackn_no} not found in active fillings',
                        'message': f'Filing with ackn_no {ackn_no} not found in active fillings'        
                    }

            
            url = f"{self.config.base_url}{self.config.everify_otp_validate_endpoint}"

            if verify_now:
                payload={"serviceName":"verifyOtpUsingAadhar",
                         "verifPan":pan,
                         "otp":otp,
                         "assessmntYr":self.itr_year,
                         "ackNum":ackn_no,
                         "moduleCode":"ITR",
                         "otpGenerationSource":"EFL",
                         "selectionFlag":"N",
                         "formCd":self.form_cd,
                         "aadhaarTxnId":self.adhar_transaction_id,
                         "preLoginFlag":"N",
                         "header":{"formName":"FO-091-EVERI"},
                         "loggedInUserId":pan
                        }




            else:
                payload = {
                    "serviceName": "verifyOtpUsingAadhar",
                    "verifPan": pan,
                    "otp": otp,
                    "assessmntYr": str(filling_data.get('assmentYear')),
                    "header": {"formName": "FO-091-EVERI"},
                    "loggedInUserId": pan,
                    "ackNum": ackn_no,
                    "moduleCode": "ITR",
                    "preLoginFlag": "N",
                    "selectionFlag": "L",
                    "otpGenerationSource": "EFL",
                    "aadhaarTxnId": self.adhar_transaction_id,
                    "noOfDelay":filling_data.get('noOfDelay') if not verify_now else "",
                    "delayCd": "",
                    "delayOthTxt": "",
                    "username": self.auth_state.entity,
                    "formCd": filling_data.get('formTypeCd') if not verify_now else self.form_cd,
                }



  

            response = self._safe_post(url, json_data=payload, sn="verifyOtpUsingAadhar")
            # print(response.text)
            data, json_err = self._safe_parse_json(response)
            if json_err:
        
                logger.error(f"check_everify_otp JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to verify OTP: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            
        

            messages = data.get('messages', [])
            errors = data.get('errors', [])
            response_code = data.get('responseCode')
            arn_number = data.get('arnNumber')
            transaction_no = data.get('transactionNo')

            # Check for error messages
            error_messages = []
            for msg in messages:
                if str(msg.get('type', '')).lower() == 'error' or msg.get('code', '').startswith('ERR'):
                    desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                    if desc:
                        error_messages.append(desc)

                

            # Add errors from 'errors' list
            if errors:
                for err in errors:
                    if isinstance(err, dict):
                        desc = err.get('desc') or err.get('description') or err.get('message', '')
                        if desc:
                            error_messages.append(desc)
                    elif isinstance(err, str):
                        error_messages.append(err)

            # Determine success
            has_success = (
                (response_code in (0, '0', 'SUCCESS', None)) and
                arn_number and transaction_no and
                not error_messages and
                not errors
            )

            error_text = '; '.join(error_messages) if error_messages else None

            if  verify_now and not errors:
                final_submit =self.submit_itr_final_step(self.json_data,ackn_no,self.itr_year,self.filling_type,self.form_cd,self.it_sec_cd,pan,stringify=True)

                if not final_submit.get('success'):
                    return {
                        'success': False,
                        'error': final_submit.get('error'),
                        'message': f'OTP verified but final submission failed: {final_submit.get("message")}',
                        'status_code': response.status_code
                    }
                
                else:
                    return {
                        'success': True,
                        'data': final_submit.get('data'),
                        'arnNumber': arn_number,
                        'transactionNo': transaction_no,
                        'message': 'OTP verified and ITR form submitted successfully',
                        'error': None,
                        'messages': messages,
                        'errors': errors,
                        'status_code': response.status_code
                    }

            return {
                'success': has_success,
                'data': data,
                'arnNumber': arn_number,
                'transactionNo': transaction_no,
                'message': 'ITR form validated successfully' if has_success else ('Form validation failed' if error_text else 'Form validation completed'),
                'error': error_text,
                'messages': messages,
                'errors': errors,
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"check_everify_otp failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to verify OTP: {str(e)}'
            }

    




    def get_itr_status(self, pan: str) -> Dict[str, Any]:
        """Get ITR filing status.
        
        Args:
            pan: PAN number
            
        Returns:
            Dict with:
                - success: bool
                - data: dict (ITR status data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """
        try:
            url = f"{self.config.base_url}{self.config.get_entity_endpoint}"
            payload = {
                "header": {"formName": "FO-006-ITRST"},
                "serviceName": "itrStatusServiceShort",
                "entityNum": pan
            }
            
            response = self._safe_post(url, json_data=payload)
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"get_itr_status JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve ITR status: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            
            # Handle case where API returns a list instead of dict
            if isinstance(data, list):
                return {
                    'success': True,
                    'data': data,
                    'message': 'ITR status retrieved successfully',
                    'error': None,
                    'messages': [],
                    'status_code': response.status_code
                }
            
            messages = data.get('messages', [])
            has_success = any(msg.get('code') == 'EF00000' for msg in messages)
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != 'EF00000':
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success or response.status_code == 200,
                'data': data,
                'message': 'ITR status retrieved successfully' if has_success else ('ITR status request failed' if error_text else 'ITR status request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"get_itr_status failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve ITR status: {str(e)}'
            }
    
    def get_download_itr_file(self, pan: str, ackn_no: str) -> Dict[str, Any]:
        """Download ITR file by acknowledgment number.
        
        Args:
            pan: PAN number
            ackn_no: Acknowledgment number
            
        Returns:
            Dict with:
                - success: bool
                - data: dict (download data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """
        try:
            url = f"{self.config.base_url}{self.config.download_endpoint}"
            payload = {
                "ackNum": ackn_no,
                "loggedInUserId": pan
            }
            
            response = self._safe_post(url, json_data=payload, sn="")



            
            if response.status_code == 200:
                data, json_err = self._safe_parse_json(response)
                if json_err:
                    logger.error(f"get_download_itr_file JSON parse error: {json_err}")
                    return {
                        'success': False,
                        'error': json_err,
                        'message': f'Failed to download ITR file: {json_err}',
                        'status_code': getattr(response, 'status_code', None)
                    }
                messages = data.get('messages', [])
                has_success = any(msg.get('code') == 'EF00000' for msg in messages)
                
                # Extract error messages if not successful
                error_messages = []
                if not has_success:
                    for msg in messages:
                        if msg.get('code') != 'EF00000':
                            desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                            if desc:
                                error_messages.append(desc)
                
                error_text = '; '.join(error_messages) if error_messages else None
                
                return {
                    'success': has_success or response.status_code == 200,
                    'data': data,
                    'message': 'ITR file download initiated' if has_success else ('Download request failed' if error_text else 'Download request completed'),
                    'error': error_text,
                    'messages': messages,
                    'status_code': response.status_code
                }
            else:
                return {
                    'success': False,
                    'error': f"HTTP {response.status_code}",
                    'message': f'Download failed with status {response.status_code}',
                    'status_code': response.status_code
                }
        except Exception as e:
            logger.error(f"get_download_itr_file failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to download ITR file: {str(e)}'
            }
    
    def get_active_verify_filings(self, pan: str) -> Dict[str, Any]:
        """Retrieve active e-verification filings.
        
        Args:
            pan: PAN number
            
        Returns:
            Dict with:
                - success: bool
                - data: dict (filing data if successful)
                - active_list: list (parsed active filings)
                - error: str (error message if failed)
                - message: str (description)
        """
        url = f"{self.config.base_url}{self.config.get_entity_endpoint}"
        payload = {
            "header": {"formName": "FO-016-EVRTN"},
            "serviceName": "eVerifyReturnPostLoginService",
            "entityNum": pan
        }
        
        response = self._safe_post(url, json_data=payload, sn="eVerifyReturnPostLoginService")
        
        if response.status_code != 200:
            return {
                'success': False,
                'error': f"HTTP {response.status_code}",
                'message': f'Request failed with status {response.status_code}',
                'status_code': response.status_code
            }
        
        try:
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"get_active_verify_filings JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve active filings: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            messages = data.get("messages", [])
            
            # Check for success code
            has_success = any(msg.get("code") == MessageCode.EVERIFY_SUCCESS.value 
                            for msg in messages)
            
            active_list = []
            if has_success:
                active_list_raw = data.get("activeList", [])
                
                # Parse active list if it's a JSON string
                if isinstance(active_list_raw, str):
                    try:
                        active_list = json.loads(active_list_raw)
                        self.active_fillings = active_list
                    except json.JSONDecodeError:
                        self.active_fillings = []
                        active_list = []
                else:
                    active_list = active_list_raw if isinstance(active_list_raw, list) else []
                    self.active_fillings = active_list
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != MessageCode.EVERIFY_SUCCESS.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success,
                'data': data,
                'active_list': active_list,
                'message': 'Active filings retrieved successfully' if has_success else ('Failed to retrieve active filings' if error_text else 'No active filings found'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            
        except json.JSONDecodeError:
            return {
                'success': False,
                'error': 'Invalid JSON response',
                'message': 'Failed to parse response',
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"get_active_verify_filings failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve active filings: {str(e)}'
            }

    def e_verify_active_filings(self, year: int,ack_no:str,pan:str) -> Dict[str, Any]:
        """E-verify active filing for a specific year.
        
        Args:
            year: Assessment year
            ack_no: Acknowledgment number
            pan: PAN number
        """
        try:
            self.year=year
            self.ack_no=ack_no
            revise_filling=self.revise_active_efillings(year, pan)
            if not revise_filling.get('success'):
                return {
                    'success': False,
                    'error': revise_filling.get('error'),
                    'message': f"Failed to validate revised return for year {year}: {revise_filling.get('error')}"
                }
            
            itr_status=self.get_download_itr_file(pan=pan,ackn_no=ack_no)
            
            if not itr_status.get('success'):
                return {
                    'success': False,
                    'error': itr_status.get('error'),
                    'message': f"Failed to get ITR status: {itr_status.get('error')}"
                }
            
            
            check_aadhaar=self.check_aadhaar_linked(pan=pan)
            if not check_aadhaar.get('success'):
                return {
                    'success': False,
                    'error': check_aadhaar.get('error'),
                    'message': f"Failed to check Aadhaar linkage: {check_aadhaar.get('error')}"
                }
            
            # check_dsc=self.check_dsc_linked(pan=pan)
            # if not check_dsc.get('status_code')==200:
            #     return {
            #         'success': False,
            #         'error': check_dsc.get('error'),
            #         'message': f"Failed to check DSC linkage: {check_dsc.get('error')}"
            #     }
            
            send_otp=self.send_otp_aadhaar(pan=pan)
            if not send_otp.get('success'):
                return {
                    'success': False,
                    'error': send_otp.get('error'),
                    'message': f"Failed to send OTP to Aadhaar-linked mobile: {send_otp.get('error')}"
                }
            
            return {
                'success': True,
                'data': send_otp.get('data'),
                'aadhaar_transaction_id': self.adhar_transaction_id,
                'message': f"E-verify process initiated successfully for year {year}, please check your Aadhaar-linked mobile for OTP.000000"
            }
        except Exception as e:
            logger.error(f"e_verify_active_filings failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to e-verify active filing: {str(e)}'
            }
            

            
            

    def revise_active_efillings(self, year: int, pan: str) -> Dict[str, Any]:
        """Validate revised return for a specific year.
        
        Args:
            year: Assessment year
            
        Returns:
            Dict with:
                - success: bool
                - data: dict (validation data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """
      
        
        try:
            url = f"{self.config.base_url}{self.config.get_entity_endpoint}"
            payload = {
                "assmentYear": str(year),
                "entityNum": pan,
                "serviceName": "everifyReturnPostLoginRevisedValidation"
            }
            
            response = self._safe_post(url, json_data=payload, 
                                       sn="everifyReturnPostLoginRevisedValidation")

            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"revise_active_efillings JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to validate revised return: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            messages = data.get("messages", [])
            
            # Check if response indicates success
            # Since messages list is empty, check other fields
            has_success = (
                data.get('entityNum') == pan and 
                data.get('assmentYear') == year and
                response.status_code == 200
            )
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                    if desc:
                        error_messages.append(desc)
                
                # If no messages but success criteria not met, add generic error
                if not error_messages:
                    if data.get('entityNum') != pan:
                        error_messages.append('PAN mismatch in response')
                    if data.get('assmentYear') != year:
                        error_messages.append('Assessment year mismatch in response')
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success,
                'data': data,
                'message': 'Revised return validated successfully' if has_success else ('Validation failed' if error_text else 'Validation completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            
        except json.JSONDecodeError:
            return {
                'success': False,
                'error': 'Invalid JSON response',
                'message': 'Failed to parse response',
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"revise_active_efillings failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to validate revised return: {str(e)}'
            }
    
    def check_aadhaar_linked(self, pan: str) -> Dict[str, Any]:
        """Check if Aadhaar is linked to PAN.
        
        Args:
            pan: PAN number
            
        Returns:
            Dict with:
                - success: bool (True if Aadhaar is linked)
                - data: dict (linkage data)
                - error: str (error message if failed)
                - message: str (description)
        """
        try:
            url = f"{self.config.base_url}{self.config.verification_endpoint}"
            payload = {
                "serviceName": "verifyOtpUsingAadhar",
                "header": {"formName": "FO-091-EVERI"},
                "loggedInUserId": pan
            }
            
            response = self._safe_post(url, json_data=payload, sn="verifyOtpUsingAadhar")

            if response.status_code != 200:
                return {
                    'success': False,
                    'error': f"HTTP {response.status_code}",
                    'message': f'Request failed with status {response.status_code}',
                    'status_code': response.status_code
                }
            
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"check_aadhaar_linked JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to check Aadhaar linkage: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            messages = data.get("messages", [])
            
            has_linkage = any(msg.get("code") == MessageCode.AADHAAR_LINKED.value 
                            for msg in messages)
            
            # Extract error messages if not successful
            error_messages = []
            if not has_linkage:
                for msg in messages:
                    if msg.get('code') != MessageCode.AADHAAR_LINKED.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_linkage,
                'data': data,
                'message': 'Aadhaar is linked to PAN' if has_linkage else ('Aadhaar linkage check failed' if error_text else 'Aadhaar not linked to PAN'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            
        except json.JSONDecodeError:
            return {
                'success': False,
                'error': 'Invalid JSON response',
                'message': 'Failed to parse response',
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"check_aadhaar_linked failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to check Aadhaar linkage: {str(e)}'
            }
    def check_dsc_linked(self, pan: str) -> Dict[str, Any]:
        """Check if DSC is linked to PAN.
        
        Args:
            pan: PAN number

        """

        try:
            url = f"{self.config.base_url}{self.config.dsc_config_endpoint}"

            payload = {
            "header": {"formName": "FO-052-RDSCT"},
            "userInfo": pan,
            "foreignEmailId": "",
            "serviceName": "userExistsService",
            "loggedInUserId": pan
            }
            
            response = self._safe_post(url, json_data=payload, sn="userExistsService")
            
            if response.status_code != 200:
                return {
                    'success': False,
                    'error': f"HTTP {response.status_code}",
                    'message': f'Request failed with status {response.status_code}',
                    'status_code': response.status_code
                }
                
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"check_dsc_linked JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to check DSC linkage: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            messages = data.get("messages", [])
            
            # Check for success based on response structure
            # If registeredDataResponse is "success", DSC is linked
            dsc_registered = data.get("registeredDataResponse") == "success"
            has_success = dsc_registered or any(msg.get('code') == 'EF00000' for msg in messages)
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != 'EF00000':
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
                
                # Add specific message if DSC not registered
                if data.get("registeredDataResponse") == "failure":
                    error_messages.append("DSC not registered for this PAN")
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success,
                'data': data,
                'dsc_registered': dsc_registered,
                'message': 'DSC is registered and linked' if dsc_registered else ('DSC not registered' if not error_text else 'DSC check failed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            
        except json.JSONDecodeError:
            return {
                'success': False,
                'error': 'Invalid JSON response',
                'message': 'Failed to parse response',
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"check_dsc_linked failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to check DSC linkage: {str(e)}'
            }

    #########################################################################################
    # Method to get the challan history for a given PAN
    #########################################################################################
    def get_challan_history(self, pan: str, crn=None, account_type="O") -> Dict[str, Any]:
        """Retrieve challan data for a given PAN.
    
        Args:
            pan: PAN number
            crn: Optional specific CRN to filter
            account_type: Account type ("O"=Old regime, "N"=New regime)
        Returns:
            Dict with:  
                - success: bool
                - data: dict (raw response)
                - payments: list (abstracted challan list from content)
                - error: str (error message if failed)
                - message: str (description)
        """

        try:
            url = f"{self.config.base_url}{self.config.challan_history_endpoint}"
            self.account_type = account_type
            
            payload = {
                "header": {"formName": "PO-03-PYMNT"},
                "formData": {
                    "pan": pan,
                    "actType": account_type,
                    "pageNumber": 0,
                    "pageSize": 5,
                    "loggedInUserID": pan,
                    "loggedInUserType": "IND"
                }
            }
            
            response = self._safe_post(url, json_data=payload, sn="")
            data, json_err = self._safe_parse_json(response)
            
            if json_err:
                logger.error(f"get_challan_history JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve challan history: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            
            messages = data.get("messages", [])
            has_success = any(msg.get("code") == MessageCode.SUCCESS.value for msg in messages) or response.status_code == 200

            # ✅ PROPER ABSTRACTION: Extract 'content' array from 'paymentList' paginated object
            payments = []
            
            # Step 1: Check if paymentList exists and extract content
            if "paymentList" in data and isinstance(data["paymentList"], dict):
                payment_list_obj = data["paymentList"]
                # The actual challan items are in the 'content' array
                if "content" in payment_list_obj and isinstance(payment_list_obj["content"], list):
                    payments = payment_list_obj["content"]
            
            # Step 2: Fallback - check other possible keys
            if not payments:
                possible_keys = ("payments", "challanList", "paymentHistory", "paymentDetails", "activeList")
                for key in possible_keys:
                    if key in data and isinstance(data[key], list):
                        payments = data[key]
                        break
            
            # Step 3: Check nested wrappers
            if not payments:
                inner = data.get("content") or data.get("data") or data.get("response")
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except Exception:
                        inner = None
                if isinstance(inner, dict):
                    for key in possible_keys:
                        if key in inner and isinstance(inner[key], list):
                            payments = inner[key]
                            break
            
            # Step 4: Parse if JSON string
            if isinstance(payments, str):
                try:
                    payments = json.loads(payments)
                except Exception:
                    payments = [payments] if payments else []
            
            # Step 5: Normalize to list
            if not isinstance(payments, list):
                payments = []
            
            # Store on the client for later use
            self.payments = payments
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != MessageCode.SUCCESS.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            # Filter by CRN if provided
            specific_payment = []
            if crn:
                self.payment_crn = crn
                specific_payment = [p for p in payments if p.get('crn') == crn]
            
            return {
                'success': has_success,
                'data': data,  # Raw response with pagination info
                'payments': specific_payment if crn else payments,  # Abstracted challan list
                'pagination': {
                    'totalPages': data.get('paymentList', {}).get('pageable', {}).get('totalPages') if isinstance(data.get('paymentList'), dict) else 0,
                    'totalElements': data.get('paymentList', {}).get('pageable', {}).get('totalElements') if isinstance(data.get('paymentList'), dict) else len(payments),
                    'pageSize': data.get('paymentList', {}).get('pageable', {}).get('pageSize') if isinstance(data.get('paymentList'), dict) else len(payments),
                    'currentPage': data.get('paymentList', {}).get('pageable', {}).get('pageNumber') if isinstance(data.get('paymentList'), dict) else 0
                },
                'message': 'Challan history retrieved successfully' if has_success else ('Failed to retrieve challan history' if error_text else 'No challan history found'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }

        except Exception as e:
            logger.error(f"get_challan_history failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve challan history: {str(e)}'
            }
   #----------------------------------------------------------------------------------------
    # Method to get the challan details for a given assessment year
   #-----------------------------------------------------------------------------------------

    def get_challan_details(self, pan: str, year: str, account_type: str = "O") -> Dict[str, Any]:

        """Retrieve challan details for a given PAN and assessment year.
        
        Args:
            pan: PAN number
            year: Assessment year
            account_type: Account type
        Returns:
            Dict with:  
                - success: bool     
                - data: dict (challan details if successful)
                - error: str (error message if failed)

                - message: str (description)
            """
        try:
            if not self.payments:
                challan_history = self.get_challan_history(pan, account_type=account_type)
                if not challan_history.get('success'):
                    return {
                        'success': False,
                        'error': challan_history.get('error'),
                        'message': f"Failed to retrieve challan history: {challan_history.get('error')}"
                    }
            relevant_challans = [c for c in self.payments if str(c.get('assessmentYear')) == str(year)]

            if not relevant_challans:
                return {
                    'success': False,
                    'error': f'No challan found for assessment year {year}',
                    'message': f'No challan found for assessment year {year}'
                }

            payment_details_results = []
            
            for challan in relevant_challans:
                url = f"{self.config.base_url}{self.config.challan_download_endpoint}"
                payload = {
                    "header": {"formName": "PO-03-PYMNT"},
                    "formData": {
                        "entityNum": pan,
                        "cin": challan.get('cin')
                    }
                }
                
                response = self._safe_post(url, json_data=payload, sn="")
                data, json_err = self._safe_parse_json(response)
                if json_err:
                    logger.error(f"get_challan_details JSON parse error for cin={challan.get('cin')}: {json_err}")
                    # treat as non-successful detail for this challan
                    messages = []
                    has_success = False
                    data = None
                else:
                    messages = data.get("messages", [])
                    has_success = any(msg.get("code") == MessageCode.SUCCESS.value for msg in messages) or response.status_code == 200

                data_details={

                    "challan_info": challan,
                    "payment_details": data if has_success else None

                }
                    
                
                payment_details_results.append(data_details)
                
            # Store all payment details
            self.payments_history = payment_details_results
            
            return {
                'success': True,
                'data': payment_details_results,
                'message': f'Challan details retrieved successfully for year {year}',
                'error': None,
                'messages': messages,
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"get_challan_details failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve challan details: {str(e)}'
            }

    #----------------------------------------------------------------------------------------
    # Method to get the ITR return types for a given assessment year
    #-----------------------------------------------------------------------------------------

    def get_return_types(self,year,is_active) -> Dict[str, Any]:
        """Retrieve available return types.

        Args:
            year: Assessment year
            is_active: Active status flag   
        
        Returns:
            Dict with:
                - success: bool
                - data: dict (return types data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """
        try:
            url = f"{self.config.base_url}/iec/master/getDetails/join"
            
            payload = {
                "tokenName": "itr_ay_sec_map",
                "requiredColumns": ["filing_type", "sec_cd", "it_sec_cd"],
                "dependentField": {
                    "assment_year": year,
                    "is_active": is_active,
                    "show_filing_type": "Y"
                },
                "distinctColumnName": "filing_type",
                "includeTokenName": "itr_ay_role_audit_map",
                "includeDependentField": {
                    "role_cd": self.auth_state.user_type,
                    "assment_year": year,
                    "is_active": is_active
                }
            }
            # print(f"Payload for get_return_types: {payload}")
            
            response = self._safe_post(url, json_data=payload, sn="itr_ay_sec_map")
            
            data, json_err = self._safe_parse_json(response)
            
            if json_err:
                logger.error(f"get_return_types JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve return types: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            # Handle case where API returns data array directly
            if isinstance(data, list):
                return {
                    'success': True,
                    'data': data,
                    'message': 'Return types retrieved successfully',
                    'error': None,
                    'messages': [],
                    'status_code': response.status_code
                }
            
            # Handle wrapper response with data field
            if isinstance(data, dict) and 'data' in data:
                data_list = data.get('data', [])
                if isinstance(data_list, list):
                    return {
                        'success': True,
                        'data': data_list,
                        'message': 'Return types retrieved successfully',
                        'error': None,
                        'messages': [],
                        'status_code': response.status_code
                    }
            
            messages = data.get("messages", [])
            has_success = any(msg.get("code") == MessageCode.SUCCESS.value 
                            for msg in messages) or response.status_code == 200
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != MessageCode.SUCCESS.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success,
                'data': data,
                'message': 'Return types retrieved successfully' if has_success else ('Failed to retrieve return types' if error_text else 'Return types request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            
        except Exception as e:
            logger.error(f"get_return_types failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve return types: {str(e)}'
            }

    #----------------------------------------------------------------------------------------
    # Method to get the assessment years(for itr returns submission)
    #-----------------------------------------------------------------------------------------

    def get_assesment_years(self) -> Dict[str, Any]:
        """Retrieve available assessment years.
        
        Returns:
            Dict with:
                - success: bool
                - data: dict (assessment years data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """
        try:
            url = f"{self.config.base_url}/iec/master/getDetails"
            payload = {
                "tokenName": "assment_year",
                "requiredColumns": ["assment_year_cd", "assment_year_desc", "itr_mode"],
                "dependentField": {"itr_flag": "Y"},
                "orderBy": [["assment_year_cd", "desc"]]
            }
            
            response = self._safe_post(url, json_data=payload, sn="assment_year")
            # print(response.text)
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"get_assesment_years JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve assessment years: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            
            # Handle case where API returns data array directly
            if isinstance(data, list):
                return {
                    'success': True,
                    'data': data,
                    'message': 'Assessment years retrieved successfully',
                    'error': None,
                    'messages': [],
                    'status_code': response.status_code
                }
            
            # Handle wrapper response with data field
            if isinstance(data, dict) and 'data' in data:
                data_list = data.get('data', [])
                if isinstance(data_list, list):
                    return {
                        'success': True,
                        'data': data_list,
                        'message': 'Assessment years retrieved successfully',
                        'error': None,
                        'messages': [],
                        'status_code': response.status_code
                    }
            
            messages = data.get("messages", [])
            has_success = any(msg.get("code") == MessageCode.SUCCESS.value 
                            for msg in messages) or response.status_code == 200
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != MessageCode.SUCCESS.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success,
                'data': data,
                'message': 'Assessment years retrieved successfully' if has_success else ('Failed to retrieve assessment years' if error_text else 'Assessment years request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            
        except Exception as e:
            logger.error(f"get_assesment_years failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve assessment years: {str(e)}'
            }


        
    def send_otp_aadhaar(self, pan: str) -> Dict[str, Any]:
        """Send OTP to Aadhaar-linked mobile.

        
        Args:
            pan: PAN number
            
        Returns:
            Dict with:
            - success: bool
            - data: dict (OTP send data)
            - error: str (error message if failed)
            - message: str (description)
        """
        try:
            # print("Sending OTP to Aadhaar-linked mobile...")
            url = f"{self.config.base_url}{self.config.verification_endpoint_save}"
            payload = {
                "serviceName": "verifyOtpUsingAadhar",
                "header": {"formName": "FO-091-EVERI"},
                "loggedInUserId": pan
            }
            
            response = self._safe_post(url, json_data=payload, sn="verifyOtpUsingAadhar")


            
            if response.status_code != 200:
                return {
                    'success': False,
                    'error': f"HTTP {response.status_code}",
                    'message': f'Request failed with status {response.status_code}',
                    'status_code': response.status_code
                }
            
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"send_otp_aadhaar JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to send OTP: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            messages = data.get("messages", [])
            success = "SUCCESS" in data.get("status", "")
            
            # Extract error messages if not successful
            error_messages = []
            if not success:
                for msg in messages:
                    desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                    if desc:
                        error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            if success:
                self.adhar_transaction_id = data.get("aadhaarTxnId")
            
            return {
                'success': success,
                'data': data,
                'message': 'OTP sent successfully' if success else ('Failed to send OTP' if error_text else 'OTP request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            
        except json.JSONDecodeError:
            return {
                'success': False,
                'error': 'Invalid JSON response',
                'message': 'Failed to parse response',
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"send_otp_aadhaar failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to send OTP: {str(e)}'
            }

    #=========================================================================================
    # GET THE ITR TYPES
    #=========================================================================================
    def get_itr_types(self, year: int, sec_cd: str, is_active: str, audit_flag: bool) -> Dict[str, Any]:

        """Retrieve available ITR types for a given assessment year.
        
        Args:
            year: Assessment year
            sec_cd: Section code
            is_active: Active status
            audit_flag: Audit flag
        Returns:
            Dict with:
                - success: bool
                - data: dict (ITR types data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """

        try:
            url = f"{self.config.base_url}/iec/master/getDetails/join/"
            payload = {
                "tokenName": "itr_ay_sec_map",
                "requiredColumns": ["form_cd", "form_cd"],
                "dependentField": {
                    "assment_year": year,
                    "sec_cd": sec_cd,
                    "is_active": is_active,
                },
                "distinctColumnName": "form_cd",
                "includeTokenName": "itr_ay_role_audit_map",
                "includeDependentField": {
                    "role_cd": self.auth_state.user_type,
                    "audit_flag": "N" if audit_flag is False else "Y",
                    "assment_year": year,
                    "is_active": is_active
                }
            }
            
            response = self._safe_post(url, json_data=payload, sn="itr_ay_sec_map")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"get_itr_types JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve ITR types: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            
            # Handle case where API returns data array directly
            if isinstance(data, list):
                return {
                    'success': True,
                    'data': data,
                    'message': 'ITR types retrieved successfully',
                    'error': None,
                    'messages': [],
                    'status_code': response.status_code
                }
            
            # Handle wrapper response with data field
            if isinstance(data, dict) and 'data' in data:
                data_list = data.get('data', [])
                if isinstance(data_list, list):
                    return {
                        'success': True,
                        'data': data_list,
                        'message': 'ITR types retrieved successfully',
                        'error': None,
                        'messages': [],
                        'status_code': response.status_code
                    }
            
            messages = data.get("messages", [])
            has_success = any(msg.get("code") == "EF40003" for msg in messages) and response.status_code == 200
            
            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != "EF40003":
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            
            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success,
                'data': data,
                'message': 'ITR types retrieved successfully' if has_success else ('Failed to retrieve ITR types' if error_text else 'ITR types request completed'),
                'error': error_text,
                'messages': messages,   
                'status_code': response.status_code
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve ITR types: {str(e)}'
            }

    #=========================================================================================
    # GET THE BANK ACCOUNTS
    #=========================================================================================
    
    def get_bank_accounts(self, pan: str) -> Dict[str, Any]:

        """Retrieve bank account details for a given PAN.
        
        Args:
            pan: PAN number

        Returns:
        """

        try:
            url=self.config.base_url + self.config.get_entity_endpoint

            payload={
                "entityNum": pan,
                "header": {
                    "formName": "FO-054-PBACC"
                },
                "serviceName": "myBankAccountService"
            }
            response = self._safe_post(url, json_data=payload, sn="myBankAccountService")
            data, json_err = self._safe_parse_json(response)
        
            if json_err:
                logger.error(f"get_bank_accounts JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to retrieve bank accounts: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            messages = data.get("messages", [])

            has_success = any(msg.get("code") == "EF40003" for msg in messages) and response.status_code == 200

            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != "EF40003":
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)

            error_text = '; '.join(error_messages) if error_messages else None

            data={"active_bank_accounts":data.get("activeBank",[]),
                  "inactive_bank_accounts":data.get("inActiveBank",[]),
                  "failed_bank_accounts":data.get("failedBank",[])
                  }


            return {
                'success': has_success,
                'data': data,
                'message': 'Bank accounts retrieved successfully' if has_success else ('Failed to retrieve bank accounts' if error_text else 'Bank accounts request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }

        except Exception as e:
            logger.error(f"get_bank_accounts failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to retrieve bank accounts: {str(e)}'
            }
        



    def validate_itr_form(self,pan,json_data,itr_type,year,it_sec_cd,filling_type,mode,stringify=False) -> Dict[str, Any]:
        """Validate ITR form for a given PAN and assessment year.
        
        Args:
            pan: PAN number
            json_data: ITR form data in JSON format

        Returns:
            Dict with:
                - success: bool
                - data: dict (validation response data if successful)
                - error: str (error message if failed)
                - message: str (description)


        """

        try:

            url = f"{self.config.base_url}/iec/itrweb/auth/v0.1/returns/submit/wzrd/xml"
            
            # Extract ITR data from the nested structure
            itr_data = json_data
            
            payload = {
                    "header": {
                        "formName": itr_type,
                        "mimeType": "json",
                        "entityType": "P",
                        "entityNum": pan,
                        "ay": str(year),                    # String, not int
                        "createdBy": pan,
                        "filingMode": mode,                # "O"=Online, "A"=Offline
                        "filingTypeCd": filling_type,                # "U"=Updated, "O"=Original
                        "incomeTaxSecCd": it_sec_cd,
                        "submittedBy": "SLF"
                    },
                    "formData": itr_data,  # 👈 STRINGIFY THIS
                    "loggedInUserId": pan
                }
            
            if stringify:
            
                payload["formData"] = json.dumps(payload["formData"], separators=(',', ':'))

           

            response = self._safe_post(url, json_data=payload, sn="")
            
            data, json_err = self._safe_parse_json(response)

            # print(f"Validation Response Data: {data}")
            
            if json_err:
                logger.error(f"validate_itr_form JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to validate ITR form: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            
            messages = data.get('messages', [])
            errors = data.get('errors', [])
            response_code = data.get('responseCode')
            arn_number = data.get('arnNumber')
            transaction_no = data.get('transactionNo')

            # Check for error messages
            error_messages = []
            for msg in messages:
                if str(msg.get('type', '')).lower() == 'error' or msg.get('code', '').startswith('ERR'):
                    desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                    if desc:
                        error_messages.append(desc)

            # Add errors from 'errors' list
            if errors:
                for err in errors:
                    if isinstance(err, dict):
                        desc = err.get('desc') or err.get('description') or err.get('message', '')
                        if desc:
                            error_messages.append(desc)
                    elif isinstance(err, str):
                        error_messages.append(err)

            # Determine success
            has_success = (
                (response_code in (0, '0', 'SUCCESS', None)) and
                arn_number and transaction_no and
                not error_messages and
                not errors
            )

            error_text = '; '.join(error_messages) if error_messages else None
            
            return {
                'success': has_success,
                'data': data,
                'arnNumber': arn_number,
                'transactionNo': transaction_no,
                'message': 'ITR form validated successfully' if has_success else ('Form validation failed' if error_text else 'Form validation completed'),
                'error': error_text,
                'messages': messages,
                'errors': errors,
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"validate_itr_form failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to validate ITR form: {str(e)}'
            }
    def validate_itr_dsc(self,pan,json_data,form_type,year,ackn) -> Dict[str, Any]:
        """Validate ITR form with DSC for a given PAN and assessment year.
        
        Args:
            pan: PAN number
            json_data: ITR form data in JSON format

        Returns:
            Dict with:
                - success: bool
                - data: dict (validation response data if successful)
                - error: str (error message if failed)
                - message: str (description)


        """

        try:
            url = f"{self.config.base_url}/iec/itrweb/auth/v0.1/returns/validation/dsc"

            payload = {
                "ackNum": ackn,
                "ay": str(year),
                "entityNum": pan,
                "flag44ab": "N",
                "formCode": form_type
            }

            response = self._safe_post(url, json_data=payload, sn="")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"validate_itr_dsc JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to validate ITR form with DSC: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            # print(f"DSC Validation Response Data: {data}")

            messages = data.get('messages', [])
            has_success = any(msg.get('code') == MessageCode.SUCCESS.value for msg in messages) or response.status_code == 200

            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != MessageCode.SUCCESS.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)

            error_text = '; '.join(error_messages) if error_messages else None

            return {
                'success': has_success,
                'data': data,
                'message': 'ITR form with DSC validated successfully' if has_success else ('Validation failed' if error_text else 'Validation completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            logger.error(f"validate_itr_dsc failed: {e}")
        except Exception as e:
    
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to validate ITR form with DSC: {str(e)}'
            }

    def everify_itr_later(self,pan,ackn_no,year,form_cd) -> Dict[str, Any]:
        """E-verify ITR later process.
        
        Returns:
            Dict with:
                - success: bool
                - data: dict (response data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """

        try:
            url =f"{self.config.base_url}/iec/verificationservices/auth/saveEntity"
            
            
            payload = {
                "ackNum": ackn_no,
                "assessmntYr": str(year),
                "formCd": form_cd,  # You may need to provide the correct form code here
                "loggedInUserId": pan,
                "moduleCode": "ITR",                
                "selectionFlag": "L",
                "panNumber":pan,
                "serviceName": "everifyLater",
                "verifPan": pan,               
            }

            # print(f"Payload for everify_itr_later: {payload}")

            response = self._safe_post(url, json_data=payload, sn="everifyLater")
            data, json_err = self._safe_parse_json(response)
        
            if json_err:
                logger.error(f"everify_itr_later JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to e-verify ITR later: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            

            messages = data.get('messages', [])
            has_success = any(msg.get('code') == "DATA_INSERTION_SUCCESS_FLAG" for msg in messages) or response.status_code == 200

            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != MessageCode.SUCCESS.value and msg.get('code') != "DATA_INSERTION_SUCCESS_FLAG":
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)

            error_text = '; '.join(error_messages) if error_messages else None

            # Accept DATA_INSERTION_SUCCESS_FLAG as success
            has_success = has_success or any(msg.get('code') == "DATA_INSERTION_SUCCESS_FLAG" for msg in messages)

            return {
                'success': has_success,
                'data': data,
                'message': 'E-verify ITR later processed successfully' if has_success else ('Failed to e-verify ITR later' if error_text else 'E-verify ITR later request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
         
        except Exception as e:
            logger.error(f"everify_itr_later failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to e-verify ITR later: {str(e)}'
            }
    
    def submit_itr_final_step(self, data, ackn_no, year, filling_type, form_name, sec_code, pan,stringify=False) -> Dict[str, Any]:

        """
        Docstring for submit_itr_final_step
        
        :param s: Description
        :param data: Description
        :param ackn_no: Description
        :param year: Description
        :param form_type: Description
        :param sec_code: Description
        :param pan: Description
        :return: Description
        :rtype: Any
        """

        try:
            url = f"{self.config.base_url}/iec/itrweb/auth/v0.1/returns/submit/wzrd"
            payload = {
            "formData": data,
            "header": {
            "ackNum": str(ackn_no),
            "ay": str(year),
            "createdBy": pan,
            "entityNum": pan,
            "entityType": "P",
            "filingMode": "OF",
            "filingTypeCd": filling_type,
            "formName": form_name,
            "incomeTaxSecCd": sec_code,
            "mimeType": "json",
            "submittedBy": "SLF"
        },
        "loggedInUserId": pan
    }
  # 'data' should be the full payload dictionary as provided

            if stringify:
                payload["formData"] = json.dumps(payload["formData"], separators=(',', ':'))

            # print(payload)

            
            response = self._safe_post(url, json_data=payload, sn="")

            # print(f"Final Submission Response: {response.text}")
            
            resp_data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"submit_itr_final_step JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to submit ITR final step: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }
            messages = resp_data.get('messages', [])
            has_success = any(msg.get('code') == MessageCode.SUCCESS.value for msg in messages) or response.status_code == 200

            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get('code') != MessageCode.SUCCESS.value:
                        desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                        if desc:
                            error_messages.append(desc)
            error_text = '; '.join(error_messages) if error_messages else None

            return {
                'success': has_success,
                'data': resp_data,
                'message': 'ITR final step submitted successfully' if has_success else ('Failed to submit ITR final step' if error_text else 'ITR final step request completed'),
                'error': error_text,
                'messages': messages,
                'status_code': response.status_code
            }
            
        except Exception as e:
            logger.error(f"submit_itr_final_step failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to submit ITR final step: {str(e)}'
            }

    def start_online_return(self,pan,year,form_type,sec_code, verify_now=True) -> Dict[str, Any]:
        """Start online return process.
        
        Returns:
            Dict with:
                - success: bool
                - data: dict (response data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """

        try:
            url =f"{self.config.base_url}/iec/itrweb/auth/v0.1/returns/insertSla/wzrd"
            # print("Starting online return process is currently not implemented.")
            payload = {
                "status": "start",
                "entityNum": pan,
                "ay": year,
                "formTypeCd": form_type,
                "incmTaxCd": sec_code,    
                "loggedInUserId": pan
            }

            # print(f"Payload for start_online_return: {payload}")

            response = self._safe_post(url, json_data=payload, sn="")

            return {
                'success': False,
                'error': 'Start online return not implemented',
                'message': 'Starting online return process is currently not implemented.'
            }
        except Exception as e:
            logger.error(f"start_online_return failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to start online return process: {str(e)}'
            }


    def submit_itr_form(self,pan,itr_type,year,form_type,json_link,fetch_from_url=True,verify_now=True) -> Dict[str, Any]:
        """Submit ITR form for a given PAN and assessment year.
        
        Args:
            pan: PAN number
            form_type: Type of ITR form (e.g., "ITR1", "ITR2")
            itr_type: Type of ITR submission (e.g., "original", "revised", "belated")
            year: Assessment year
            json_link: Path to the JSON file containing ITR data
        Returns:
            Dict with:
                - success: bool
                - data: dict (submission response data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """
        # Load JSON data from the provided json_link (file path)


        
        if fetch_from_url:
            try:
                if json_link.startswith("http://") or json_link.startswith("https://"):
                    headers = {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": "application/json, text/plain, */*",
                        "Referer": "https://app.easyreturn.in/"
                    }
                    resp = requests.get(json_link, headers=headers)
                    resp.raise_for_status()
                    json_data = resp.json()
                else:
                    with open(json_link, "r", encoding="utf-8") as f:
                        json_data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load JSON data from {json_link}: {e}")
                return {
                    'success': False,
                    'error': f"Failed to load JSON data: {e}",
                    'message': f"Could not read ITR JSON file: {json_link}"
                }
        else:
            json_data = json_link
            
        try:
            self.itr_year=year
            self.json_data=json_data


            year_dear=self.get_assesment_years()

            if not year_dear['success']:
                return {
                    'success': False,
                    'error': year_dear.get('error'),
                    'message': f"Failed to fetch assessment years: {year_dear.get('error')}"
                }
                        
            ay=year_dear['data']
            for item in ay:
                if int(item['assment_year_cd']) == int(year):
                    assessment_year=item['assment_year_cd']
                    mode=item['itr_mode']        
        
            types=self.get_return_types(year=assessment_year,is_active="Y")

            return_types=types['data']
            # print(f"Return Types: {return_types}")
            for item in return_types:
                sec_codes={"modified":"139(9A)","updated":"139(8A)","after-notice":"92CD","belated":"139(4)","original":"139(1)","revised":"139(5)"}
                if str(item['sec_cd'])==str(sec_codes.get(itr_type)):
                    
                    self.sec_cd=item['sec_cd']
                    self.it_sec_cd=item['it_sec_cd']
                    self.filling_type=item['filing_type']

            if not self.sec_cd:
                return {

                    'success': False,
                    'error': "Invalid return type",
                    'message': "Return type not found"
                }

            forms=self.get_itr_types(year=assessment_year,sec_cd=self.sec_cd,is_active="Y",audit_flag=False)

            itr_forms=forms['data']
            for item in itr_forms:
                if str(item['form_cd'])==str(form_type):
                    self.form_cd=item['form_cd']



            validate_response=self.validate_itr_form(pan,json_data,self.form_cd,year,self.it_sec_cd,self.filling_type,mode,stringify=True )        
            if not validate_response['success'] :
                return{
                    'success': False,
                    'error': validate_response.get('error'),
                    'message': f"Form validation failed: {validate_response.get('error')}",
                    "details":validate_response.get('data'),

                }
            
            dsc=self.validate_itr_dsc(pan,json_data,self.form_cd,year,validate_response['arnNumber'])
            if not dsc['success']:
                return{
                    'success': False,
                    'error': dsc.get('error'),
                    'message': f"DSC validation failed: {dsc.get('error')}"

                }

            if self.sec_cd in ["139(1)","139(5)","139(4)"] and verify_now == False:
                everify_response=self.everify_itr_later(pan,validate_response['arnNumber'],year,self.form_cd)

                if not everify_response['success']:
                    
                    return{
                        'success': False,
                        'error': everify_response.get('error'),
                        'message': f"E-verify ITR later failed: {everify_response.get('error')}" 
                        }

                final_response=self.submit_itr_final_step(json_data,validate_response['arnNumber'],year,self.filling_type,self.form_cd,self.it_sec_cd,pan,stringify=True)
                if not final_response['success']:
                    return{
                        'success': False,
                        'error': final_response.get('error'),
                        'message': f"Final ITR submission failed: {final_response.get('error')}" 
                        }
                return{
                    'success': True,
                    'data': final_response.get('data'),
                    'message': f"ITR form submitted successfully.",
                    'error': None
                    }    
        
            else :

                check_aadhaar=self.check_aadhaar_linked(pan)

                if check_aadhaar['success'] is False:
                    return {
                        'success': False,
                        'error': check_aadhaar.get('error'),
                        'message': f"Aadhaar linkage check failed: {check_aadhaar.get('error')}"
                    }
                

                adhar_otp=self.send_otp_aadhaar(pan)

                if not adhar_otp['success']:
                    return {
                        'success': False,
                        'error': adhar_otp.get('error'),
                        'message': f"Failed to send Aadhaar OTP: {adhar_otp.get('error')}"
                    }
                


                final_response={
                    "ackn_no":validate_response['arnNumber'],
                    "transaction_no":validate_response['transactionNo'],
                    "aadhar_txn_id":adhar_otp.get('data',{}).get('aadhaarTxnId')
                    

                }
                
                return {
                    'success': True,
                    'data': final_response,
                    'message': 'Aadhaar OTP sent successfully. Please verify to complete submission.',
                    'error': None
                }


        except Exception as e:
             
            logger.error(f"submit_itr_form failed during validation: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to submit ITR form: {str(e)}'
            }
        
    def get_itr_receipt(self, ackn_no, year, pan) -> Dict[str, Any]:
        try:
            url = f"{self.config.base_url}/iec/itrweb/auth/v0.1/returns/pdf"
            payload = {
                "ackNum": ackn_no,
                "ay": str(year),
                "loggedInUserId": pan
            }
            
            response = self._safe_post(url, json_data=payload, sn="")
            
            # ✅ Check Content-Disposition instead of Content-Type
            content_disposition = response.headers.get("Content-Disposition", "").lower()
            is_pdf = ".pdf" in content_disposition  # Checks for filename=...pdf
            
            if is_pdf:
                import os
                user_dir = os.path.join("data", "users", pan)
                os.makedirs(user_dir, exist_ok=True)
                file_path = os.path.join(user_dir, f"ITR_Receipt_{ackn_no}_{year}.pdf")
                
                # ✅ requests library auto-decompresses gzip automatically
                with open(file_path, "wb") as f:
                    f.write(response.content)  # Binary data (already decompressed by requests)
                
                return {
                    'success': True,
                    'file': file_path,
                    'message': 'ITR receipt PDF downloaded successfully.'
                }
            else:
                try:
                    data = response.json()
                except Exception:
                    data = response.text
                
                return {
                    'success': False,
                    'error': f'Unexpected response. Content-Disposition: {content_disposition}',
                    'message': 'Failed to download ITR receipt.',
                    'data': data
                }
        
        except Exception as e:
            logger.error(f"get_itr_receipt failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to get ITR receipt: {str(e)}'
            }

    def aes_redirection(self,pan,year):
        '''

        Handle AES redirection for a given PAN and assessment year.

        Params:
            pan (str): PAN number
            year (int): Assessment year
        Returns:

        
        
        
        '''

        try:
            url=self.config.base_url + self.config.aes_redirection_endpoint
            payload={
                "pan": pan,
                "ay": str(year)
            }

            response = self._safe_post(url, json_data=payload, sn="")

            if response.status_code == 200:

                
                return {
                    'success': True,
                    'data': response.json(),
                    'message': 'AES redirection successful.'
                }
        except Exception as e:
            logger.error(f"aes_redirection failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to handle AES redirection: {str(e)}'
            }
            


    
    def view_26_aes(self,pan,year) -> Dict[str, Any]:
        """View Form 26AS for a given PAN and assessment year.
        
        Args:
            pan: PAN number
            year: Assessment year

        
        Returns:
            Dict with:
                - success: bool
                - data: dict (Form 26AS data if successful)
                - error: str (error message if failed)
                - message: str (description)
        """


        try:
            data=self.aes_redirection(pan,year)

            print(data)

            if not data['success']:
                return {
                    'success': False,
                    'error': data.get('error'),
                    'message': f"Failed to fetch AES redirection data: {data.get('error')}"
                }
            aes_data=data.get('data',{})

            url="https://services.tdscpc.gov.in/serv/view26AS.xhtml"

            payload={
                "data":aes_data.get('data',''),
                "signature":aes_data.get('signature','')
                }


            response = self._safe_post(url, data=payload, sn="")
            print(response.status_code)

            if response.status_code == 200:
                return {
                    'success': True,
                    'data': response.text,
                    'message': 'Form 26AS retrieved successfully.'
                }
            else:
                return {
                    'success': False,
                    'error': f"HTTP {response.status_code}",
                    'message': 'Failed to retrieve Form 26AS.'
                }

        except Exception as e:
            logger.error(f"view_26_aes failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to view Form 26AS: {str(e)}'
            }



    def aadhar_pan_linkage(self,pan,aadhaar_number) -> Dict[str, Any]:
        """Check Aadhaar-PAN linkage status for a given PAN.
        
        Args:
            pan: PAN number     

            """
        
        try:
            url = f"{self.config.base_url}/iec/servicesapi/saveEntity"

            # aadhaar_number = getattr(self, "aadhaar_number", None)
            
            if not aadhaar_number:
                return {
                    'success': False,
                    'error': 'Aadhaar number not provided',
                    'message': 'Aadhaar number is required for linkage validation'
                }

            payload = {
                "preLoginFlag": "Y",
                "aadhaarNumber": aadhaar_number,
                "pan": pan,
                "serviceName": "linkAadhaarValidationService",
                "createdBy": "linkAadhaarValidationService",
                "updatedBy": "linkAadhaarValidationService",
                "createdByUser": pan,
                "updatedByUser": pan
            }

            try:
                response = self._safe_post(url, json_data=payload, sn="linkAadhaarValidationService")
                data, json_err = self._safe_parse_json(response)
               
                if json_err:
                    logger.error(f"aadhar_pan_linkage JSON parse error: {json_err}")
                    return {
                        'success': False,
                        'error': json_err,
                        'message': f'Failed to check Aadhaar-PAN linkage: {json_err}',
                        'status_code': getattr(response, 'status_code', None)
                    }

                messages = data.get("messages", [])
                
                has_success = any(msg.get("code") in ["EF40127","EF40122"] for msg in messages) and response.status_code == 200

                self.payment_verified= any(msg.get("code") == "EF40122" for msg in messages)

                    

                error_messages = []
                if not has_success:
                    for msg in messages:
                        if msg.get('code') != MessageCode.SUCCESS.value:
                            desc = msg.get('desc') or msg.get('description') or msg.get('message', '')
                            if desc:
                                error_messages.append(desc)

                error_text = '; '.join(error_messages) if error_messages else None

                return {
                    'success': has_success,
                    'data': data,
                    'message': 'Aadhaar-PAN linkage validated successfully' if has_success else ('Failed to validate linkage' if error_text else 'Linkage validation completed'),
                    'error': error_text,
                    'messages': messages,
                    'status_code': response.status_code,
                    'payment_verify': self.payment_verified
                }
            except Exception as e:
                logger.error(f"aadhar_pan_linkage failed: {e}")
                return {
                    'success': False,
                    'error': str(e),
                    'message': f'Failed to check Aadhaar-PAN linkage: {str(e)}'
                }
            
        except Exception as e:
            logger.error(f"aadhar_pan_linkage failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to check Aadhaar-PAN linkage: {str(e)}'
            }

    # def verify_link_pan_adhaar(self,mobile) -> Dict[str, Any]:
    #     """
    #     Verify the linkage between PAN and Aadhaar for a given mobile number.
    #     and continue 
    #     """

    #     pass


    

  
    def generate_payment_otp(self, pan: str, mobile: str, area_cd: str = "91") -> Dict[str, Any]:
        """Generate OTP for payment authori
        
        zation for a given PAN."""
        try:
            url = f"{self.config.base_url}/iec/paymentapi/commapi/generateOtp"
            payload = {
                "areaCd": area_cd,
                "formName": "PO-03-PYMNT",
                "mobNo": mobile,
                "panNumber": pan,
                "serviceName": "paymentOtpService"
            }

            response = self._safe_post(url, json_data=payload, sn="paymentOtpService")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"generate_payment_otp JSON parse error: {json_err}")
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to generate payment OTP: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            messages = data.get("messages", [])
            errors = data.get("errors", [])
            req_id = data.get("reqId") or data.get("requestId")
            pan_number = data.get("panNumber") or pan
            mob_no = data.get("mobNo")
            area_cd = data.get("areaCd")
            otp_status = data.get("otp")

            has_success = (
                (otp_status or "").upper() == "GENERATED"
                or any(msg.get("code") == MessageCode.SUCCESS.value for msg in messages)
                and response.status_code == 200
            )

            # Persist useful payment context
            self.payment_req_id = req_id
            self.payment_pan = pan_number
            self.payment_mobile = mob_no
            self.payment_area_cd = area_cd
            self.payment_otp_status = otp_status

            error_messages = []
            for msg in messages:
                if msg.get("code") != MessageCode.SUCCESS.value:
                    desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                    if desc:
                        error_messages.append(desc)
            if errors:
                for err in errors:
                    if isinstance(err, dict):
                        desc = err.get("desc") or err.get("description") or err.get("message", "")
                        if desc:
                            error_messages.append(desc)
                    elif isinstance(err, str):
                        error_messages.append(err)

            error_text = "; ".join(error_messages) if error_messages else None

            return {
                "success": has_success,
                "data": data,
                "message": "Payment OTP generated successfully"
                if has_success
                else ("Failed to generate payment OTP" if error_text else "Payment OTP request completed"),
                "error": error_text,
                "messages": messages,
                "status_code": response.status_code,
            }

        except Exception as e:
            logger.error(f"generate_payment_otp failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to generate payment OTP: {str(e)}",
            }

    def validate_payment_otp(self,otp):
        """Validate OTP generated for payment authorization."""
        try:
            url = f"{self.config.base_url}/iec/paymentapi/commapi/validateOtp"

            payload = {
            "panNumber": getattr(self, "payment_pan", None),
            "otp": otp,
            "mobNo": getattr(self, "payment_mobile", None),
            "serviceName": "paymentOtpService",
            "formName": "PO-03-PYMNT",
            "reqId": getattr(self, "payment_req_id", None),
            }

            # allow caller to pass explicit values via kwargs-like attributes
            if not payload["panNumber"] or not payload["reqId"]:
                return {
                    "success": False,
                    "error": "Missing payment OTP context. Generate and validate OTP first.",
                    "message": "Payment OTP validation failed: missing context.",
                }

            response = self._safe_post(url, json_data=payload, sn="paymentOtpService")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"validate_payment_otp JSON parse error: {json_err}")
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to validate payment OTP: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            errors = data.get("errors", [])

            has_success = (
            any(msg.get("code") == "EF00015" for msg in messages)
            or str(data.get("otp", "")).upper() == "VALIDATED"
            or response.status_code == 200
            )

            # Extract error messages if not successful
            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get("code") != MessageCode.SUCCESS.value:
                        desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                        if desc:
                            error_messages.append(desc)
            
            if errors:
                for err in errors:
                    if isinstance(err, dict):
                        desc = err.get("desc") or err.get("description") or err.get("message", "")
                        if desc:
                            error_messages.append(desc)
                    elif isinstance(err, str):
                        error_messages.append(err)

            error_text = "; ".join(error_messages) if error_messages else None

            return {
            "success": has_success,
            "data": data,
            "message": "Payment OTP validated successfully"
            if has_success
            else ("Failed to validate payment OTP" if error_text else "Payment OTP validation completed"),
            "error": error_text,
            "messages": messages,
            "status_code": response.status_code,
            }

        except Exception as e:
            logger.error(f"validate_payment_otp failed: {e}")
            return {
            "success": False,
            "error": str(e),
            "message": f"Failed to validate payment OTP: {str(e)}",
            }


    def create_payment_challan(
        self,
        amount: float = 1000,
        assessment_year: Optional[str] = None,
        bank_code: str = "MAHB",
        major_head: str = "0021",
        minor_head: str = "500",
        sub_minor_hd: str = "APL",
        total_amt_word: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create payment challan after OTP validation."""
        try:
            # derive current AY if not provided, e.g., 2026-27
            if not assessment_year:
                today = datetime.utcnow()
                assessment_year = f"{today.year}-{str(today.year + 1)[-2:]}"

            url = f"{self.config.base_url}/iec/paymentapi/challan/prelogincreatechallan"

            pan = getattr(self, "payment_pan", None)
            mob = getattr(self, "payment_mobile", None)
            req_id = getattr(self, "payment_req_id", None)

            if not (pan or req_id):
                return {
                    "success": False,
                    "error": "Missing payment OTP context. Generate and validate OTP first.",
                    "message": "Payment challan creation failed: missing context.",
                }

            # ensure assessment year is in "YYYY-YY" format using current and next year (e.g., "2026-27")
            current_year = datetime.utcnow().year
            assessment_year = assessment_year or f"{current_year}-{str(current_year + 1)[-2:]}"

            payload = {
                "header": {
                    "formName": "PO-03-PYMNT",
                    "reqId": req_id,
                },
                "formData": {
                    "pan": pan,
                    "paymentMode": "EPY",
                    "subPayMode": "PG",
                    "majorHead": major_head,
                    "minorHead": minor_head,
                    "surCharge": 0,
                    "totalAmt": amount,
                    "assmentYear": assessment_year,
                    "totalAmtWord": total_amt_word or f"INR {amount}",
                    "bankCode": bank_code,
                    "basicTax": 0,
                    "eduCess": 0,
                    "interest": 0,
                    "penalty": 0,
                    "others": amount,
                    "entityName": "",
                    "mobNum": mob,
                    "tileId": "2",
                    "loginType": "PRE",
                    "majorSlNum": "2",
                    "minorSlNum": "20",
                    "natrOfPymnt": "",
                    "subMinorHd": sub_minor_hd,
                },
            }

            response = self._safe_post(url, json_data=payload, sn="prelogincreatechallan")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"create_payment_challan JSON parse error: {json_err}")
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to create payment challan: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            errors=data.get("errors", [])
            has_success = (
                any(msg.get("code") == MessageCode.SUCCESS.value for msg in messages)
                or response.status_code == 200
            )

            # persist challan response context (CRN, status, etc.)
            self.last_challan_response = data
            self.last_challan_crn = data.get("crn")
            self.last_challan_status = data.get("status")
            self.last_challan_amount = data.get("totalAmt")
            self.last_challan_assessment_year = data.get("assmentYear")
            self.last_challan_bank = data.get("bankName") or data.get("bankCd")
            self.last_challan_json_data = data.get("jsonData")

            error_messages = []
            for msg in messages:
                if msg.get("code") != MessageCode.SUCCESS.value:
                    desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                    if desc:
                        error_messages.append(desc)
            if errors:
                for err in errors:
                    if isinstance(err, dict):
                        desc = err.get("desc") or err.get("description") or err.get("message", "")
                        if desc:
                            error_messages.append(desc)
                    elif isinstance(err, str):
                        error_messages.append(err)

            error_text = "; ".join(error_messages) if error_messages else None

            return {
                "success": has_success,
                "data": data,
                "message": "Payment challan created successfully"
                if has_success
                else ("Failed to create payment challan" if error_text else "Payment challan request completed"),
                "error": error_text,
                "messages": messages,
                "status_code": response.status_code,
            }

        except Exception as e:
            logger.error(f"create_payment_challan failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to create payment challan: {str(e)}",
            }

    def view_crn_details(
        self,
        crn: str,
        entity_num: Optional[str] = None,
        req_id: Optional[str] = None,
        ) -> Dict[str, Any]:
        """View CRN details for a generated challan (pre-login)."""
        try:
            url = f"{self.config.base_url}/iec/paymentapi/challan/preloginviewcrndetails"

            payload = {
                "header": {
                    "formName": "PO-03-PYMNT",
                    "reqId": req_id or getattr(self, "payment_req_id", None),
                },
                "formData": {
                    "crn": crn,
                    "entityNum": entity_num or getattr(self, "payment_pan", None),
                },
            }

            response = self._safe_post(url, json_data=payload, sn="preloginviewcrndetails")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"view_crn_details JSON parse error: {json_err}")
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to view CRN details: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            success_flag = bool(data.get("successFlag")) or response.status_code == 200

            # Persist CRN response context
            self.last_crn_response = data
            self.last_crn = data.get("crn") or crn
            self.last_crn_status = data.get("status") or data.get("statusCd")
            self.last_crn_assessment_year = data.get("assessmentYear") or data.get("assmentYear")
            self.last_crn_amount = data.get("totalAmt")
            self.last_crn_bank = data.get("bankName") or data.get("bankCd")
            self.last_crn_json_data = data.get("jsonData")

            error_messages = []
            if not success_flag:
                for msg in messages:
                    if msg.get("code") != MessageCode.SUCCESS.value:
                        desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                        if desc:
                            error_messages.append(desc)
            error_text = "; ".join(error_messages) if error_messages else None

            return {
                "success": success_flag,
                "data": data,
                "message": "CRN details retrieved successfully"
                if success_flag
                else ("Failed to retrieve CRN details" if error_text else "CRN details request completed"),
                "error": error_text,
                "messages": messages,
                "status_code": response.status_code,
            }

        except Exception as e:
            logger.error(f"view_crn_details failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to view CRN details: {str(e)}",
            }

    # ...existing code...

    def create_payment(
        self,
        bank_cd: Optional[str] = None,
        login_type: str = "pre",
        sub_pay_mode: str = "PG",
    ) -> Dict[str, Any]:
        """
        Create payment session with bank gateway using previously generated challan/OTP context.
        Returns the bank redirect URL and a POST payload that can be auto-submitted in the browser.
        """
        try:
            req_id = getattr(self, "payment_req_id", None)
            crn = getattr(self, "last_crn", None) or getattr(self, "last_challan_crn", None)
            pan = getattr(self, "payment_pan", None)
            bank_code = bank_cd or getattr(self, "last_crn_bank", None) or "HDFC"

            if not (req_id and crn and pan):
                return {
                    "success": False,
                    "error": "Missing payment context. Generate/validate OTP and create challan first.",
                    "message": "Payment creation failed: missing reqId, CRN or PAN.",
                }

            url = f"{self.config.base_url}/iec/paymentapi/bankapi/v0.1/challan/pay/create"
            payload = {
                "header": {"formName": "PO-03-PYMNT", "reqId": req_id},
                "formData": {
                    "crn": crn,
                    "bank_cd": bank_code,
                    "loginType": login_type,
                    "subPayMode": sub_pay_mode,
                    "pan": pan,
                },
            }

            response = self._safe_post(url, json_data=payload, sn="challanPayCreate")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"create_payment JSON parse error: {json_err}")
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to create payment: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            status = str(data.get("status", "")).upper()
            has_success = (
                status == "SUCCESS"
                or any(msg.get("code") == MessageCode.SUCCESS.value for msg in messages)
                or response.status_code == 200
            )

            # Bank redirect details
            bank_url = data.get("bankUrl") or data.get("url")
            # reqJson may come as dict or string; if not present, fall back to top-level action/data/hmac
            req_json = data.get("reqJson") or data.get("payload") or {
                "action": data.get("action"),
                "data": data.get("data"),
                "hmac": data.get("hmac"),
            }

           
           
            error_messages = []
            for msg in messages:
                if msg.get("code") != MessageCode.SUCCESS.value:
                    desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                    if desc:
                        error_messages.append(desc)
            error_text = "; ".join(error_messages) if error_messages else None

            return {
                "success": has_success,
                "data": data,
                "message": "Payment created successfully"
                if has_success
                else ("Failed to create payment" if error_text else "Payment creation completed"),
                "error": error_text,
                "messages": messages,
                "status_code": response.status_code,
                "redirect_url": bank_url,
                "reqjson": req_json,
                
            }

        except Exception as e:
            logger.error(f"create_payment failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to create payment: {str(e)}",
            }

    # ...existing code...

        # ...existing code...

    def add_tax_applicable_details(self, pan, year="2026-27",old_regime=True) -> Dict[str, Any]:
        """Create advance tax payment draft (saved draft)."""
        try:
            url = f"{self.config.base_url}/iec/paymentapi/auth/challan/savedraft"

            self.payment_pan = pan
            self.payment_year = year
            self.old_regime = old_regime

            # decide minor head from AY prefix (YYYY-YY)
            current_year = datetime.utcnow().year
            year_prefix = str(year).split("-")[0].strip()
            if old_regime:
                self.minor_head = "300" if year_prefix == str(current_year) else "300"
                self.minor_sl_no= "3" if year_prefix ==  str(current_year) else "3"
            else:
                self.minor_head = "100" if year_prefix == str(current_year) else "300"
                self.minor_sl_no= "1" if year_prefix ==  str(current_year) else "3"

            payload = {
                "header": {"formName": "PO-03-PYMNT"},
                "formData": {
                    "actType": "O" if old_regime else "N",
                    "pan": getattr(self, "payment_pan", None),
                    "tileId": "2",
                    "majorHead": "0021",
                    "minorHead": self.minor_head,
                    
                    "basicTax": "",
                    "surCharge": "",
                    "eduCess": "",
                    "interest": "",
                    "penalty": "",
                    "others": "",
                    "totalAmt": "",
                    "totalAmtWord": "",
                    "paymentMode": "",
                    "subPayMode": "",
                    "bankCd": "",
                    "pymntRefNum": "",
                    "majorSlNum": "2",
                    "minorSlNum": self.minor_sl_no,
                    "taxPayerName": "",
                    "category": "",
                    "natrOfPymnt": "",
                    "subMinorHd": "",
                    "pageName": "addTaxApplicableDetails",
                    "createdByUser": pan,
                },
            }
            if old_regime:
                payload["formData"]["assmentYear"] = year
            else:
                payload["formData"]["taxYear"] = year

            response = self._safe_post(url, json_data=payload, sn="")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"create_advance_payment_draft JSON parse error: {json_err}")
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to create advance payment draft: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            success_flag = bool(data.get("successFlag"))
            has_success = (
                success_flag
                or any(msg.get("code") == MessageCode.SUCCESS.value for msg in messages)
                or response.status_code == 200
            )

            # persist payment reference if present
            self.payment_refrence_no = data.get("pymntRefNum") or getattr(self, "last_payment_ref_num", None)

            error_messages = []
            if not has_success:
                for msg in messages:
                    if msg.get("code") != MessageCode.SUCCESS.value:
                        desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                        if desc:
                            error_messages.append(desc)

            error_text = "; ".join(error_messages) if error_messages else None

            return {
                "success": has_success,
                "data": data,
                "message": "Advance payment draft created successfully"
                if has_success
                else ("Failed to create advance payment draft" if error_text else "Advance payment draft request completed"),
                "error": error_text,
                "messages": messages,
                "status_code": response.status_code,
                "pymntRefNum": data.get("pymntRefNum"),
            }

        except Exception as e:
            logger.error(f"create_advance_payment_draft failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to create advance payment draft: {str(e)}",
            }

        # ...existing code...

    def add_payment_details(
        self,
       payment_detils: Dict[str, Any],
    ) -> Dict[str, Any]:

        url = f"{self.config.base_url}/iec/paymentapi/auth/challan/savedraft"

        payload = {
            "header": {"formName": "PO-03-PYMNT"},
            "formData": {
                "pan": self.payment_pan,
                "actType": "O" if self.old_regime else "N",
                "tileId": "2",
                "majorHead": "0021",
                "minorHead": "100",
                "majorSlNum": "2",
                "minorSlNum": self.minor_sl_no,
                "basicTax": payment_detils.get("basicTax")   ,
                "surCharge": payment_detils.get("surCharge"),
                "eduCess": payment_detils.get("eduCess"),
                "interest": payment_detils.get("interest"),
                "penalty": payment_detils.get("penalty"),
                "others": payment_detils.get("others"),
                "totalAmt": payment_detils.get("totalAmt"),
                
                "totalAmtWord": payment_detils.get("totalAmtWord"),
                "paymentMode": "",
                "subPayMode": "",
                "bankCd": "",
                "pymntRefNum": self.payment_refrence_no,
                "category": "",
                "natrOfPymnt": "",
                "subMinorHd": "",
                "pageName": "addTaxBreakupDetails",
                "createdByUser": self.payment_pan,
            },
        }
        if self.old_regime:
            payload["formData"]["assmentYear"] = self.payment_year
        else:
            payload["formData"]["taxYear"] = self.payment_year

        response = self._safe_post(url, json_data=payload, sn="")

        data, json_err = self._safe_parse_json(response)
        if json_err:
            logger.error(f"add_payment_details JSON parse error: {json_err}")
            return {
                "success": False,
                "error": json_err,
                "message": f"Failed to add payment details: {json_err}",
                "status_code": getattr(response, "status_code", None),
            }

        messages = data.get("messages", [])
        success_flag = bool(data.get("successFlag"))
        has_success = (
            success_flag
            or any(msg.get("code") == MessageCode.SUCCESS.value for msg in messages)
            or response.status_code == 200
        )

        error_messages = []
        if not has_success:
            for msg in messages:
                if msg.get("code") != MessageCode.SUCCESS.value:
                    desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                    if desc:
                        error_messages.append(desc)

        error_text = "; ".join(error_messages) if error_messages else None

        return {
            "success": has_success,
            "data": data,
            "message": "Payment details added successfully"
            if has_success
            else ("Failed to add payment details" if error_text else "Add payment details request completed"),
            "error": error_text,
            "messages": messages,
            "status_code": response.status_code,
        }

        # ...existing code...

    def add_bank_details(
        self,
        payment_details: Dict[str, Any],
        payment_mode: str = "EPY",
        sub_pay_mode: str = "PG",
        bank_cd: str = "MAHB",
        payment_mode_desc: str = "e-Payment/Internet Banking",
        eff_bank_details: str = "",
    ) -> Dict[str, Any]:
        """Add bank/payment details to payment draft (saved draft)."""
        url = "https://eportal.incometax.gov.in/iec/paymentapi/auth/challan/savedraft"

        
        payload = {
            "header": {"formName": "PO-03-PYMNT"},
            "formData": {
                "pan": self.payment_pan,
                "actType": "O" if self.old_regime else "N",
                
                "majorHead": "0021",
                "majorSlNum": "2",
                "minorHead": self.minor_head,
                "minorSlNum": self.minor_sl_no,
                "tileId": "2",
                "basicTax": payment_details.get("basicTax")  ,
                "surCharge": payment_details.get("surCharge"),
                "eduCess": payment_details.get("eduCess", 0),
                "interest": payment_details.get("interest", 0),
                "penalty": payment_details.get("penalty", 0),
                "others": payment_details.get("others", 0),
                "totalAmt": payment_details.get("totalAmt"),
                "totalAmtWord": payment_details.get("totalAmtWord"),
                "paymentMode": payment_mode,
                "subPayMode": sub_pay_mode,
                "bankCd": bank_cd,
                "pymntRefNum": self.payment_refrence_no,
                "paymentModeDesc": payment_mode_desc,
                "category": "",
                "natrOfPymnt": "",
                "subMinorHd": "",
                "effBankDetails": eff_bank_details,
                "pageName": "addPaymentDetails",
                "createdByUser": self.payment_pan,
            },
        }

        if self.old_regime:
            payload["formData"]["assmentYear"] = self.payment_year
        else:
            payload["formData"]["taxYear"] = self.payment_year



        response = self._safe_post(url, json_data=payload, sn="")
        data, json_err = self._safe_parse_json(response)
        if json_err:
            logger.error(f"add_bank_details JSON parse error: {json_err}")
            return {
                "success": False,
                "error": json_err,
                "message": f"Failed to add bank details: {json_err}",
                "status_code": getattr(response, "status_code", None),
            }

        messages = data.get("messages", [])
        success_flag = bool(data.get("successFlag"))
        has_success = (
            success_flag
            or any(msg.get("code") == MessageCode.SUCCESS.value for msg in messages)
            or response.status_code == 200
        )

        error_messages = []
        if not has_success:
            for msg in messages:
                if msg.get("code") != MessageCode.SUCCESS.value:
                    desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                    if desc:
                        error_messages.append(desc)

        error_text = "; ".join(error_messages) if error_messages else None

        return {
            "success": has_success,
            "data": data,
            "message": "Bank details added successfully"
            if has_success
            else ("Failed to add bank details" if error_text else "Add bank details request completed"),
            "error": error_text,
            "messages": messages,
            "status_code": response.status_code,
        }

        # ...existing code...

    def create_advance_payment_challan(self, payment_details: Dict[str, Any]) -> Dict[str, Any]:
        """Create advance payment challan (post-login)."""
        try:
            url = "https://eportal.incometax.gov.in/iec/paymentapi/auth/challan/create"

            payload = {
                "header": {"formName": "PO-03-PYMNT"},
                "formData": {
                    "pan": self.payment_pan,
                    "paymentMode": "EPY",
                    "subPayMode": "PG",
                    "majorHead": "0021",
                    "actType": "O" if self.old_regime else "N",
                    "minorHead": int(self.minor_head),
                    "surCharge": 0,
                    "totalAmt": payment_details.get("totalAmt"),
                    "totalAmtWord": payment_details.get("totalAmtWord"),
                    "bankCode": "MAHB",
                    "basicTax": payment_details.get("basicTax"),
                    "eduCess": payment_details.get("eduCess", 0),
                    "interest": payment_details.get("interest", 0),
                    "penalty": payment_details.get("penalty", 0),
                    "others": payment_details.get("others", 0),
                    "pymntRefNum": self.payment_refrence_no,
                    "tileId": "2",
                    "loginType": "post",
                    "majorSlNum": "2",
                    "minorSlNum": self.minor_sl_no,
                    "subMinorHd": "",
                    "createdByUser": self.payment_pan,
                },
            }
            if self.old_regime:
                payload["formData"]["assmentYear"] = self.payment_year
            else:
                payload["formData"]["taxYear"] = self.payment_year

            response = self._safe_post(url, json_data=payload, sn="")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to create advance payment challan: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }
            success=bool(data.get("successFlag")) or response.status_code == 200
            messages = data.get("messages", [])
            error_messages = []
            if not success:
                for msg in messages:
                    if msg.get("code") != MessageCode.SUCCESS.value:
                        desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                        if desc:
                            error_messages.append(desc)
            error_text = "; ".join(error_messages) if error_messages else None

            self.challan_data=data
            return {
                "success": success,
                "data": data,
                "message": "Advance payment challan created successfully"
                if success
                else ("Failed to create advance payment challan" if error_text else "Advance payment challan request completed"),

                "error": error_text,
                "messages": messages,
                "status_code": response.status_code,
            }
        

        except Exception as e:
            logger.error(f"create_advance_payment_challan failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to create advance payment challan: {str(e)}",
            }
    

    def  advance_payment_bank_url(self):

        try:

            url="https://eportal.incometax.gov.in/iec/paymentapi/bankapi/auth/v0.1/challan/pay/create"

                # Bank redirect details

            print(self.challan_data)

            payload = {
                "crn": self.challan_data.get("crn"),
                "bank_cd": "MAHB",
                "loginType": "post",
                "subPayMode": "PG",
                "pan": self.payment_pan,
                "loginPan": self.payment_pan,
                "loggedInUserId": self.payment_pan,
            }

            print(payload)

            response = self._safe_post(url, json_data=payload, sn="")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to get bank URL: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }
            # Bank redirect details
            bank_url = data.get("bankUrl") or data.get("url")
            # reqJson may come as dict or string; if not present, fall back to top-level action/data/hmac
            req_json = data.get("reqJson") or data.get("payload") or {
                "action": data.get("action"),
                "data": data.get("data"),
                "hmac": data.get("hmac"),
            }


            return {
                "success": response.status_code == 200,
                "data": data,
                "message": "Bank URL fetched successfully" if response.status_code == 200 else "Failed to get bank URL",
                "status_code": response.status_code,
                "bank_url": bank_url,
                "reqjson": req_json,
                "crn": self.challan_data.get("crn"),
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to get bank URL for advance payment: {str(e)}",
            }
        

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to get bank URL for advance payment: {str(e)}",
            }

    def get_user_profile(self):
        try:
            url="https://eportal.incometax.gov.in/iec/servicesapi/auth/saveEntity"

            payload={"serviceName":"userProfileService","userId":self.credentials.pan }

            response = self._safe_post(url, json_data=payload, sn="userProfileService") 

            data, json_err = self._safe_parse_json(response)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to get user profile: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Unexpected status code: {response.status_code}",
                    "message": "Failed to get user profile",
                    "status_code": response.status_code,
                }
         
            self.user_profile=data
            return {
                "success": True,
                "data": data,
                "message": "User profile fetched successfully", 
                "status_code": getattr(response, "status_code", None),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to get user profile: {str(e)}",
            }



    def get_branch_details(self,ifsc):
        try:
            url="https://eportal.incometax.gov.in/iec/master/getDetails"
            payload={
                "tokenName":"ifsc",
                "requiredColumns":["branch_txt","bank_name"],
                "dependentField":{"ifsc_cd":ifsc}
                }
            print("Fetching branch details with payload:", payload)
            response = self._safe_post(url, json_data=payload, sn="ifsc")
            data, json_err = self._safe_parse_json(response)
            print("Branch details response:", data)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to get branch details: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Unexpected status code: {response.status_code}",
                    "message": "Failed to get branch details",
                    "status_code": response.status_code,
                }

            self.branch_details=data.get('data')[0]
            

                
            
            return {
                "success": True,
                "data": data,
                "message": "Branch details fetched successfully",
                "status_code": getattr(response, "status_code", None),
            }
        except Exception as e:
            return{
                "success":False,
                "error":str(e),
                "message":f"Failed to get branch details: {str(e)}"
            }

    # def get_bank_entity_details(self):
    #     try:
    #         url=""
        # ...existing code...
    
    def add_bank_account(self,bank_details):
        try:
            url="https://eportal.incometax.gov.in/iec/servicesapi/auth/saveEntity"

            print("Adding bank account with branch details:", self.branch_details, self.user_profile, self.account_details)
            print("Using user profile details:", self.user_profile)
            print("Using account details:", self.account_details)
            payload={"bankAcctNum":self.account_details.get("accountNo"),
                    "accountType":"1" if self.account_details.get("accountType","savings").lower()=="savings" else "2",
                    "accountHolderType":"P" if self.account_details.get("accountHolderType","individual").lower()=="individual" else "C",
                    "refundFlag":"Y",
                    "ifscCd":self.account_details.get("ifsc"),
                    "bankName":self.branch_details.get("bank_name"),
                    "bankBrnchTxt":self.branch_details.get("branch_txt"),
                    "mobileNo":self.user_profile.get("priMobileNum"),
                   "emailId":self.user_profile.get("priEmailId"),
                    "entityNum":self.credentials.pan,  # fixed
                    "entityType":"P",
                    "sourceFlag":"USR",
                    "serviceName":"myBankAccountService"}
            print("add_bank_account payload:", payload)
            response = self._safe_post(url, json_data=payload, sn="myBankAccountService")
            # print(response.json())
            data, json_err = self._safe_parse_json(response)
            print(f"Response on Bank account add is {data}")
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to add bank account details: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

         
            messages = data.get("messages", [])
            success_flag = bool(data.get("successFlag")) or any(m.get("code") == "EF00000" for m in messages)

            # Store transaction id for future use
            self.bank_account_transaction_id = data.get("transactionNo")
            self.payment_crn=data.get("crn")

            return {
                "success": success_flag,
                "data": data,
                "transaction_id": self.bank_account_transaction_id,
                "message": "Bank account added successfully" if success_flag else "Bank account add request completed",
                "messages": messages,
                "status_code": response.status_code,
            }

        except Exception as e:
            # raise e
            return{
                "success": False,
                "error": str(e),
                "message": f"Failed to add bank account details: {str(e)}",
            }
        
    # def get_bank_add_otp(self):
    #     try:
    #         url="https://eportal.incometax.gov.in/iec/servicesapi/auth/generateBankAccountOtp"
    #         payload={"transactionNo":self.bank_account_transaction_id}
    #         response = self._safe_post(url, json_data=payload, sn="generateBankAccountOtp")
    #         data, json_err = self._safe_parse_json(response)
    #         if json_err:
    #             return {
    #                 "success": False,
    #                 "error": json_err,
    #                 "message": f"Failed to generate OTP for bank account addition: {json_err}",
    #                 "status_code": getattr(response, "status_code", None),
    #             }

    #         messages = data.get("messages", [])
    #         success_flag = bool(data.get("successFlag")) or any(m.get("code") == "EF00000" for m in messages)

    #         return {
    #             "success": success_flag,
    #             "data": data,
    #             "message": "OTP generated successfully for bank account addition" if success_flag else "OTP generation request completed",
    #             "messages": messages,
    #             "status_code": response.status_code,
    #         }

    #     except Exception as e:
    #         return{
    #             "success": False,
    #             "error": str(e),
    #             "message": f"Failed to generate OTP for bank account addition: {str(e)}",
    #         }

        
            

    def pay_payment(self,pan,payment_details,year="2026-27",old_regime=True,):

        try:
            resp=self.add_tax_applicable_details(pan,year=year,old_regime=old_regime)
            # print("step-0",resp)
            if not resp["success"]:
                return resp
            resp2=self.add_payment_details(payment_detils=payment_details)
            # print("step-1",resp2)
            if not resp2["success"]:
                return resp2
            resp3=self.add_bank_details(payment_details=payment_details)
            # print("step-2",resp3) 
            if not resp3["success"]:
                return resp3
            resp4=self.create_advance_payment_challan(payment_details=payment_details)  
            # print("step-3",resp4)
            if not resp4["success"]:
                return resp4
            resp5=self.advance_payment_bank_url()
            return resp5
        

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to process advance payment: {str(e)}",
            }
    def prevalidate_bank(self,account_details):
        try:
            self.account_details=account_details
            resp=self.get_user_profile()
            # print("step-1",resp)
            if not resp["success"]:
                return resp
            # print(account_details)
            resp2=self.get_branch_details(ifsc=account_details.get("ifsc"))
            # print("step-2",resp2)
            if not resp2["success"]:
                return resp2
            resp3=self.add_bank_account(bank_details=account_details)
            # print("step-3",resp3)
            if not resp3["success"]:
                return resp3
            resp4=self.check_aadhaar_linked(pan=self.credentials.pan)
            # print("step-4",resp4)
            if not resp4["success"]:
                return resp4
            resp5=self.send_otp_aadhaar(pan=self.credentials.pan)
            # print("step-5",resp5)
            return resp5
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to prevalidate bank account: {str(e)}",
            }
        
    def prevalidate_save(self) -> Dict[str, Any]:
        """
        Submit bank account prevalidation request using transactionNo + OTP.
        """
        try:
            url = "https://eportal.incometax.gov.in/iec/servicesapi/auth/saveEntity"

            transaction_no = getattr(self, "bnk_transaction_no", None)
            if not transaction_no:
                return {
                    "success": False,
                    "error": "Missing bank account transaction id",
                    "message": "Call add_bank_account first to get transactionNo.",
                }

            payload = {
                "bankAcctNum": self.account_details.get("accountNo"),
                "accountType": "1" if self.account_details.get("accountType", "savings").lower() == "savings" else "2",
                "accountHolderType": "P" if self.account_details.get("accountHolderType", "individual").lower() == "individual" else "C",
                "refundFlag": "Y",
                "ifscCd": self.account_details.get("ifsc"),
                "bankName": self.branch_details.get("bank_name"),
                "bankBrnchTxt": self.branch_details.get("branch_txt"),
                "mobileNo": self.user_profile.get("priMobileNum"),
                "emailId": self.user_profile.get("priEmailId"),
                "entityNum": self.credentials.pan,
                "entityType": "P",
                "sourceFlag": "USR",
                "serviceName": "myBankAccountService",
                "transactionNo": transaction_no,
                "otp": "EVERIFY",
            }

            response = self._safe_post(url, json_data=payload, sn="myBankAccountService")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to submit bank prevalidation: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            success_codes = {"EF40034", "EF00000"}
            has_success = any(m.get("code") in success_codes for m in messages) and response.status_code == 200

            # persist ids for future tracking
            self.bank_account_transaction_id = data.get("transactionNo") or transaction_no
            self.bank_prevalidation_unique_req_id = data.get("uniqueReqId")
            self.bank_prevalidation_status = data.get("status")

            return {
                "success": has_success,
                "data": data,
                "transaction_id": self.bank_account_transaction_id,
                "unique_req_id": self.bank_prevalidation_unique_req_id,
                "status": self.bank_prevalidation_status,
                "message": "Bank prevalidation submitted successfully" if has_success else "Bank prevalidation request completed",
                "messages": messages,
                "status_code": response.status_code,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to save bank prevalidation: {str(e)}",
            }

    def check_prevalidate_bank_otp(self, otp: str) -> Dict[str, Any]:
        try:
            url = "https://eportal.incometax.gov.in/iec/verificationservices/auth/validateOTP"

            ack_num = getattr(self, "bank_account_ack_num", None) or getattr(self, "bank_account_transaction_id", None)
            aadhaar_txn_id = getattr(self, "adhar_transaction_id", None)
            assessmnt_yr = str(getattr(self, "prevalidate_assessment_year", datetime.utcnow().year))

            if not ack_num:
                return {
                    "success": False,
                    "error": "Missing ackNum",
                    "message": "Call add_bank_account/prevalidate_save first.",
                }
            if not aadhaar_txn_id:
                return {
                    "success": False,
                    "error": "Missing aadhaarTxnId",
                    "message": "Call send_otp_aadhaar first.",
                }

            payload = {
                "serviceName": "verifyOtpUsingAadhar",
                "verifPan": self.credentials.pan,
                "otp": otp,
                "assessmntYr": assessmnt_yr,
                "ackNum": str(ack_num),
                "moduleCode": "NON-ITR",
                "otpGenerationSource": "EFL",
                "selectionFlag": "N",
                "formCd": "",
                "aadhaarTxnId": aadhaar_txn_id,
                "preLoginFlag": "N",
                "header": {"formName": "FO-091-EVERI"},
                "loggedInUserId": self.credentials.pan,
            }

            print("Validating prevalidation OTP with payload:", payload)

            response = self._safe_post(url, json_data=payload, sn="verifyOtpUsingAadhar")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to validate prevalidation OTP: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            status = str(data.get("status", "")).upper()
            has_success = (
                status == "SUCCESS"
                or any(str(m.get("code", "")).upper() == "OTP VALIDATED" for m in messages)
            ) and response.status_code == 200

            self.ackNo = data.get("ackNum")
            self.bnk_transaction_no=data.get("transactionNo")
            self.bank_prevalidation_aadhaar_txn_id = data.get("aadhaarTxnId") or aadhaar_txn_id

            return {
                "success": has_success,
                "data": data,
                "transaction_id": data.get("transactionNo"),
                "ack_num": data.get("ackNum"),
                "aadhaar_txn_id": self.bank_prevalidation_aadhaar_txn_id,
                "message": "Prevalidation OTP validated successfully" if has_success else "Prevalidation OTP validation completed",
                "messages": messages,
                "errors": data.get("errors", []),
                "status_code": response.status_code,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to validate prevalidation OTP: {str(e)}",
            }

    def pre_validate_continue(self,otp):
        try:
            resp=self.check_prevalidate_bank_otp(otp=otp)   
            if not resp["success"]:
                return resp
            resp1=self.prevalidate_save()

            return resp1
        
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to continue prevalidation: {str(e)}",
            }

    

    def validate_pan_adhaar_number(self,aadhar_no):

        """
        ----------------------------------------------------------------------------------------
        step-validate the pan adhaar linkage 

        ----------------------------------------------------------------------------------------
        
        
        """

        try:
            url="https://eportal.incometax.gov.in/iec/servicesapi/getEntity"

            payload={"aadhaarNumber": aadhar_no, "serviceName": "linkAadhaarVerhoeffAlgoService"}

            resp=self._safe_post(url, json_data=payload, sn="linkAadhaarVerhoeffAlgoService")

            data, json_err = self._safe_parse_json(resp)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to validate Aadhaar number: {json_err}",
                    "status_code": getattr(resp, "status_code", None),
                }

            messages=data.get("messages", [])
            errors = data.get("errors", [])

            success_codes = {
                MessageCode.SUCCESS.value,
                MessageCode.VALID_AADHAAR_NUMBER.value,
            }

            has_success = any(m.get("code") in success_codes for m in messages) and not errors

            error_messages = []
            if not has_success:
                for m in messages:
                    if m.get("code") not in success_codes:
                        desc = m.get("desc") or m.get("description") or m.get("message", "")
                        if desc:
                            error_messages.append(desc)
                for e in errors:
                    if isinstance(e, dict):
                        desc = e.get("desc") or e.get("description") or e.get("message", "")
                        if desc:
                            error_messages.append(desc)
                    elif isinstance(e, str):
                        error_messages.append(e)

            return {
                "success": has_success,
                "data": data,
                "message": "Aadhaar number is valid" if has_success else "Aadhaar validation failed",
                "error": "; ".join(error_messages) if error_messages else None,
                "messages": messages,
                "errors": errors,
                "status_code": resp.status_code,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to validate Aadhaar number: {str(e)}",
            }

    def get_validate_adhaar_pan_linkage(self, aadhar_no, name, pan, mobile_num, area_cd="91", country_cd="IN"):
        """
        ----------------------------------------------------------------------------------------
        step-validate the pan adhaar linkage
        ----------------------------------------------------------------------------------------
        """
        try:
            url = "https://eportal.incometax.gov.in/iec/servicesapi/getEntity"

            payload = {
            "mobileNum": mobile_num,
            "areaCd": area_cd,      # use param
            "countryCd": country_cd,# use param
            "preLoginFlag": "Y",
            "aadhaarNumber": aadhar_no,
            "nameAsOnAadhaarCard": name,
            "pan": pan,
            "aadhaarYobFlag": "Y",
            "serviceName": "linkAadhaarValidationService",
            "createdBy": "linkAadhaarValidationService",
            "updatedBy": "linkAadhaarValidationService",
            "createdByUser": pan,
            "updatedByUser": pan,
            }
            resp = self._safe_post(url, json_data=payload, sn="linkAadhaarValidationService")

            data, json_err = self._safe_parse_json(resp)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to validate Aadhaar number: {json_err}",
                    "status_code": getattr(resp, "status_code", None),
                }

            messages=data.get("messages", [])
            errors = data.get("errors", [])

            success_codes = {
                MessageCode.SUCCESS.value,
                MessageCode.VALID_AADHAAR_NUMBER.value,
                MessageCode.AADHAAR_LINK_VALIDATION_SUCCESS.value,  # EF40126
            }
            has_success = any(m.get("code") in success_codes for m in messages) and not errors

            error_messages = []
            if not has_success:
                for m in messages:
                    if m.get("code") not in success_codes:
                        desc = m.get("desc") or m.get("description") or m.get("message", "")
                        if desc:
                            error_messages.append(desc)
                for e in errors:
                    if isinstance(e, dict):
                        desc = e.get("desc") or e.get("description") or e.get("message", "")
                        if desc:
                            error_messages.append(desc)
                    elif isinstance(e, str):
                        error_messages.append(e)

            return {
                "success": has_success,
                "data": data,
                "message": "Aadhaar-PAN linkage is valid" if has_success else "Aadhaar-PAN linkage validation failed",
                "error": "; ".join(error_messages) if error_messages else None,
                "messages": messages,
                "errors": errors,
                "status_code": resp.status_code,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to validate Aadhaar-PAN linkage: {str(e)}",
            }

  
    def send_adhar_pan_link_otp(self, pan: str, mobile: str, area_cd: str = "91", country_cd: str = "IN") -> Dict[str, Any]:
        """Send OTP for Aadhaar-PAN linkage.
        
        Args:
            pan: PAN number (userId)
            mobile: Mobile number
            area_cd: Area/country code (default: "91")
            country_cd: Country code (default: "IN")
            
        Returns:
            Dict with:
                - success: bool
                - data: dict (response data)
                - error: str (error message if failed)
                - message: str (description)
        """
        try:
            url = "https://eportal.incometax.gov.in/iec/servicesapi/saveEntity"

            # Store context for later use in save_adhar_pan_link
            self.adhar_pan_link_pan = pan
            self.adhar_pan_link_mobile = mobile
            self.adhar_pan_link_area_cd = area_cd
            self.adhar_pan_link_country_cd = country_cd

            payload = {
                "userId": pan,
                "serviceName": "linkAadharOtpService",
                "mobileNum": mobile,
                "areaCd": area_cd,
                "countryCd": country_cd,
            }

            response = self._safe_post(url, json_data=payload, sn="linkAadharOtpService")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"send_adhar_pan_link_otp JSON parse error: {json_err}")
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to send Aadhaar-PAN link OTP: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            errors = data.get("errors", [])
            result = data.get("result", "")

            has_success = (
                "otp generated" in result.lower()
                or response.status_code == 200
            ) and not errors

            # Store userId from response for future use
            if has_success:
                self.panuserid = data.get("userId")

            error_messages = []
            if not has_success:
                for msg in messages:
                    desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                    if desc:
                        error_messages.append(desc)
                for err in errors:
                    if isinstance(err, dict):
                        desc = err.get("desc") or err.get("description") or err.get("message", "")
                        if desc:
                            error_messages.append(desc)
                    elif isinstance(err, str):
                        error_messages.append(err)

            error_text = "; ".join(error_messages) if error_messages else None

            return {
                "success": has_success,
                "data": data,
                "userId": getattr(self, "panuserid", None),
                "message": result if has_success else ("Failed to send Aadhaar-PAN link OTP" if error_text else "OTP request completed"),
                "error": error_text,
                "messages": messages,
                "errors": errors,
                "status_code": response.status_code,
            }

        except Exception as e:
            logger.error(f"send_adhar_pan_link_otp failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to send Aadhaar-PAN link OTP: {str(e)}",
            }


    def vaildate_adhar_pan_link_otp(self, otp: str) -> Dict[str, Any]:
        """Validate OTP for Aadhaar-PAN linkage.
        
        Args:
            otp: The OTP received on the registered mobile number

        Returns:
            Dict with:
                - success: bool
                - data: dict (response data)
                - error: str (error message if failed)
                - message: str (description)    
        """
        try:
            url = "https://eportal.incometax.gov.in/iec/servicesapi/validateOTP"

            payload = {
                "userId": getattr(self, "panuserid", None),
                "serviceName": "linkAadharOtpService",
                "mobileNum": getattr(self, "adhar_pan_link_mobile", None),
                "areaCd": getattr(self, "adhar_pan_link_area_cd", "91"),
                "countryCd": getattr(self, "adhar_pan_link_country_cd", "IN"),
                "otp": otp,
            }

            if not payload["userId"]:
                return {
                    "success": False,
                    "error": "Missing userId. Call send_adhar_pan_link_otp first.",
                    "message": "OTP validation failed: missing context.",
                }

            response = self._safe_post(url, json_data=payload, sn="linkAadharOtpService")
            data, json_err = self._safe_parse_json(response)
            if json_err:
                logger.error(f"vaildate_adhar_pan_link_otp JSON parse error: {json_err}")
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to validate Aadhaar-PAN link OTP: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                }

            messages = data.get("messages", [])
            errors = data.get("errors", [])
            result = data.get("result", "")
            error_remarks = data.get("errorRemarks", "")

            # Success: result should NOT be "OTP INCORRECT" and no error messages
            has_success = (
                "otp incorrect" not in result.lower()
                and not any(msg.get("code") == "EF40088" for msg in messages)
                and not errors
                and response.status_code == 200
            )

            error_messages = []
            if not has_success:
                # Use errorRemarks if present
                if error_remarks:
                    error_messages.append(error_remarks)
                else:
                    for msg in messages:
                        desc = msg.get("desc") or msg.get("description") or msg.get("message", "")
                        if desc:
                            error_messages.append(desc)
                    for err in errors:
                        if isinstance(err, dict):
                            desc = err.get("desc") or err.get("description") or err.get("message", "")
                            if desc:
                                error_messages.append(desc)
                        elif isinstance(err, str):
                            error_messages.append(err)

            error_text = "; ".join(error_messages) if error_messages else None

            return {
                "success": has_success,
                "data": data,
                "result": result,
                "message": result if has_success else (error_text or "OTP validation failed"),
                "error": error_text,
                "messages": messages,
                "errors": errors,
                "status_code": response.status_code,
            }

        except Exception as e:
            logger.error(f"vaildate_adhar_pan_link_otp failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to validate Aadhaar-PAN link OTP: {str(e)}",
            }

    def generate_pan_link_otp(self,aadhar_no,pan,mobile,name,area_cd="91",country_cd="IN"):

        resp=self.validate_pan_adhaar_number(aadhar_no=aadhar_no)

        if not resp["success"]:
            return resp

        resp2=self.get_validate_adhaar_pan_linkage(aadhar_no=aadhar_no,name=name,pan=pan,mobile_num=mobile,area_cd=area_cd,country_cd=country_cd) 

        if not resp2["success"]:
            return resp2    


        resp3=self.send_adhar_pan_link_otp(pan=pan,mobile=mobile,area_cd=area_cd,country_cd=country_cd)


        return resp3

    
    def get_ais_redirection(self, pan):
        """Handle AIS redirection for a given PAN."""
        try:
            url = self.config.base_url + self.config.ais_redirection_endpoint
            payload = {
                "header": {"formName": "FO-042-CPRS"},
                "serviceName": "aisIntegrationService",
                "loggedInUserId": pan
            }

            response = self._safe_post(url, json_data=payload, sn="aisIntegrationService")
            data, json_err = self._safe_parse_json(response)

            if json_err:
                return {
                    'success': False,
                    'error': json_err,
                    'message': f'Failed to handle AIS redirection: {json_err}',
                    'status_code': getattr(response, 'status_code', None)
                }

            param1 = data.get("param1", "")
            param2 = data.get("param2", "")
            param3 = data.get("param3", "")
            unmflg = str(data.get("unmFlg", data.get("unmflg", "0")))

            self.redirection_parms = {
                "param1": param1,
                "param2": param2,
                "param3": param3,
                "unmFlg": unmflg,
            }

            access_url = self._build_ais_access_url(
                param1=param1,
                param2=param2,
                param3=param3,
                unmflg=unmflg,
            )

            return {
                'success': response.status_code == 200,
                'data': data,
                'params': self.redirection_parms,
                'access_url': access_url,
                'message': 'AIS redirection successful.' if response.status_code == 200 else 'Failed to handle AIS redirection.',
                'status_code': response.status_code
            }
        except Exception as e:
            logger.error(f"get_ais_redirection failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to handle AIS redirection: {str(e)}'
            }

    def create_ais_url(self, pan):
        """Create AIS access URL for a given PAN."""
        try:
            redirection = self.get_ais_redirection(pan)
            if not redirection.get("success"):
                return redirection

            return {
                'success': True,
                'data': {
                    'url': redirection.get('access_url'),
                    'params': redirection.get('params', {})
                },
                'message': 'AIS URL created successfully.'
            }
        except Exception as e:
            logger.error(f"create_ais_url failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to create AIS URL: {str(e)}'
            }



    # ...existing code up to and including get_ais_redirection / create_ais_url ...

    def _build_ais_access_url(
        self,
        param1: str,
        param2: str = "",
        param3: str = "",
        unmflg: str = "0",
    ) -> str:
        query = urlencode({
            "param1": param1 or "",
            "param2": param2 or "",
            "param3": param3 or "",
            "unmflg": str(unmflg or "0"),
        })
        return f"https://ais.insight.gov.in/portal/access?{query}"

    def _build_cookie_header_from_jars(self, *sessions) -> str:
        """Build a merged Cookie header string from one or more session cookie jars."""
        cookie_map: Dict[str, str] = {}
        for session in sessions:
            if not session:
                continue
            for cookie in session.cookies:
                if cookie.name:
                    cookie_map[cookie.name] = cookie.value
        return "; ".join(f"{name}={value}" for name, value in cookie_map.items())

    def _copy_cookies_to_ais_session(self) -> None:
        """
        Copy ALL ePortal cookies into AIS session jar.
        Seeds them for both original domain AND ais.insight.gov.in.
        Clears AIS jar first to avoid stale cookies.
        """
        try:
            self.ais_session.cookies.clear()

            for cookie in self.session.cookies:
                # Keep original domain cookie
                try:
                    self.ais_session.cookies.set(
                        cookie.name,
                        cookie.value,
                        domain=cookie.domain,
                        path=cookie.path or "/",
                        secure=cookie.secure,
                        expires=cookie.expires,
                    )
                except Exception:
                    self.ais_session.cookies.set(cookie.name, cookie.value)

                # Also seed for AIS domain
                try:
                    self.ais_session.cookies.set(
                        cookie.name,
                        cookie.value,
                        domain="ais.insight.gov.in",
                        path="/",
                        secure=True,
                    )
                except Exception:
                    pass

            cookie_count = len(list(self.ais_session.cookies))
            logger.info(f"Copied {cookie_count} cookies to AIS session jar")
            print(f"[DEBUG] Copied {cookie_count} cookies to AIS session")
            print(f"[DEBUG] AIS jar cookies: { {c.name: c.value[:20]+'...' for c in self.ais_session.cookies} }")

        except Exception as e:
            logger.error(f"Failed to copy cookies to AIS session: {e}", exc_info=True)

    # ...existing code...

    def _update_ais_cookies_from_response(self, response) -> None:
        """Parse Set-Cookie from AIS response and force-store into ais_session jar + flat dict."""
        if not response or not hasattr(response, "headers"):
            return

        if not hasattr(self, "_ais_cookie_dict"):
            self._ais_cookie_dict = {}

        # Absorb response.cookies — works for both requests.Response and curl_cffi.Response
        resp_cookies = getattr(response, "cookies", None)
        if resp_cookies:
            try:
                # curl_cffi Cookies object supports .items() or iteration
                if hasattr(resp_cookies, "items"):
                    for name, value in resp_cookies.items():
                        self._ais_cookie_dict[name] = value
                        try:
                            self.ais_session.cookies.set(name, value, domain="ais.insight.gov.in", path="/", secure=True)
                        except Exception:
                            try:
                                self.ais_session.cookies.set(name, value)
                            except Exception:
                                pass
                elif hasattr(resp_cookies, "__iter__"):
                    for cookie in resp_cookies:
                        try:
                            cname = getattr(cookie, "name", None)
                            cvalue = getattr(cookie, "value", None)
                            if cname and cvalue:
                                self._ais_cookie_dict[cname] = cvalue
                                self.ais_session.cookies.set(cname, cvalue, domain="ais.insight.gov.in", path="/", secure=True)
                        except Exception:
                            pass
            except Exception:
                pass

        # Manual Set-Cookie parsing for edge cases
        set_cookie_values = []
        try:
            # Standard requests raw headers
            raw = getattr(response, "raw", None)
            raw_headers = getattr(raw, "headers", None) if raw is not None else None
            if raw_headers is not None and hasattr(raw_headers, "get_all"):
                set_cookie_values = raw_headers.get_all("Set-Cookie") or []
        except Exception:
            pass

        if not set_cookie_values:
            header_val = response.headers.get("Set-Cookie")
            if header_val:
                set_cookie_values = re.split(r', (?=[^\s=]+=)', header_val)

        for sc in set_cookie_values:
            try:
                cookie = SimpleCookie()
                cookie.load(sc)
                for name, morsel in cookie.items():
                    value = morsel.value
                    domain = morsel["domain"] if morsel["domain"] else "ais.insight.gov.in"
                    path = morsel["path"] if morsel["path"] else "/"
                    secure = bool(morsel["secure"])
                    self._ais_cookie_dict[name] = value
                    try:
                        self.ais_session.cookies.set(name, value, domain=domain, path=path, secure=secure)
                    except Exception:
                        try:
                            self.ais_session.cookies.set(name, value)
                        except Exception:
                            pass
            except Exception:
                continue


    # ...existing code...

    def _safe_ais_request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Any:
        """
        Perform AIS request with retries.
        Uses curl_cffi with Chrome TLS impersonation to bypass CloudFront WAF 425.
        Falls back to requests.request() only if curl_cffi is not installed.
        """
        max_retries = self.config.max_retries
        delay = self.config.initial_delay
        last_exception = None

        if not hasattr(self, "_ais_cookie_dict"):
            self._ais_cookie_dict = {}

        for attempt in range(1, max_retries + 1):
            try:
                req_headers: Dict[str, str] = {
                    "User-Agent": self.config.user_agent,
                    "Accept-Encoding": self.config.accept_encoding,
                    "Accept-Language": self.config.accept_language,
                    "Connection": "keep-alive",
                }

                # Caller headers override defaults (including Cookie)
                if headers:
                    req_headers.update(headers)

                allow_redirects = kwargs.pop("allow_redirects", True)

                # print(f"[DEBUG] AIS {method.upper()} {url[:100]}... (attempt {attempt})")
                # print(f"[DEBUG] AIS engine: {'curl_cffi' if HAS_CURL_CFFI else 'requests (WARNING: will likely get 425)'}")

                if HAS_CURL_CFFI:
                    # ── curl_cffi path: Chrome TLS fingerprint ──
                    # Extract Cookie header and merge into a flat dict
                    # curl_cffi sends cookies= as Cookie header automatically
                    explicit_cookie = req_headers.pop("Cookie", None)

                    # Build merged cookie dict from both sessions + flat dict
                    merged_cookies: Dict[str, str] = {}
                    for c in self.session.cookies:
                        if c.name:
                            merged_cookies[c.name] = c.value
                    for c in self.ais_session.cookies:
                        if c.name:
                            merged_cookies[c.name] = c.value
                    merged_cookies.update(self._ais_cookie_dict)

                    # Parse explicit Cookie header into dict too
                    if explicit_cookie:
                        for pair in explicit_cookie.split(";"):
                            pair = pair.strip()
                            if "=" in pair:
                                k, v = pair.split("=", 1)
                                merged_cookies[k.strip()] = v.strip()

                    print(f"[DEBUG] AIS curl_cffi cookie count: {len(merged_cookies)}")
                    print(f"[DEBUG] AIS curl_cffi cookie names: {list(merged_cookies.keys())}")

                    cffi_kwargs: Dict[str, Any] = {
                        "headers": req_headers,
                        "cookies": merged_cookies,
                        "timeout": self.config.timeout,
                        "allow_redirects": allow_redirects,
                        "impersonate": "chrome",
                    }

                    if json_data is not None:
                        cffi_kwargs["json"] = json_data

                    # Pass through extra kwargs (data=, params=, etc.)
                    for k, v in kwargs.items():
                        if k not in cffi_kwargs:
                            cffi_kwargs[k] = v

                    response = cffi_requests.request(
                        method.upper(),
                        url,
                        **cffi_kwargs,
                    )

                    # Absorb Set-Cookie from response + redirect chain
                    self._update_ais_cookies_from_response(response)
                    if hasattr(response, "history") and response.history:
                        for h in response.history:
                            self._update_ais_cookies_from_response(h)

                    print(f"[DEBUG] AIS response: status={response.status_code}")
                    if hasattr(response, "url"):
                        print(f"[DEBUG] AIS final url: {str(response.url)[:120]}")

                    return response

                else:
                    # ── Fallback: standard requests (will likely get 425 from CloudFront) ──
                    cookie_header = req_headers.pop("Cookie", None)

                    # Build merged cookie dict
                    merged_cookies = {}
                    for c in self.session.cookies:
                        if c.name:
                            merged_cookies[c.name] = c.value
                    for c in self.ais_session.cookies:
                        if c.name:
                            merged_cookies[c.name] = c.value
                    merged_cookies.update(self._ais_cookie_dict)
                    if cookie_header:
                        for pair in cookie_header.split(";"):
                            pair = pair.strip()
                            if "=" in pair:
                                k, v = pair.split("=", 1)
                                merged_cookies[k.strip()] = v.strip()

                    print(f"[DEBUG] AIS requests cookie count: {len(merged_cookies)}")

                    request_kwargs: Dict[str, Any] = {
                        "timeout": self.config.timeout,
                        "allow_redirects": allow_redirects,
                        **kwargs,
                    }
                    if json_data is not None:
                        request_kwargs["json"] = json_data

                    response = requests.request(
                        method.upper(),
                        url,
                        headers=req_headers,
                        cookies=merged_cookies,
                        **request_kwargs,
                    )

                    self._update_ais_cookies_from_response(response)
                    if hasattr(response, "history"):
                        for h in response.history:
                            self._update_ais_cookies_from_response(h)

                    print(f"[DEBUG] AIS response: status={response.status_code}, url={response.url[:100]}")
                    return response

            except Exception as e:
                last_exception = e
                logger.warning(f"AIS request error on attempt {attempt}/{max_retries}: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= self.config.backoff_factor
                else:
                    raise NetworkError(f"AIS request failed for {url}") from e

        raise NetworkError(f"AIS request failed after {max_retries} attempts") from last_exception


    def init_ais_using_access_url(self, access_url: str) -> Dict[str, Any]:
        """
        STEP 3b: SSO Redirect to AIS Portal

        CRITICAL flow:
        1. Copy ALL ePortal cookies into AIS jar
        2. Build merged Cookie header from BOTH ePortal + AIS jars
        3. GET the access URL with Content-Type/Accept: application/json
        4. Follow ALL redirects
        5. Capture new cookies from Set-Cookie headers
        """
        try:
            # 1) Copy all ePortal cookies into AIS jar
            self._copy_cookies_to_ais_session()

            # 2) Build merged Cookie header from BOTH jars
            merged_cookie_header = self._build_cookie_header_from_jars(self.session, self.ais_session)

            print(f"\n{'='*80}")
            print(f"STEP 3b: SSO Redirect to AIS Portal")
            print(f"{'='*80}")
            print(f"[DEBUG] Access URL: {access_url[:120]}...")
            print(f"[DEBUG] Merged cookie header length: {len(merged_cookie_header)}")
            print(f"[DEBUG] ePortal cookies: {[c.name for c in self.session.cookies]}")
            print(f"[DEBUG] AIS cookies before: {[c.name for c in self.ais_session.cookies]}")

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Referer": "https://eportal.incometax.gov.in/iec/foservices/",
                "Cookie": merged_cookie_header,
            }

            # 3) Follow all redirects
            response = self._safe_ais_request(
                "GET",
                access_url,
                headers=headers,
                allow_redirects=True,
            )

            print(f"[DEBUG] Final URL after redirects: {response.url[:120]}...")

            print("DEBUG Response Headers after AIS redirect:")
            for k, v in response.headers.items():
                print(f"{k}: {v}")

            self.ais_access_url = access_url
            self.ais_final_url = response.url

            redirect_chain = [r.url for r in response.history] + [response.url]
            ais_cookies = {c.name: c.value for c in self.ais_session.cookies}
            ais_cookie_count = sum(
                1 for c in self.ais_session.cookies
                if "insight.gov.in" in (c.domain or "")
            )

            print(f"[DEBUG] 3b status: {response.status_code}")
            print(f"[DEBUG] 3b final URL: {response.url[:120]}")
            print(f"[DEBUG] 3b redirect chain ({len(redirect_chain)}): {[u[:80] for u in redirect_chain]}")
            print(f"[DEBUG] 3b AIS cookie count: {ais_cookie_count}")
            print(f"[DEBUG] 3b all cookies after: {list(ais_cookies.keys())}")
            print(f"[DEBUG] 3b Set-Cookie header: {response.headers.get('Set-Cookie', '(none)')[:200]}")
            print(f"[DEBUG] 3b response body preview: {(response.text or '')[:300]}")

            success = response.status_code == 200 and ais_cookie_count > 0

            return {
                "success": success,
                "data": {
                    "initial_url": access_url,
                    "final_url": response.url,
                    "redirect_chain": redirect_chain,
                    "ais_cookie_count": ais_cookie_count,
                    "cookies": ais_cookies,
                    "response_headers": dict(response.headers),
                },
                "message": "AIS SSO redirect completed successfully." if success else "AIS SSO redirect failed.",
                "status_code": response.status_code,
            }

        except Exception as e:
            logger.error(f"init_ais_using_access_url failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to initialize AIS SSO: {str(e)}",
            }

    def get_ais_access_token(self) -> Dict[str, Any]:
        """
        STEP 3c: Get AIS access token

        POST with Content-Type: application/json, Accept: application/json
        Cookie: [BOTH eportal + AIS cookies merged]
        Referer: https://ais.insight.gov.in/complianceportal/ais/summary
        """
        try:
            url = "https://ais.insight.gov.in/v1/portal-auth/auth/portal/eFiling/accessToken"

            # Build merged Cookie from BOTH jars — ePortal + AIS
            merged_cookie_header = self._build_cookie_header_from_jars(self.session, self.ais_session)

            print(f"\n{'='*80}")
            print(f"STEP 3c: Get AIS Access Token")
            print(f"{'='*80}")
            print(f"[DEBUG] Token URL: {url}")
            print(f"[DEBUG] Merged cookie header length: {len(merged_cookie_header)}")
            print(f"[DEBUG] AIS cookie names: {[c.name for c in self.ais_session.cookies]}")
            print(f"[DEBUG] ePortal cookie names: {[c.name for c in self.session.cookies]}")
            print(f"[DEBUG] Merged cookie header: {merged_cookie_header}...")

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Referer": "https://ais.insight.gov.in/complianceportal/ais/summary",
                "Origin": "https://ais.insight.gov.in",
                "Cookie": merged_cookie_header,
            }

            response = self._safe_ais_request(
                "POST",
                url,
                json_data={},
                headers=headers,
                allow_redirects=True,
            )

            print(response)


            print(f"[DEBUG] 3c status: {response.status_code}")
            print(f"[DEBUG] 3c response headers: { {k: v[:100] for k, v in response.headers.items()} }")
            print(f"[DEBUG] 3c response body: {(response.text or '')[:500]}")
            print(f"[DEBUG] 3c cookies after: {[c.name for c in self.ais_session.cookies]}")

            data, json_err = self._safe_parse_json(response)
            if json_err:
                return {
                    "success": False,
                    "error": json_err,
                    "message": f"Failed to get AIS access token: {json_err}",
                    "status_code": getattr(response, "status_code", None),
                    "response_text": (response.text[:500] if getattr(response, "text", None) else ""),
                    "cookies": dict((c.name, c.value) for c in self.ais_session.cookies),
                }

            access_token = data.get("accessToken") or data.get("access_token") or data.get("token")
            if access_token:
                self.ais_access_token = access_token

            return {
                "success": bool(access_token) and response.status_code == 200,
                "data": data,
                "accessToken": access_token,
                "headers": {"Authorization": f"Bearer {access_token}"} if access_token else {},
                "cookies": dict((c.name, c.value) for c in self.ais_session.cookies),
                "message": "AIS access token fetched successfully." if access_token else "AIS access token not found.",
                "status_code": response.status_code,
            }

        except Exception as e:
            logger.error(f"get_ais_access_token failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to get AIS access token: {str(e)}",
            }

    def initialize_ais_portal(self, pan: str) -> Dict[str, Any]:
        """
        Full AIS init flow:
        1. create_ais_url (gets params from ePortal)
        2. SSO redirect to AIS portal (copies cookies, follows redirects)
        3. fetch AIS access token (uses merged cookies from both portals)
        """
        try:
            url_result = self.create_ais_url(pan)
            
            if not url_result.get("success"):
                # print(url_result)
                return url_result

            access_url = (url_result.get("data") or {}).get("url")
            if not access_url:
                return {
                    "success": False,
                    "error": "AIS access URL missing",
                    "message": "Failed to initialize AIS portal.",
                }

            sso_result = self.init_ais_using_access_url(access_url)
            # print(sso_result)
            if not sso_result.get("success"):
                return sso_result

            token_result = self.get_ais_access_token()
            if not token_result.get("success"):
                return token_result

        #     return {
        #         "success": True,
        #         "data": {
        #             "access_url": access_url,
        #             "final_url": (sso_result.get("data") or {}).get("final_url"),
        #             "redirect_chain": (sso_result.get("data") or {}).get("redirect_chain", []),
        #             "accessToken": token_result.get("accessToken"),
        #             "cookies": token_result.get("cookies", {}),
        #         },
        #         "message": "AIS portal initialized successfully.",
        #     }

        except Exception as e:
            logger.error(f"initialize_ais_portal failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to initialize AIS portal: {str(e)}",
            }


# Maintain backward compatibility with existing code
EPortalLoginStealth = EPortalClient


# # ============================================================================
# # Main Entry Point
# # # ============================================================================

# if __name__ == "__main__":
#     user_credentials = {
#         "PAN": "ABCDE1234F",
#         "PASSWORD": "Sunny@123#",
#     }

#     try:
#         client = EPortalClient(user_credentials)
#         auth_result = client.login()

#         if auth_result["success"]:
#             print("✓ Login successful!")
#             profile_result = client.get_user_profile()
#             if profile_result["success"]:
#                 ais_data = client.initialize_ais_portal(user_credentials["PAN"])
#                 print(ais_data)
#             else:
#                 print(f"✗ Failed to fetch user profile: {profile_result.get('error')}")

#     except Exception as e:
#         print(f"✗ Error: {e}")
#         logger.error("Application error", exc_info=True)
