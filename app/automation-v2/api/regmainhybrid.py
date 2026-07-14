import sys
import os
from pathlib import Path
import platform
import asyncio

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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime, timedelta
import secrets
import hashlib
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import traceback

from core.registration_hybrid import ePortalRegistrationApi

load_dotenv()

CLIENT_KEY = os.getenv("CLIENT_KEY")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"
ENABLE_METRICS = os.getenv("ENABLE_METRICS", "true").lower() == "true"

if not CLIENT_KEY:
    raise ValueError("ERROR: CLIENT_KEY environment variable is not set!")

def setup_logging():
    log_format = (
        '%(asctime)s | %(name)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s'
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO if not DEBUG_MODE else logging.DEBUG)
    console_handler.setFormatter(logging.Formatter(log_format))

    file_handler = logging.FileHandler('api_hybrid_registration.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format))

    error_handler = logging.FileHandler('api_hybrid_registration_errors.log', encoding='utf-8')
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(log_format))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)
    return logging.getLogger(__name__)

logger = setup_logging()

try:
    current_policy = asyncio.get_event_loop_policy()
    logger.info(f"✓ Current Event Loop Policy: {type(current_policy).__name__}")
except Exception as e:
    logger.warning(f"Could not determine event loop policy: {e}")

SECRET_KEY = secrets.token_urlsafe(32)
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

MAX_REQUESTS_PER_IP = 50
RATE_LIMIT_WINDOW = timedelta(minutes=5)
SESSION_TIMEOUT = timedelta(minutes=30)

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
    LASTNAME: str = Field(..., min_length=1, description="Last name (mandatory)")
    FIRSTNAME: Optional[str] = Field(default=None, description="First name (optional)")
    MIDDLENAME: Optional[str] = Field(default=None, description="Middle name (optional)")
    DOB_YEAR: str = Field(..., description="Date of birth year (YYYY)")
    DOB_MONTH: str = Field(..., description="Date of birth month (JAN, FEB, etc.)")
    DOB_DAY: str = Field(..., description="Date of birth day (1-31)")
    GENDER: str = Field(..., description="Gender (MALE/FEMALE)")
    RESIDENT: str = Field(..., description="Resident status (true/false)")
    MOBILE: str = Field(..., min_length=10, max_length=10, description="Mobile number")
    EMAIL: str = Field(..., description="Email address")
    ADDRESS: str = Field(..., description="Address")
    PIN: str = Field(..., min_length=6, max_length=6, description="PIN code")
    PASSWORD: str = Field(..., min_length=8, description="Password")
    CONFIRMPWD: str = Field(..., min_length=8, description="Confirm password")
    PERMSG: str = Field(..., description="Personal message")

    @field_validator('PAN')
    @classmethod
    def validate_pan(cls, v):
        v = v.upper().strip()
        if not v.isalnum() or len(v) != 10:
            raise ValueError("PAN must be 10 alphanumeric characters")
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

class RegistrationInitRequest(BaseModel):
    user: UserRegistrationData = Field(..., description="User registration data")

class AadhaarOTPRequest(BaseModel):
    session_id: str = Field(..., description="Registration session ID")
    aadhaar_otp: str = Field(..., min_length=6, max_length=6, description="Aadhaar OTP")

class FinalOTPRequest(BaseModel):
    session_id: str = Field(..., description="Registration session ID")
    mobile_otp: str = Field(..., min_length=6, max_length=6, description="Mobile OTP")
    email_otp: str = Field(..., min_length=6, max_length=6, description="Email OTP")

class SessionCloseRequest(BaseModel):
    session_id: str = Field(..., description="Registration session ID to close")

class StandardResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
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
    if client_ip in request_attempts:
        request_attempts[client_ip] = [
            attempt for attempt in request_attempts[client_ip]
            if now - attempt < RATE_LIMIT_WINDOW
        ]
    else:
        request_attempts[client_ip] = []

    if len(request_attempts[client_ip]) >= MAX_REQUESTS_PER_IP:
        logger.warning(f"⚠ Rate limit exceeded for IP: {client_ip}")
        return False

    request_attempts[client_ip].append(now)
    return True

def create_registration_session(service: ePortalRegistrationApi, user_data: dict) -> str:
    session_id = f"rg_{secrets.token_urlsafe(32)}"
    registration_sessions[session_id] = {
        'service': service,
        'user_data': user_data,
        'created_at': datetime.now(),
        'expires_at': datetime.now() + SESSION_TIMEOUT,
        'stage': 'initialized',
        'is_aadhaar_flow': False
    }
    logger.info(f"✓ Created registration session {session_id[:16]}...")
    return session_id

def cleanup_expired_sessions():
    now = datetime.now()
    expired = []
    for session_id, session_data in list(registration_sessions.items()):
        if now > session_data['expires_at']:
            expired.append(session_id)
            service = session_data.get('service')
            if service and hasattr(service, 'close'):
                try:
                    service.close()
                except Exception as e:
                    logger.error(f"Error closing expired session {session_id}: {e}")
    for session_id in expired:
        del registration_sessions[session_id]
    if expired:
        logger.info(f"✓ Cleaned up {len(expired)} expired sessions")

def validate_session(session_id: str) -> Dict[str, Any]:
    if session_id not in registration_sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired registration session"
        )
    session_data = registration_sessions[session_id]
    if datetime.now() > session_data['expires_at']:
        service = session_data.get('service')
        if service and hasattr(service, 'close'):
            try:
                service.close()
            except Exception:
                pass
        del registration_sessions[session_id]
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Registration session expired. Please restart registration."
        )
    return session_data

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 80)
    logger.info(f"🚀 Starting ePortal Hybrid REST API - Registration Service")
    logger.info(f"Platform: {platform.system()} {platform.release()}")
    logger.info(f"Python: {platform.python_version()}")
    logger.info(f"Environment: {ENVIRONMENT}")
    logger.info("=" * 80)
    yield
    logger.info("=" * 80)
    logger.info("🛑 Shutting down ePortal Hybrid REST API...")
    for session_id, session_data in list(registration_sessions.items()):
        service = session_data.get('service')
        if service and hasattr(service, 'close'):
            try:
                service.close()
            except Exception as e:
                logger.error(f"Error closing registration session {session_id}: {e}")
        del registration_sessions[session_id]
    logger.info("✓ Cleanup completed")
    logger.info("=" * 80)

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
    allow_origins=["*"] if DEBUG_MODE else [os.getenv("ALLOWED_ORIGINS", "localhost")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)

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
        "metrics": metrics.to_dict() if metrics else None
    }

@app.post("/api/v1/registration/init", response_model=StandardResponse)
async def registration_init(
    request: RegistrationInitRequest,
    auth: str = Depends(verify_auth)
):
    reg_service = None
    reg_session_id = None
    try:
        cleanup_expired_sessions()
        user_data_dict = request.user.model_dump()
        logger.info(f"📝 Starting hybrid registration init for PAN: {user_data_dict.get('PAN')}")
        reg_service = ePortalRegistrationApi(user_data_dict, headers={}, cookies=[])

        step1 = reg_service.step1_validate_pan()
        if not step1.get("success"):
            if metrics:
                metrics.failed_registrations += 1
            return StandardResponse(
                success=False,
                message="Registration initialization failed",
                error=step1.get("error", "pan_validation_failed"),
                data=step1
            )

        if step1.get("aadhar_validation"):
            aadhar_result = reg_service.aadhar_validator()
            if not aadhar_result.get("success"):
                if metrics:
                    metrics.failed_registrations += 1
                return StandardResponse(
                    success=False,
                    message="Aadhaar validation failed",
                    error=aadhar_result.get("error", "aadhar_validation_failed"),
                    data=aadhar_result
                )
            reg_session_id = create_registration_session(reg_service, user_data_dict)
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
            detail_result = reg_service.step2_validate_details()
            if not detail_result.get("success"):
                if metrics:
                    metrics.failed_registrations += 1
                return StandardResponse(
                    success=False,
                    message="Registration initialization failed",
                    error=detail_result.get("error", "details_validation_failed"),
                    data=detail_result
                )
            contact_result = reg_service.step3_validate_contact()
            if not contact_result.get("success"):
                if metrics:
                    metrics.failed_registrations += 1
                return StandardResponse(
                    success=False,
                    message="Contact validation failed",
                    error=contact_result.get("error", "contact_validation_failed"),
                    data=contact_result
                )
            reg_session_id = create_registration_session(reg_service, user_data_dict)
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

        logger.info(f"✓ Hybrid registration init successful for {reg_session_id[:16]}...")
        return StandardResponse(
            success=True,
            message=message,
            data=response_data
        )

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"✗ Hybrid registration init error: {e}")
        logger.error(f"Traceback: {error_trace}")
        if reg_service and hasattr(reg_service, 'close'):
            try:
                reg_service.close()
            except Exception:
                pass
        if reg_session_id and reg_session_id in registration_sessions:
            del registration_sessions[reg_session_id]
        if metrics:
            metrics.total_errors += 1
            metrics.failed_registrations += 1
        return StandardResponse(
            success=False,
            message="Registration initialization failed",
            error=f"{type(e).__name__}: {str(e)}",
            data={"traceback": error_trace} if DEBUG_MODE else None
        )

@app.post("/api/v1/registration/verify-aadhaar-otp", response_model=StandardResponse)
async def verify_aadhaar_otp(
    request: AadhaarOTPRequest,
    auth: str = Depends(verify_auth)
):
    try:
        session_data = validate_session(request.session_id)
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
        logger.info(f"🔐 Verifying Aadhaar OTP for session {request.session_id[:16]}...")
        otp_result = reg_service.step4_validate_otp(otp=request.aadhaar_otp, panadhar=True)
        if not otp_result.get("success"):
            if metrics:
                metrics.total_errors += 1
            return StandardResponse(
                success=False,
                message="Aadhaar OTP verification failed",
                error=otp_result.get("error", "aadhaar_otp_failed"),
                data=otp_result
            )

        detail_result = reg_service.step2_validate_details()
        if not detail_result.get("success"):
            if metrics:
                metrics.total_errors += 1
            return StandardResponse(
                success=False,
                message="Details validation failed after Aadhaar OTP",
                error=detail_result.get("error", "details_validation_failed"),
                data=detail_result
            )

        contact_result = reg_service.step3_validate_contact()
        if not contact_result.get("success"):
            if metrics:
                metrics.total_errors += 1
            return StandardResponse(
                success=False,
                message="Contact validation failed after Aadhaar OTP",
                error=contact_result.get("error", "contact_validation_failed"),
                data=contact_result
            )

        registration_sessions[request.session_id]['stage'] = 'final_otp_pending'
        logger.info(f"✓ Aadhaar OTP verified and email/mobile OTP sent for {request.session_id[:16]}...")
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
        error_trace = traceback.format_exc()
        logger.error(f"✗ Aadhaar OTP verification error: {e}")
        logger.error(f"Traceback: {error_trace}")
        if metrics:
            metrics.total_errors += 1
        return StandardResponse(
            success=False,
            message="Aadhaar OTP verification failed",
            error=f"{type(e).__name__}: {str(e)}",
            data={"traceback": error_trace} if DEBUG_MODE else None
        )

@app.post("/api/v1/registration/verify-final-otp", response_model=StandardResponse)
async def verify_final_otp(
    request: FinalOTPRequest,
    auth: str = Depends(verify_auth)
):
    try:
        session_data = validate_session(request.session_id)
        if session_data['stage'] != 'final_otp_pending':
            return StandardResponse(
                success=False,
                message=f"Invalid stage. Current stage: {session_data['stage']}",
                error="Registration flow out of sequence"
            )

        reg_service = session_data['service']
        logger.info(f"🔐 Verifying final OTPs for session {request.session_id[:16]}...")
        otp_result = reg_service.step4_validate_otp(
            otp=request.mobile_otp,
            email_otp=request.email_otp,
            panadhar=False
        )
        if not otp_result.get("success"):
            if metrics:
                metrics.failed_registrations += 1
            if request.session_id in registration_sessions:
                del registration_sessions[request.session_id]
            return StandardResponse(
                success=False,
                message="Final OTP verification failed",
                error=otp_result.get("error", "final_otp_failed"),
                data=otp_result
            )

        password_result = reg_service.step5_set_new_password()
        if not password_result.get("success"):
            if metrics:
                metrics.failed_registrations += 1
            if request.session_id in registration_sessions:
                del registration_sessions[request.session_id]
            return StandardResponse(
                success=False,
                message="Password setup failed",
                error=password_result.get("error", "password_set_failed"),
                data=password_result
            )

        if metrics:
            metrics.successful_registrations += 1
        if request.session_id in registration_sessions:
            del registration_sessions[request.session_id]

        logger.info(f"✓ Registration completed successfully for {request.session_id[:16]}...")
        return StandardResponse(
            success=True,
            message="Registration completed successfully!",
            data=password_result
        )

    except HTTPException:
        raise
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"✗ Final OTP verification error: {e}")
        logger.error(f"Traceback: {error_trace}")
        try:
            if request.session_id in registration_sessions:
                service = registration_sessions[request.session_id].get('service')
                if service and hasattr(service, 'close'):
                    service.close()
                del registration_sessions[request.session_id]
        except Exception:
            pass
        if metrics:
            metrics.total_errors += 1
            metrics.failed_registrations += 1
        return StandardResponse(
            success=False,
            message="Final OTP verification failed",
            error=f"{type(e).__name__}: {str(e)}",
            data={"traceback": error_trace} if DEBUG_MODE else None
        )

@app.post("/api/v1/registration/close-session", response_model=StandardResponse)
async def close_session(
    request: SessionCloseRequest,
    auth: str = Depends(verify_auth)
):
    try:
        if request.session_id not in registration_sessions:
            return StandardResponse(
                success=False,
                message="Session not found or already closed",
                error="Invalid session ID"
            )
        session_data = registration_sessions[request.session_id]
        service = session_data.get('service')
        if service and hasattr(service, 'close'):
            try:
                service.close()
                logger.info(f"✓ Closed hybrid registration service for session {request.session_id[:16]}...")
            except Exception as e:
                logger.warning(f"Error closing service for session {request.session_id[:16]}: {e}")
        del registration_sessions[request.session_id]
        return StandardResponse(
            success=True,
            message="Session closed successfully",
            data={
                "session_id": request.session_id,
                "closed_at": datetime.now().isoformat()
            }
        )
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"✗ Error closing session: {e}")
        logger.error(f"Traceback: {error_trace}")
        if metrics:
            metrics.total_errors += 1
        return StandardResponse(
            success=False,
            message="Failed to close session",
            error=f"{type(e).__name__}: {str(e)}",
            data={"traceback": error_trace} if DEBUG_MODE else None
        )

if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 100)
    print(" " * 10 + "🚀 ePortal Hybrid REST API - Registration Service")
    print("=" * 100)
    print(f"\n  Version: 1.0.0 | Platform: {platform.system()} {platform.release()}")
    print(f"  Python: {platform.python_version()} | Environment: {ENVIRONMENT}")
    print(f"\n  🌐 API Base: http://localhost:8002")
    print(f"  🏥 Health Check: http://localhost:8002/health")
    print(f"  📚 Documentation: http://localhost:8002/docs")
    print("\n" + "=" * 100)
    log_level = "debug" if DEBUG_MODE else "info"
    logger.info("✓ Starting uvicorn hybrid registration server on port 8002...")
    uvicorn.run(
        "regmainhybrid:app",
        host="0.0.0.0",
        port=8012,
        reload=False,
        log_level=log_level,
        access_log=True,
        workers=1,
        loop="asyncio"
    )
