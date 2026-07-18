import sys
import os
from pathlib import Path
import platform
import asyncio
import json
import re
import threading
import queue
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from time import monotonic

# ======================================================================
# Windows event loop policy MUST be configured before importing async libs
# ======================================================================
if platform.system() == "Windows":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception as e:
            print(f"WARNING: Could not set Windows event loop policy: {e}")

sys.path.append(str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, status, Header, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime, timedelta, timezone
import secrets
import hashlib
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

from registration_core import ePortalRegistrationApi
from playwrite_login_with_session_cookie import EPortalLoginStealth, proxy_slot_status

CLIENT_KEY = os.getenv("CLIENT_KEY")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"
ENABLE_METRICS = os.getenv("ENABLE_METRICS", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG" if DEBUG_MODE else "INFO").upper()
LOG_DIR = os.getenv("LOG_DIR", str(Path(__file__).resolve().parent / "logs")).strip()
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))
ALLOWED_ORIGINS = [value.strip() for value in os.getenv("ALLOWED_ORIGINS", "").split(",") if value.strip()]
ALLOWED_HOSTS = [value.strip() for value in os.getenv("ALLOWED_HOSTS", "*").split(",") if value.strip()]
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8002"))
FORWARDED_ALLOW_IPS = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "65536"))

REQUEST_ID = ContextVar("request_id", default="-")
SESSIONS_LOCK = threading.RLock()
RATE_LIMIT_LOCK = threading.Lock()

_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_MOBILE_RE = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")
_SECRET_RE = re.compile(
    r"(?i)((?:password|confirmpwd|cred|newcredential|aadhaar_otp|mobile_otp|"
    r"email_otp|otp|authorization|cookie|x-api-key|autkn)\s*['\"]?\s*[:=]\s*)"
    r"(['\"]?)([^,\s}\]]+)"
)


def _fingerprint(label: str, value: Any) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{label}#{digest}"


def _redact_text(value: Any) -> str:
    text = str(value)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}<redacted>", text)
    text = _PAN_RE.sub(lambda match: _fingerprint("pan", match.group(0).upper()), text)
    text = _EMAIL_RE.sub(lambda match: _fingerprint("email", match.group(0).lower()), text)
    return _MOBILE_RE.sub(lambda match: _fingerprint("mobile", match.group(0)), text)


def redact(value: Any, key: str = "") -> Any:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if any(part in normalized for part in ("password", "credential", "otp", "cookie", "token", "authorization", "apikey", "autkn", "sessionid", "secaccessmsg")):
        return "<redacted>"
    if normalized.startswith("dob") or normalized in {
        "pan", "email", "mobile", "address", "firstname", "middlename", "lastname",
        "gender", "resident", "pin", "permsg", "reqid", "transactionno",
    }:
        return _fingerprint(normalized, value)
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, key) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _session_ref(session_id: Optional[str]) -> str:
    return _fingerprint("session", session_id) if session_id else "-"


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(redact(value), ensure_ascii=True, default=str))


if not CLIENT_KEY:
    raise ValueError("ERROR: CLIENT_KEY environment variable is not set!")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
            "request_id": getattr(record, "request_id", REQUEST_ID.get()),
            "source": f"{record.filename}:{record.lineno}",
            "function": record.funcName,
            "process_id": record.process,
            "thread": record.threadName,
        }
        for name in ("event", "http_method", "http_path", "status_code", "duration_ms", "portal_step", "session_ref"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=True, default=str)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", REQUEST_ID.get())
        return True


def setup_logging():
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    formatter = JsonFormatter()
    context_filter = RequestContextFilter()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)

    handlers = [console_handler]
    file_error = None
    if LOG_DIR:
        try:
            log_dir = Path(LOG_DIR).expanduser().resolve()
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / "api_hybrid_registration.log",
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(context_filter)
            handlers.append(file_handler)

            error_handler = RotatingFileHandler(
                log_dir / "api_hybrid_registration_errors.log",
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            error_handler.addFilter(context_filter)
            handlers.append(error_handler)
        except OSError as exc:
            file_error = exc

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    for handler in handlers:
        root_logger.addHandler(handler)
    logging.captureWarnings(True)
    configured_logger = logging.getLogger(__name__)
    if file_error:
        configured_logger.warning(
            "File logging unavailable; continuing with stdout: %s",
            file_error,
            extra={"event": "log_file_unavailable"},
        )
    return configured_logger

logger = setup_logging()

try:
    current_policy = asyncio.get_event_loop_policy()
    logger.info(f"✓ Current Event Loop Policy: {type(current_policy).__name__}")
except Exception as e:
    logger.warning(f"Could not determine event loop policy: {e}")

API_KEYS = {}

try:
    API_KEYS = {
        hashlib.sha256(CLIENT_KEY.encode()).hexdigest(): "client",
    }
    logger.info("✓ API keys loaded successfully")
except Exception as e:
    logger.error(f"✗ Failed to initialize API keys: {e}")

registration_sessions: Dict[str, Dict[str, Any]] = {}
request_attempts: Dict[str, List[datetime]] = {}

MAX_REQUESTS_PER_IP = int(os.getenv("MAX_REQUESTS_PER_IP", "50"))
RATE_LIMIT_WINDOW = timedelta(minutes=5)
SESSION_TIMEOUT = timedelta(minutes=30)
BROWSER_SESSION_TIMEOUT = timedelta(seconds=int(os.getenv("BROWSER_SESSION_TIMEOUT_SECONDS", "600")))
SESSION_CLEANUP_INTERVAL_SECONDS = int(os.getenv("SESSION_CLEANUP_INTERVAL_SECONDS", "60"))
CLEANUP_STOP_EVENT = threading.Event()

class Metrics:
    def __init__(self):
        self.total_requests = 0
        self.total_authenticated = 0
        self.total_errors = 0
        self.successful_registrations = 0
        self.failed_registrations = 0
        self.aadhaar_registrations = 0
        self.non_aadhaar_registrations = 0
        self.start_time = datetime.now()

    def get_uptime_seconds(self) -> int:
        return int((datetime.now() - self.start_time).total_seconds())

    def to_dict(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "total_authenticated": self.total_authenticated,
            "total_errors": self.total_errors,
            "successful_registrations": self.successful_registrations,
            "failed_registrations": self.failed_registrations,
            "aadhaar_registrations": self.aadhaar_registrations,
            "non_aadhaar_registrations": self.non_aadhaar_registrations,
            "active_sessions": len(registration_sessions),
            "uptime_seconds": self.get_uptime_seconds(),
            "timestamp": datetime.now().isoformat()
        }

metrics = Metrics() if ENABLE_METRICS else None

class UserRegistrationData(BaseModel):
    PAN: str = Field(..., min_length=10, max_length=10, description="PAN card number")
    LASTNAME: str = Field(..., min_length=1, max_length=100, description="Last name (mandatory)")
    FIRSTNAME: Optional[str] = Field(default=None, max_length=100, description="First name (optional)")
    MIDDLENAME: Optional[str] = Field(default=None, max_length=100, description="Middle name (optional)")
    DOB_YEAR: str = Field(..., min_length=4, max_length=4, description="Date of birth year (YYYY)")
    DOB_MONTH: str = Field(..., min_length=1, max_length=3, description="Date of birth month (JAN, FEB, etc.)")
    DOB_DAY: str = Field(..., min_length=1, max_length=2, description="Date of birth day (1-31)")
    GENDER: str = Field(..., min_length=1, max_length=20, description="Gender (MALE/FEMALE)")
    RESIDENT: Any = Field(..., description="Resident status (true/false or RES/NRI)")
    MOBILE: str = Field(..., min_length=10, max_length=10, description="Mobile number")
    EMAIL: str = Field(..., min_length=3, max_length=254, description="Email address")
    ADDRESS: str = Field(..., min_length=1, max_length=1000, description="Address")
    PIN: str = Field(..., min_length=6, max_length=6, description="PIN code")
    PASSWORD: str = Field(..., min_length=8, max_length=256, description="Password")
    CONFIRMPWD: str = Field(..., min_length=8, max_length=256, description="Confirm password")
    PERMSG: str = Field(..., min_length=1, max_length=500, description="Personal message")

    @field_validator('PAN')
    @classmethod
    def validate_pan(cls, v):
        v = v.upper().strip()
        if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", v):
            raise ValueError("PAN must match AAAAA9999A")
        return v

    @field_validator('MOBILE')
    @classmethod
    def validate_mobile(cls, v):
        v = v.strip()
        if not v.isdigit() or len(v) != 10:
            raise ValueError("Mobile must be 10 digits")
        return v

    @field_validator('PIN')
    @classmethod
    def validate_pin(cls, v):
        v = v.strip()
        if not v.isdigit() or len(v) != 6:
            raise ValueError("PIN must be 6 digits")
        return v

    @field_validator('EMAIL')
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if len(v) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", v):
            raise ValueError("EMAIL must be a valid email address")
        return v

    @field_validator('GENDER')
    @classmethod
    def validate_gender(cls, v):
        value = v.strip().upper()
        if value not in {"MALE", "FEMALE", "TRANSGENDER"}:
            raise ValueError("GENDER must be MALE, FEMALE, or TRANSGENDER")
        return value

    @field_validator('RESIDENT')
    @classmethod
    def validate_resident(cls, v):
        if isinstance(v, bool):
            return v
        value = str(v).strip()
        if value.lower() not in {"true", "false"} and value.upper() not in {"RES", "NRI"}:
            raise ValueError("RESIDENT must be true, false, RES, or NRI")
        return value

    @model_validator(mode="after")
    def validate_registration(self):
        months = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }
        month_text = self.DOB_MONTH.strip().upper()
        month = months.get(month_text)
        if month is None:
            try:
                month = int(month_text)
            except ValueError:
                month = None
        try:
            datetime(int(self.DOB_YEAR), int(month), int(self.DOB_DAY))
        except (TypeError, ValueError):
            raise ValueError("DOB_YEAR, DOB_MONTH, and DOB_DAY must form a valid date")
        if self.PASSWORD != self.CONFIRMPWD:
            raise ValueError("PASSWORD and CONFIRMPWD must match")
        return self

class RegistrationInitRequest(BaseModel):
    user: UserRegistrationData = Field(..., description="User registration data")

class AadhaarOTPRequest(BaseModel):
    session_id: str = Field(..., min_length=10, max_length=128, description="Registration session ID")
    aadhaar_otp: str = Field(..., min_length=6, max_length=6, description="Aadhaar OTP")

    @field_validator('aadhaar_otp')
    @classmethod
    def validate_otp(cls, v):
        if not v.isdigit():
            raise ValueError("aadhaar_otp must be 6 digits")
        return v

class FinalOTPRequest(BaseModel):
    session_id: str = Field(..., min_length=10, max_length=128, description="Registration session ID")
    mobile_otp: str = Field(..., min_length=6, max_length=6, description="Mobile OTP")
    email_otp: str = Field(..., min_length=6, max_length=6, description="Email OTP")

    @field_validator('mobile_otp', 'email_otp')
    @classmethod
    def validate_otps(cls, v):
        if not v.isdigit():
            raise ValueError("OTP values must be 6 digits")
        return v

class SessionCloseRequest(BaseModel):
    session_id: str = Field(..., min_length=10, max_length=128, description="Registration session ID to close")

class StandardResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    request_id: str = Field(default_factory=lambda: REQUEST_ID.get())
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

def verify_api_key(api_key: Optional[str]) -> Optional[str]:
    if not api_key:
        return None
    try:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        return API_KEYS.get(key_hash)
    except Exception as e:
        logger.error(f"Error verifying API key: {e}")
        return None

def check_rate_limit(client_ip: str) -> bool:
    now = datetime.now()
    with RATE_LIMIT_LOCK:
        request_attempts[client_ip] = [
            attempt for attempt in request_attempts[client_ip]
            if now - attempt < RATE_LIMIT_WINDOW
        ] if client_ip in request_attempts else []
        if len(request_attempts[client_ip]) >= MAX_REQUESTS_PER_IP:
            logger.warning(
                "Rate limit exceeded for client_ip=%s",
                client_ip,
                extra={"event": "rate_limit_exceeded"},
            )
            return False
        request_attempts[client_ip].append(now)
        return True


class PortalServiceWorker:
    """Own one Playwright-backed registration service on a dedicated thread."""

    def __init__(self, user_data: Dict[str, Any]):
        self.user_data = user_data
        self._jobs = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name=f"portal-session-{_fingerprint('pan', user_data.get('PAN'))}",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        self._service = None
        while True:
            method_name, args, kwargs, reply = self._jobs.get()
            try:
                if method_name == "__close__":
                    self._close_service()
                    reply.put((True, None))
                    return
                if method_name == "__bootstrap__":
                    result = self._bootstrap()
                else:
                    if self._service is None:
                        raise RuntimeError("Registration service is not initialized")
                    result = getattr(self._service, method_name)(*args, **kwargs)
                reply.put((True, result))
            except BaseException as exc:
                reply.put((False, exc))

    def _bootstrap(self) -> Dict[str, Any]:
        browser_obj = EPortalLoginStealth(userData={"PAN": self.user_data["PAN"]})
        session_bootstrap = browser_obj.open_register()
        if not session_bootstrap.get("success"):
            try:
                browser_obj.close()
            except Exception:
                pass
            return {
                "success": False,
                "failed_step": "open_register",
                "error": session_bootstrap.get("error", "open_register_failed"),
            }
        self._service = ePortalRegistrationApi(
            self.user_data,
            headers=session_bootstrap["headers"],
            cookies=session_bootstrap["cookies"],
            browser_obj=browser_obj,
        )
        return {"success": True}

    def _close_service(self) -> None:
        if self._service is not None:
            try:
                self._service.close()
            finally:
                self._service = None

    def call(self, method_name: str, *args, timeout: int = 180, **kwargs):
        if self._closed and method_name != "__close__":
            raise RuntimeError("Registration service is closed")
        reply = queue.Queue(maxsize=1)
        self._jobs.put((method_name, args, kwargs, reply))
        ok, value = reply.get(timeout=timeout)
        if ok:
            return value
        raise value

    def bootstrap(self) -> Dict[str, Any]:
        return self.call("__bootstrap__")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.call("__close__", timeout=30)
        except Exception:
            logger.exception("Failed to close portal worker")

    def __getattr__(self, method_name: str):
        def caller(*args, **kwargs):
            return self.call(method_name, *args, **kwargs)
        return caller


def _safe_close(service: Any, reason: str, session_id: Optional[str] = None) -> None:
    if not service:
        return
    try:
        if hasattr(service, "close"):
            service.close()
        elif getattr(service, "session", None) is not None:
            service.session.close()
    except Exception:
        logger.exception(
            "Failed to close registration service; reason=%s",
            reason,
            extra={"event": "service_close_failed", "session_ref": _session_ref(session_id)},
        )


def run_portal_step(name: str, operation, session_id: Optional[str] = None) -> Dict[str, Any]:
    started = monotonic()
    log_extra = {
        "event": "portal_step_finished",
        "portal_step": name,
        "session_ref": _session_ref(session_id),
    }
    try:
        result = operation()
    except Exception:
        log_extra["event"] = "portal_step_exception"
        log_extra["duration_ms"] = round((monotonic() - started) * 1000, 2)
        logger.exception("Portal step raised an exception", extra=log_extra)
        raise

    log_extra["duration_ms"] = round((monotonic() - started) * 1000, 2)
    success = isinstance(result, dict) and bool(result.get("success"))
    logger.log(
        logging.INFO if success else logging.ERROR,
        "Portal step result=%s",
        json.dumps(redact(result), ensure_ascii=True, default=str),
        extra=log_extra,
    )
    return result

def create_registration_session(service: ePortalRegistrationApi) -> str:
    session_id = f"rg_{secrets.token_urlsafe(32)}"
    now = datetime.now()
    with SESSIONS_LOCK:
        registration_sessions[session_id] = {
            'service': service,
            'created_at': now,
            'expires_at': now + SESSION_TIMEOUT,
            'browser_expires_at': now + BROWSER_SESSION_TIMEOUT,
            'stage': 'initialized',
            'is_aadhaar_flow': False,
            'lock': threading.RLock(),
        }
    logger.info(
        "Created registration session",
        extra={"event": "session_created", "session_ref": _session_ref(session_id)},
    )
    return session_id


def remove_registration_session(session_id: str, reason: str, close_service: bool = True):
    with SESSIONS_LOCK:
        session_data = registration_sessions.pop(session_id, None)
    if session_data and close_service:
        _safe_close(session_data.get('service'), reason, session_id)
    if session_data:
        logger.info(
            "Removed registration session; reason=%s",
            reason,
            extra={"event": "session_removed", "session_ref": _session_ref(session_id)},
        )
    return session_data

def cleanup_expired_sessions():
    now = datetime.now()
    with SESSIONS_LOCK:
        expired = []
        for session_id, session_data in registration_sessions.items():
            reason = None
            if now > session_data.get('browser_expires_at', session_data['expires_at']):
                reason = "browser_timeout"
            elif now > session_data['expires_at']:
                reason = "expired"
            if reason:
                expired.append((session_id, reason))
    for session_id, reason in expired:
        remove_registration_session(session_id, reason)
    if expired:
        logger.info(
            "Cleaned up expired sessions; count=%d",
            len(expired),
            extra={"event": "expired_sessions_cleaned"},
        )


def cleanup_expired_sessions_loop():
    while not CLEANUP_STOP_EVENT.wait(SESSION_CLEANUP_INTERVAL_SECONDS):
        try:
            cleanup_expired_sessions()
        except Exception:
            logger.exception("Background session cleanup failed", extra={"event": "session_cleanup_failed"})


def validate_session(session_id: str) -> Dict[str, Any]:
    with SESSIONS_LOCK:
        session_data = registration_sessions.get(session_id)
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired registration session"
        )
    now = datetime.now()
    if now > session_data.get('browser_expires_at', session_data['expires_at']):
        remove_registration_session(session_id, "browser_timeout_on_access")
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Registration browser session expired after 10 minutes. Please restart registration."
        )
    if now > session_data['expires_at']:
        remove_registration_session(session_id, "expired_on_access")
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Registration session expired. Please restart registration."
        )
    with SESSIONS_LOCK:
        if session_id in registration_sessions:
            registration_sessions[session_id]['expires_at'] = datetime.now() + SESSION_TIMEOUT
    return session_data

@asynccontextmanager
async def lifespan(app: FastAPI):
    CLEANUP_STOP_EVENT.clear()
    cleanup_thread = threading.Thread(
        target=cleanup_expired_sessions_loop,
        name="registration-session-cleanup",
        daemon=True,
    )
    cleanup_thread.start()
    logger.info(
        "Registration API started; platform=%s release=%s python=%s environment=%s transport=%s",
        platform.system(),
        platform.release(),
        platform.python_version(),
        ENVIRONMENT,
        os.getenv("EP_PORTAL_HTTP_TRANSPORT", "curl_cffi"),
        extra={"event": "service_started"},
    )
    logger.warning(
        "Registration sessions are process-local; run exactly one API worker",
        extra={"event": "single_worker_required"},
    )
    try:
        yield
    finally:
        logger.info("Registration API shutting down", extra={"event": "service_stopping"})
        CLEANUP_STOP_EVENT.set()
        cleanup_thread.join(timeout=5)
        with SESSIONS_LOCK:
            session_ids = list(registration_sessions)
        for session_id in session_ids:
            remove_registration_session(session_id, "application_shutdown")
        logger.info("Registration API cleanup completed", extra={"event": "service_stopped"})

app = FastAPI(
    title="ePortal Hybrid REST API - Registration Service",
    description="Hybrid registration API using the ePortal registration hybrid flow",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if DEBUG_MODE and not ALLOWED_ORIGINS else ALLOWED_ORIGINS,
    allow_credentials=bool(ALLOWED_ORIGINS and "*" not in ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS or ["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = supplied_request_id if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_request_id) else uuid.uuid4().hex
    token = REQUEST_ID.set(request_id)
    started = monotonic()
    status_code = 500
    log_extra = {
        "event": "http_request_started",
        "http_method": request.method,
        "http_path": request.url.path,
    }
    logger.info("HTTP request started", extra=log_extra)
    try:
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BYTES:
            status_code = 413
            logger.warning(
                "Request body exceeds configured limit; content_length=%s limit=%d",
                content_length,
                MAX_REQUEST_BYTES,
                extra={**log_extra, "event": "request_too_large", "status_code": status_code},
            )
            response = JSONResponse(
                status_code=status_code,
                content=StandardResponse(
                    success=False,
                    message="Request body too large",
                    error="request_too_large",
                ).model_dump(),
            )
            response.headers["X-Request-ID"] = request_id
            return response
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        logger.exception(
            "Unhandled error while processing HTTP request",
            extra={**log_extra, "event": "http_request_exception"},
        )
        raise
    finally:
        logger.log(
            logging.INFO if status_code < 400 else logging.ERROR,
            "HTTP request completed",
            extra={
                **log_extra,
                "event": "http_request_completed",
                "status_code": status_code,
                "duration_ms": round((monotonic() - started) * 1000, 2),
            },
        )
        REQUEST_ID.reset(token)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "Request validation failed; errors=%s body=%s",
        json.dumps(redact(exc.errors()), ensure_ascii=True, default=str),
        json.dumps(redact(exc.body), ensure_ascii=True, default=str),
        extra={
            "event": "request_validation_failed",
            "http_method": request.method,
            "http_path": request.url.path,
            "status_code": 422,
        },
    )
    return JSONResponse(
        status_code=422,
        content=StandardResponse(
            success=False,
            message="Request validation failed",
            error="validation_error",
            data={"errors": _json_safe(exc.errors())},
        ).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(
        "HTTP error; detail=%s",
        exc.detail,
        extra={
            "event": "http_error",
            "http_method": request.method,
            "http_path": request.url.path,
            "status_code": exc.status_code,
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content=StandardResponse(
            success=False,
            message=str(exc.detail),
            error="http_error",
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled API exception",
        extra={
            "event": "unhandled_api_exception",
            "http_method": request.method,
            "http_path": request.url.path,
            "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
        },
    )
    if metrics:
        metrics.total_errors += 1
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=StandardResponse(
            success=False,
            message="Internal server error",
            error="internal_server_error",
        ).model_dump(),
    )

async def verify_auth(request: Request, x_api_key: Optional[str] = Header(None)):
    if metrics:
        metrics.total_requests += 1
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later."
        )
    key_name = verify_api_key(x_api_key)
    if not key_name:
        if metrics:
            metrics.total_errors += 1
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"}
        )
    if metrics:
        metrics.total_authenticated += 1
    logger.debug(f"✓ Authenticated request from {client_ip} as {key_name}")
    return key_name

@app.get("/")
async def root():
    return {
        "service": "ePortal Hybrid REST API - Registration Service",
        "version": "1.0.0",
        "status": "running",
        "environment": ENVIRONMENT,
        "platform": platform.system(),
        "endpoints": {
            "registration_init": "POST /api/v1/registration/init",
            "aadhaar_otp_verify": "POST /api/v1/registration/verify-aadhaar-otp",
            "final_otp_verify": "POST /api/v1/registration/verify-final-otp",
            "close_session": "POST /api/v1/registration/close-session",
            "health": "GET /health",
            "documentation": "GET /docs"
        },
        "flows": {
            "aadhaar_flow": [
                "1. POST /init -> returns session_id, is_aadhaar_flow=true",
                "2. POST /verify-aadhaar-otp -> verifies Aadhaar OTP, fills details, sends email/mobile OTP",
                "3. POST /verify-final-otp -> verifies final OTPs and completes registration"
            ],
            "non_aadhaar_flow": [
                "1. POST /init -> returns session_id, is_aadhaar_flow=false, sends email/mobile OTP",
                "2. POST /verify-final-otp -> verifies final OTPs and completes registration"
            ]
        },
        "authentication": "X-API-Key header required",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    cleanup_expired_sessions()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "sessions": {
            "active_registration_sessions": len(registration_sessions),
            "session_stages": {
                stage: len([s for s in registration_sessions.values() if s['stage'] == stage])
                for stage in ['initialized', 'aadhaar_otp_pending', 'final_otp_pending', 'completed']
            }
        },
        "rate_limiting": {
            "tracked_ips": len(request_attempts),
            "max_per_ip": MAX_REQUESTS_PER_IP,
            "window_minutes": int(RATE_LIMIT_WINDOW.total_seconds() / 60)
        },
        "browser_session_timeout_seconds": int(BROWSER_SESSION_TIMEOUT.total_seconds()),
        "egress_slots": proxy_slot_status(),
        "session_cleanup_interval_seconds": SESSION_CLEANUP_INTERVAL_SECONDS,
        "metrics": metrics.to_dict() if metrics else None
    }

@app.post("/api/v1/registration/init", response_model=StandardResponse)
def registration_init(
    request: RegistrationInitRequest,
    auth: str = Depends(verify_auth)
):
    reg_service = None
    reg_session_id = None
    try:
        cleanup_expired_sessions()
        user_data_dict = request.user.model_dump()
        logger.info(
            "Starting hybrid registration initialization; pan_ref=%s",
            _fingerprint("pan", user_data_dict.get('PAN')),
            extra={"event": "registration_init_started"},
        )
        reg_service = PortalServiceWorker(user_data_dict)
        session_bootstrap = reg_service.bootstrap()
        if not session_bootstrap.get("success"):
            if metrics:
                metrics.failed_registrations += 1
            _safe_close(reg_service, "open_register_failed")
            return StandardResponse(
                success=False,
                message="Registration initialization failed",
                error=session_bootstrap.get("error", "open_register_failed"),
                data={"failed_step": "open_register"},
            )

        step1 = run_portal_step("step1_validate_pan", reg_service.step1_validate_pan)
        if not step1.get("success"):
            if metrics:
                metrics.failed_registrations += 1
            _safe_close(reg_service, "step1_failed")
            return StandardResponse(
                success=False,
                message="Registration initialization failed",
                error=step1.get("error", "pan_validation_failed"),
                data=step1,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if step1.get("aadhar_validation"):
            aadhar_result = run_portal_step("aadhaar_send_otp", reg_service.aadhar_validator)
            if not aadhar_result.get("success"):
                if metrics:
                    metrics.failed_registrations += 1
                _safe_close(reg_service, "aadhaar_send_otp_failed")
                return StandardResponse(
                    success=False,
                    message="Aadhaar validation failed",
                    error=aadhar_result.get("error", "aadhar_validation_failed"),
                    data=aadhar_result,
                )
            reg_session_id = create_registration_session(reg_service)
            registration_sessions[reg_session_id]['stage'] = 'aadhaar_otp_pending'
            registration_sessions[reg_session_id]['is_aadhaar_flow'] = True
            if metrics:
                metrics.aadhaar_registrations += 1

            response_data = {
                "session_id": reg_session_id,
                "pan": user_data_dict.get("PAN"),
                "is_aadhaar_flow": True,
                "next_step": "verify-aadhaar-otp"
            }
            message = "Aadhaar OTP sent. Please verify Aadhaar OTP in next step."
        else:
            detail_result = run_portal_step("step2_validate_details", reg_service.step2_validate_details)
            if not detail_result.get("success"):
                if metrics:
                    metrics.failed_registrations += 1
                _safe_close(reg_service, "step2_failed")
                return StandardResponse(
                    success=False,
                    message="Registration initialization failed",
                    error=detail_result.get("error", "details_validation_failed"),
                    data=detail_result,
                )
            contact_result = run_portal_step("step3_send_contact_otps", reg_service.step3_validate_contact)
            if not contact_result.get("success"):
                if metrics:
                    metrics.failed_registrations += 1
                _safe_close(reg_service, "step3_failed")
                return StandardResponse(
                    success=False,
                    message="Contact validation failed",
                    error=contact_result.get("error", "contact_validation_failed"),
                    data=contact_result,
                )
            reg_session_id = create_registration_session(reg_service)
            registration_sessions[reg_session_id]['stage'] = 'final_otp_pending'
            registration_sessions[reg_session_id]['is_aadhaar_flow'] = False
            if metrics:
                metrics.non_aadhaar_registrations += 1

            response_data = {
                "session_id": reg_session_id,
                "pan": user_data_dict.get("PAN"),
                "is_aadhaar_flow": False,
                "next_step": "verify-final-otp"
            }
            message = "Email and mobile OTP sent. Please verify both OTPs in next step."

        logger.info(
            "Registration initialization completed",
            extra={"event": "registration_init_completed", "session_ref": _session_ref(reg_session_id)},
        )
        return StandardResponse(
            success=True,
            message=message,
            data=response_data
        )

    except Exception as e:
        logger.exception(
            "Registration initialization failed",
            extra={"event": "registration_init_exception", "session_ref": _session_ref(reg_session_id)},
        )
        if reg_session_id:
            remove_registration_session(reg_session_id, "registration_init_exception")
        else:
            _safe_close(reg_service, "registration_init_exception")
        if metrics:
            metrics.total_errors += 1
            metrics.failed_registrations += 1
        return StandardResponse(
            success=False,
            message="Registration initialization failed",
            error="registration_init_exception",
        )

@app.post("/api/v1/registration/verify-aadhaar-otp", response_model=StandardResponse)
def verify_aadhaar_otp(
    request: AadhaarOTPRequest,
    auth: str = Depends(verify_auth)
):
    session_lock = None
    session_lock_acquired = False
    try:
        session_data = validate_session(request.session_id)
        session_lock = session_data['lock']
        session_lock_acquired = session_lock.acquire(blocking=False)
        if not session_lock_acquired:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another request is already processing this registration session",
            )
        if not session_data.get("is_aadhaar_flow"):
            return StandardResponse(
                success=False,
                message="This endpoint is only for Aadhaar registration flow",
                error="Invalid flow type"
            )
        if session_data['stage'] != 'aadhaar_otp_pending':
            return StandardResponse(
                success=False,
                message=f"Invalid stage. Current stage: {session_data['stage']}",
                error="Registration flow out of sequence"
            )

        reg_service = session_data['service']
        logger.info(
            "Verifying Aadhaar OTP",
            extra={"event": "aadhaar_otp_started", "session_ref": _session_ref(request.session_id)},
        )
        otp_result = run_portal_step(
            "aadhaar_validate_otp",
            lambda: reg_service.step4_validate_otp(otp=request.aadhaar_otp, panadhar=True),
            request.session_id,
        )
        if not otp_result.get("success"):
            if metrics:
                metrics.total_errors += 1
            return StandardResponse(
                success=False,
                message="Aadhaar OTP verification failed",
                error=otp_result.get("error", "aadhaar_otp_failed"),
                data=otp_result,
            )

        detail_result = run_portal_step(
            "step2_validate_details",
            reg_service.step2_validate_details,
            request.session_id,
        )
        if not detail_result.get("success"):
            if metrics:
                metrics.total_errors += 1
            return StandardResponse(
                success=False,
                message="Details validation failed after Aadhaar OTP",
                error=detail_result.get("error", "details_validation_failed"),
                data=detail_result,
            )

        contact_result = run_portal_step(
            "step3_send_contact_otps",
            reg_service.step3_validate_contact,
            request.session_id,
        )
        if not contact_result.get("success"):
            if metrics:
                metrics.total_errors += 1
            return StandardResponse(
                success=False,
                message="Contact validation failed after Aadhaar OTP",
                error=contact_result.get("error", "contact_validation_failed"),
                data=contact_result,
            )

        registration_sessions[request.session_id]['stage'] = 'final_otp_pending'
        logger.info(
            "Aadhaar OTP verified and contact OTPs sent",
            extra={"event": "aadhaar_otp_completed", "session_ref": _session_ref(request.session_id)},
        )
        return StandardResponse(
            success=True,
            message="Aadhaar OTP verified successfully. Email and mobile OTP sent. Please verify in next step.",
            data={
                "session_id": request.session_id,
                "next_step": "verify-final-otp"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Aadhaar OTP endpoint failed",
            extra={"event": "aadhaar_otp_exception", "session_ref": _session_ref(request.session_id)},
        )
        if metrics:
            metrics.total_errors += 1
        return StandardResponse(
            success=False,
            message="Aadhaar OTP verification failed",
            error="aadhaar_otp_exception",
        )
    finally:
        if session_lock_acquired:
            session_lock.release()

@app.post("/api/v1/registration/verify-final-otp", response_model=StandardResponse)
def verify_final_otp(
    request: FinalOTPRequest,
    auth: str = Depends(verify_auth)
):
    session_lock = None
    session_lock_acquired = False
    try:
        session_data = validate_session(request.session_id)
        session_lock = session_data['lock']
        session_lock_acquired = session_lock.acquire(blocking=False)
        if not session_lock_acquired:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another request is already processing this registration session",
            )
        if session_data['stage'] != 'final_otp_pending':
            return StandardResponse(
                success=False,
                message=f"Invalid stage. Current stage: {session_data['stage']}",
                error="Registration flow out of sequence"
            )

        reg_service = session_data['service']
        logger.info(
            "Verifying contact OTPs",
            extra={"event": "contact_otp_started", "session_ref": _session_ref(request.session_id)},
        )
        otp_result = run_portal_step(
            "step4_validate_contact_otps",
            lambda: reg_service.step4_validate_otp(
                otp=request.mobile_otp,
                email_otp=request.email_otp,
                panadhar=False,
            ),
            request.session_id,
        )
        if not otp_result.get("success"):
            if metrics:
                metrics.failed_registrations += 1
            return StandardResponse(
                success=False,
                message="Final OTP verification failed",
                error=otp_result.get("error", "final_otp_failed"),
                data=otp_result,
            )

        password_result = run_portal_step(
            "step5_set_password",
            reg_service.step5_set_new_password,
            request.session_id,
        )
        if not password_result.get("success"):
            if metrics:
                metrics.failed_registrations += 1
            return StandardResponse(
                success=False,
                message="Password setup failed",
                error=password_result.get("error", "password_set_failed"),
                data=password_result,
            )

        if metrics:
            metrics.successful_registrations += 1
        remove_registration_session(request.session_id, "registration_completed")

        logger.info(
            "Registration completed successfully",
            extra={"event": "registration_completed", "session_ref": _session_ref(request.session_id)},
        )
        return StandardResponse(
            success=True,
            message="Registration completed successfully!",
            data=password_result,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Final OTP endpoint failed",
            extra={"event": "final_otp_exception", "session_ref": _session_ref(request.session_id)},
        )
        if metrics:
            metrics.total_errors += 1
            metrics.failed_registrations += 1
        return StandardResponse(
            success=False,
            message="Final OTP verification failed",
            error="final_otp_exception",
        )
    finally:
        if session_lock_acquired:
            session_lock.release()

@app.post("/api/v1/registration/close-session", response_model=StandardResponse)
def close_session(
    request: SessionCloseRequest,
    auth: str = Depends(verify_auth)
):
    session_lock = None
    session_lock_acquired = False
    try:
        with SESSIONS_LOCK:
            session_data = registration_sessions.get(request.session_id)
        if not session_data:
            return StandardResponse(
                success=False,
                message="Session not found or already closed",
                error="Invalid session ID"
            )
        session_lock = session_data['lock']
        session_lock_acquired = session_lock.acquire(blocking=False)
        if not session_lock_acquired:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another request is already processing this registration session",
            )
        remove_registration_session(request.session_id, "client_requested")
        return StandardResponse(
            success=True,
            message="Session closed successfully",
            data={
                "session_id": request.session_id,
                "closed_at": datetime.now().isoformat()
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Failed to close registration session",
            extra={"event": "close_session_exception", "session_ref": _session_ref(request.session_id)},
        )
        if metrics:
            metrics.total_errors += 1
        return StandardResponse(
            success=False,
            message="Failed to close session",
            error="close_session_exception",
        )
    finally:
        if session_lock_acquired:
            session_lock.release()

if __name__ == "__main__":
    import uvicorn
    logger.info(
        "Starting API server; host=%s port=%d",
        API_HOST,
        API_PORT,
        extra={"event": "uvicorn_starting"},
    )
    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level=LOG_LEVEL.lower(),
        access_log=False,
        workers=1,
        loop="asyncio",
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips=FORWARDED_ALLOW_IPS,
    )
