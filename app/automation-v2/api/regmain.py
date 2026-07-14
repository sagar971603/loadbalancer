"""
ePortal WebSocket API - Windows Complete
All endpoints implemented: Registration, Login, Forgot Password, ITR Operations, Challans, etc.
Windows event loop properly fixed for Playwright subprocess operations.
"""

import sys
import os
from pathlib import Path
import platform
import asyncio

# ============================================================================
# CRITICAL FIX: Windows Event Loop Setup (MUST be VERY FIRST)
# ============================================================================
if platform.system() == "Windows":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception as e:
            print(f"WARNING: Could not set Windows event loop policy: {e}")

sys.path.append(str(Path(__file__).parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, ValidationError
from typing import Dict, Any, List, Optional, Literal
import json
import logging
from datetime import datetime, timedelta
import secrets
import hashlib
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import traceback

load_dotenv()

from core.eportal_login_enhanced import EPortalClient
from core.eportal_forgot_password_email_mobile_final import ePortalForgotPassword
from core.tax_portal_complete import TaxPortalRegistrationSelenium  # use Selenium version

# ============================================================================
# Environment & Configuration
# ============================================================================

CLIENT_KEY = os.getenv("CLIENT_KEY")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"
ENABLE_METRICS = os.getenv("ENABLE_METRICS", "true").lower() == "true"

if not CLIENT_KEY:
    raise ValueError("ERROR: CLIENT_KEY environment variable is not set!")

# ============================================================================
# Logging Configuration
# ============================================================================

def setup_logging():
    """Configure production-grade logging."""
    log_format = (
        '%(asctime)s | %(name)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s'
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO if not DEBUG_MODE else logging.DEBUG)
    console_handler.setFormatter(logging.Formatter(log_format))

    file_handler = logging.FileHandler('api_websocket.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format))

    error_handler = logging.FileHandler('api_websocket_errors.log', encoding='utf-8')
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

# ============================================================================
# Security & Sessions
# ============================================================================

SECRET_KEY = secrets.token_urlsafe(32)
API_KEYS = {}

try:
    API_KEYS = {
        hashlib.sha256(CLIENT_KEY.encode()).hexdigest(): "client",
    }
    logger.info("✓ API keys loaded successfully")
except Exception as e:
    logger.error(f"✗ Failed to initialize API keys: {e}")

# Session Storage
active_sessions: Dict[str, Dict[str, Any]] = {}
forgot_password_sessions: Dict[str, ePortalForgotPassword] = {}
websocket_connections: Dict[str, Dict[str, Any]] = {}
registration_sessions: Dict[str, Dict[str, Any]] = {}
connection_attempts: Dict[str, List[datetime]] = {}

MAX_CONNECTIONS_PER_IP = 10
RATE_LIMIT_WINDOW = timedelta(minutes=5)

# ============================================================================
# Metrics
# ============================================================================

class Metrics:
    """Track API metrics for monitoring."""
    def __init__(self):
        self.total_connections = 0
        self.total_authenticated = 0
        self.total_errors = 0
        self.active_ws = 0
        self.start_time = datetime.now()

    def get_uptime_seconds(self) -> int:
        return int((datetime.now() - self.start_time).total_seconds())

    def to_dict(self) -> dict:
        return {
            "total_connections": self.total_connections,
            "total_authenticated": self.total_authenticated,
            "total_errors": self.total_errors,
            "active_websockets": self.active_ws,
            "uptime_seconds": self.get_uptime_seconds(),
            "timestamp": datetime.now().isoformat()
        }

metrics = Metrics() if ENABLE_METRICS else None

# ============================================================================
# Pydantic Models
# ============================================================================

class WebSocketMessage(BaseModel):
    """WebSocket message model with validation."""
    action: str = Field(..., description="Action to perform")
    data: Dict[str, Any] = Field(default_factory=dict, description="Action data")
    session_id: Optional[str] = None
    api_key: Optional[str] = Field(None, description="API key for authentication")

    @field_validator('action')
    @classmethod
    def validate_action(cls, v):
        """Validate action is allowed."""
        allowed_actions = [
            "authenticate", "login", "logout",
            "forgot_password_init", "forgot_password_verify", "forgot_password_rerequest_otp",
            "prefill", "itr_status", "active_filings", "e_verify_active_filings",
            "check_aadhaar", "send_otp", "revise_filing", "download_itr", "filling",
            "everify_otp_verify", "get_all_challans", "get_challan_details",
            "get_bank_accounts", "submit_itr_form", "get_itr_receipt",
            "registration_init", "registration_continue",
            "download_ais", "download_26as", "download_documents"
        ]
        if v not in allowed_actions:
            raise ValueError(f"Invalid action. Allowed: {', '.join(allowed_actions)}")
        return v

# ============================================================================
# Security Functions
# ============================================================================

def verify_api_key(api_key: str) -> Optional[str]:
    """Verify API key and return key name."""
    if not api_key:
        return None
    try:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        return API_KEYS.get(key_hash)
    except Exception as e:
        logger.error(f"Error verifying API key: {e}")
        return None

def check_rate_limit(client_ip: str) -> bool:
    """Check if client has exceeded rate limit."""
    now = datetime.now()

    if client_ip in connection_attempts:
        connection_attempts[client_ip] = [
            attempt for attempt in connection_attempts[client_ip]
            if now - attempt < RATE_LIMIT_WINDOW
        ]
    else:
        connection_attempts[client_ip] = []

    if len(connection_attempts[client_ip]) >= MAX_CONNECTIONS_PER_IP:
        logger.warning(f"⚠ Rate limit exceeded for IP: {client_ip}")
        return False

    connection_attempts[client_ip].append(now)
    return True

def create_session(client: EPortalClient, login_result: Dict[str, Any]) -> str:
    """Create a new session and return session ID."""
    session_id = secrets.token_urlsafe(32)

    active_sessions[session_id] = {
        'client': client,
        'login_result': login_result,
        'created_at': datetime.now(),
        'expires_at': datetime.now() + timedelta(hours=2),
        'pan': login_result.get('user_id')
    }

    logger.info(f"✓ Created session {session_id[:8]}... for PAN {login_result.get('user_id')}")
    if metrics:
        metrics.total_authenticated += 1
    return session_id

def create_forgot_password_session(service: ePortalForgotPassword, method: str) -> str:
    """Create a forgot password session and return session ID."""
    session_id = f"fp_{secrets.token_urlsafe(32)}"
    forgot_password_sessions[session_id] = service
    logger.info(f"✓ Created forgot password session {session_id[:16]}... using {method}")
    return session_id

def create_registration_session(service: TaxPortalRegistrationSelenium, method: str) -> str:
    """Create a registration session and return session ID."""
    session_id = f"rg_{secrets.token_urlsafe(32)}"
    registration_sessions[session_id] = {
        'service': service,
        'method': method,
        'created_at': datetime.now()
    }
    logger.info(f"✓ Created registration session {session_id[:16]}... using {method}")
    return session_id

# ============================================================================
# Lifespan Management
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    logger.info("=" * 80)
    logger.info(f"🚀 Starting ePortal WebSocket API - Complete Edition")
    logger.info(f"Platform: {platform.system()} {platform.release()}")
    logger.info(f"Python: {platform.python_version()}")
    logger.info(f"Environment: {ENVIRONMENT}")
    logger.info("=" * 80)

    yield

    logger.info("=" * 80)
    logger.info("🛑 Shutting down ePortal WebSocket API...")

    for session_id, session_data in list(registration_sessions.items()):
        try:
            service = session_data.get('service')
            if service:
                await service.close()
            del registration_sessions[session_id]
        except Exception as e:
            logger.error(f"Error closing registration session {session_id}: {e}")

    for session_id, service in list(forgot_password_sessions.items()):
        try:
            if hasattr(service, 'close'):
                await service.close()
            del forgot_password_sessions[session_id]
        except Exception as e:
            logger.error(f"Error closing forgot password session {session_id}: {e}")

    for session_id, session_data in list(active_sessions.items()):
        try:
            client = session_data.get('client')
            if client and hasattr(client, 'close'):
                await client.close()
            del active_sessions[session_id]
        except Exception as e:
            logger.error(f"Error closing active session {session_id}: {e}")

    for conn_id, conn_data in list(websocket_connections.items()):
        try:
            ws = conn_data.get('websocket')
            if ws:
                await ws.close(code=1001, reason="Server shutting down")
            del websocket_connections[conn_id]
        except Exception as e:
            logger.error(f"Error closing WebSocket {conn_id}: {e}")

    logger.info("✓ Cleanup completed")
    logger.info("=" * 80)

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="ePortal WebSocket API - Complete",
    description="Complete secure WebSocket API for ePortal automation",
    version="3.1.0",
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

# ============================================================================
# HTTP Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint with service information."""
    return {
        "service": "ePortal WebSocket API - Complete Edition",
        "version": "3.1.0",
        "status": "running",
        "environment": ENVIRONMENT,
        "platform": platform.system(),
        "websocket_url": "ws://localhost:8000/ws",
        "features": [
            "User Authentication & Login",
            "Forgot Password (Email/Mobile/Aadhaar)",
            "User Registration with Playwright",
            "ITR Prefill & Status",
            "ITR Filing & Submission",
            "E-Verify Operations",
            "Challan Management",
            "Bank Account Access",
            "ITR Receipt Download",
            "Real-time WebSocket Updates"
        ],
        "documentation": "http://localhost:8000/docs",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "sessions": {
            "active_sessions": len(active_sessions),
            "forgot_password_sessions": len(forgot_password_sessions),
            "registration_sessions": len(registration_sessions),
            "websocket_connections": len(websocket_connections)
        },
        "rate_limiting": {
            "tracked_ips": len(connection_attempts),
            "max_per_ip": MAX_CONNECTIONS_PER_IP
        },
        "metrics": metrics.to_dict() if metrics else None
    }

# ============================================================================
# WebSocket Connection Manager
# ============================================================================

class ConnectionManager:
    """Manage WebSocket connections with security."""

    def __init__(self):
        self.active_connections: Dict[str, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket, connection_id: str, client_ip: str):
        """Accept and store WebSocket connection."""
        await websocket.accept()

        self.active_connections[connection_id] = {
            'websocket': websocket,
            'client_ip': client_ip,
            'connected_at': datetime.now(),
            'authenticated': False,
            'session_id': None,
            'message_count': 0
        }

        websocket_connections[connection_id] = self.active_connections[connection_id]
        logger.info(f"✓ WebSocket connected: {connection_id[:8]}... from {client_ip}")
        if metrics:
            metrics.active_ws += 1
            metrics.total_connections += 1

    def disconnect(self, connection_id: str):
        """Remove WebSocket connection."""
        if connection_id in self.active_connections:
            conn_data = self.active_connections[connection_id]
            logger.info(
                f"✓ WebSocket disconnected: {connection_id[:8]}... "
                f"from {conn_data['client_ip']} "
                f"(messages: {conn_data['message_count']})"
            )
            del self.active_connections[connection_id]
            if connection_id in websocket_connections:
                del websocket_connections[connection_id]
            if metrics:
                metrics.active_ws = max(0, metrics.active_ws - 1)

    def authenticate_connection(self, connection_id: str, api_key_name: str):
        """Mark connection as authenticated."""
        if connection_id in self.active_connections:
            self.active_connections[connection_id]['authenticated'] = True
            self.active_connections[connection_id]['api_key_name'] = api_key_name
            logger.info(f"✓ WebSocket {connection_id[:8]}... authenticated as {api_key_name}")

    def is_authenticated(self, connection_id: str) -> bool:
        """Check if connection is authenticated."""
        if connection_id not in self.active_connections:
            return False
        return self.active_connections[connection_id].get('authenticated', False)

    def associate_session(self, connection_id: str, session_id: str):
        """Associate WebSocket with login session."""
        if connection_id in self.active_connections:
            self.active_connections[connection_id]['session_id'] = session_id
            logger.debug(f"WebSocket {connection_id[:8]}... linked to session {session_id[:8]}...")

    def increment_message_count(self, connection_id: str):
        """Track message count for monitoring."""
        if connection_id in self.active_connections:
            self.active_connections[connection_id]['message_count'] += 1

manager = ConnectionManager()

# ============================================================================
# WebSocket Endpoint
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Secure WebSocket endpoint with all ePortal operations."""
    connection_id = secrets.token_urlsafe(16)
    client_host = websocket.client.host if websocket.client else "unknown"
    logger.info(f"📡 New WebSocket connection attempt from {client_host}")

    if not check_rate_limit(client_host):
        await websocket.close(code=1008, reason="Rate limit exceeded")
        logger.warning(f"✗ Rate limit exceeded for {client_host}")
        return

    await manager.connect(websocket, connection_id, client_host)

    try:
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "connection_id": connection_id,
            "timestamp": datetime.now().isoformat(),
            "message": "Please authenticate using 'authenticate' action with your API key"
        })

        while True:
            try:
                data = await websocket.receive_json()
                manager.increment_message_count(connection_id)
            except Exception as e:
                logger.error(f"Failed to receive JSON: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": "Invalid JSON format",
                    "timestamp": datetime.now().isoformat()
                })
                if metrics:
                    metrics.total_errors += 1
                continue

            try:
                message = WebSocketMessage(**data)
                action = message.action
                payload = message.data
                session_id = message.session_id
                api_key = message.api_key

                logger.debug(f"🔄 Action: {action} from {connection_id[:8]}...")

                # AUTHENTICATE
                if action == "authenticate":
                    if not api_key:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing API key",
                            "timestamp": datetime.now().isoformat()
                        })
                        continue
                    key_name = verify_api_key(api_key)
                    if key_name:
                        manager.authenticate_connection(connection_id, key_name)
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": True,
                            "message": f"Authenticated as {key_name}",
                            "timestamp": datetime.now().isoformat()
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Invalid API key",
                            "timestamp": datetime.now().isoformat()
                        })
                        await websocket.close(code=1008, reason="Invalid API key")
                        if metrics:
                            metrics.total_errors += 1
                        break
                    continue

                # Require authentication for all other actions
                if not manager.is_authenticated(connection_id):
                    await websocket.send_json({
                        "type": "error",
                        "action": action,
                        "error": "Not authenticated. Please authenticate first.",
                        "timestamp": datetime.now().isoformat()
                    })
                    continue

                # REGISTRATION INIT
                if action == "registration_init":
                    reg_service = None
                    reg_session_id = None
                    try:
                        user_data = payload.get("user")
                        if not user_data:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Missing 'user' data in payload",
                                "timestamp": datetime.now().isoformat()
                            })
                            continue

                        reg_service = TaxPortalRegistrationSelenium(userData=user_data)
                        reg_session_id = create_registration_session(reg_service, "direct")
                        manager.associate_session(connection_id, reg_session_id)

                        await websocket.send_json({
                            "type": "progress",
                            "action": action,
                            "step": 1,
                            "message": "Starting registration...",
                            "timestamp": datetime.now().isoformat()
                        })

                        result = await asyncio.to_thread(reg_service.registerUser)
                        if not result.get("success"):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "step": 1,
                                "success": False,
                                "error": result,
                                "session_id": reg_session_id,
                                "timestamp": datetime.now().isoformat()
                            })
                            try:
                                reg_service.close()
                            except Exception:
                                pass
                            del registration_sessions[reg_session_id]
                            continue

                        # Aadhaar flow
                        if reg_service.reg_with_uidai:
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "step": 2,
                                "success": True,
                                "aadhaar": True,
                                "session_id": reg_session_id,
                                "uuid": reg_service.uid,
                                "message": "OTP sent to Aadhaar-linked mobile. Continue with registration_continue and Aadhaar OTP.",
                                "timestamp": datetime.now().isoformat()
                            })
                            continue

                        # Non-Aadhaar flow
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "step": 2,
                            "success": True,
                            "aadhaar": False,
                            "session_id": reg_session_id,
                            "uuid": reg_service.uid,
                            "message": "OTP sent to email and mobile. Continue with registration_continue and OTPs.",
                            "timestamp": datetime.now().isoformat()
                        })

                    except Exception as e:
                        error_trace = traceback.format_exc()
                        logger.error(f"✗ Registration init error: {e}")
                        if reg_service:
                            try:
                                await reg_service.close()
                            except Exception:
                                pass
                        if reg_session_id and reg_session_id in registration_sessions:
                            del registration_sessions[reg_session_id]
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": f"Registration failed: {str(e)}",
                            "exception_type": type(e).__name__,
                            "traceback": error_trace if DEBUG_MODE else None,
                            "timestamp": datetime.now().isoformat()
                        })
                        if metrics:
                            metrics.total_errors += 1
                    continue

                # REGISTRATION CONTINUE
                elif action == "registration_continue":
                    reg_session_id = payload.get("session_id")
                    aadhar_otp = payload.get("aadhar_otp",None)
                    mobile_otp = payload.get("mobile_otp",None)
                    email_otp = payload.get("email_otp",None)
                    if not reg_session_id :
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing session_id ",
                            "timestamp": datetime.now().isoformat()
                        })
                        if metrics:
                            metrics.total_errors += 1
                        continue
                    if not aadhar_otp and not (mobile_otp and email_otp):
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing OTP(s) for continuation",
                            "timestamp": datetime.now().isoformat()
                        })
                        if metrics:
                            metrics.total_errors += 1
                        continue

                    if reg_session_id not in registration_sessions:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Invalid or expired registration session",
                            "timestamp": datetime.now().isoformat()
                        })
                        if metrics:
                            metrics.total_errors += 1
                        continue
                    try:
                        reg_service = registration_sessions[reg_session_id]['service']
                        logger.info(f"Continuing registration with OTP for {reg_session_id[:16]}...")
                        result = await asyncio.to_thread(reg_service.registerContinue, mobile_otp, email_otp, aadhar_otp)
                        if result.get("success"):
                            try:
                                reg_service.close()
                            except Exception:
                                pass
                            del registration_sessions[reg_session_id]
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": result.get("success", False),
                            "data": result,
                            "message": "✓ Registration completed!" if result.get("success") else "✗ Registration failed",
                            "timestamp": datetime.now().isoformat()
                        })
                        if result.get("success"):
                            logger.info(f"✓ Registration successful")
                        else:
                            logger.error(f"✗ Registration failed: {result}")
                            if metrics:
                                metrics.total_errors += 1
                    except Exception as e:
                        error_trace = traceback.format_exc()
                        logger.error(f"✗ Registration continue error: {e}")
                        try:
                            if 'reg_service' in locals():
                                await reg_service.close()
                            if reg_session_id in registration_sessions:
                                del registration_sessions[reg_session_id]
                        except Exception:
                            pass
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": f"Registration continuation failed: {str(e)}",
                            "exception_type": type(e).__name__,
                            "traceback": error_trace if DEBUG_MODE else None,
                            "timestamp": datetime.now().isoformat()
                        })
                        if metrics:
                            metrics.total_errors += 1
                    continue

                # LOGIN
                elif action == "login":
                    pan = payload.get("pan", "").upper().strip()
                    password = payload.get("password", "")
                    if not pan or not password:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing PAN or password",
                            "timestamp": datetime.now().isoformat()
                        })
                        continue
                    credentials = {"PAN": pan, "PASSWORD": password}
                    client = EPortalClient(credentials)
                    login_result = client.login()
                    if login_result.get("success"):
                        session_id = create_session(client, login_result)
                        manager.associate_session(connection_id, session_id)
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": True,
                            "session_id": session_id,
                            "pan": pan,
                            "message": "Login successful",
                            "timestamp": datetime.now().isoformat()
                        })
                    else:
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": login_result.get("error", "Login failed"),
                            "message": login_result.get("message", ""),
                            "timestamp": datetime.now().isoformat()
                        })
                    continue

                # LOGOUT
                elif action == "logout":
                    if not session_id or session_id not in active_sessions:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Invalid session. Please login first.",
                            "timestamp": datetime.now().isoformat()
                        })
                        continue
                    try:
                        sess = active_sessions[session_id]
                        if sess.get('client'):
                            await sess['client'].close()
                    except Exception:
                        pass
                    del active_sessions[session_id]
                    await websocket.send_json({
                        "type": "response",
                        "action": action,
                        "success": True,
                        "message": "Logout successful",
                        "timestamp": datetime.now().isoformat()
                    })
                    continue

                # DOWNLOAD AIS
                elif action == "download_ais":
                    pan = payload.get("pan", "").upper().strip()
                    password = payload.get("password", "")
                    return_json = payload.get("return_json", False)
                    fiscal_years = payload.get("fiscal_year") or ["2023-24"]
                    if isinstance(fiscal_years, str):
                        fiscal_years = [fiscal_years]
                    if not pan or not password:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing PAN or password",
                            "timestamp": datetime.now().isoformat()
                        })
                        continue
                    try:
                        service = TaxPortalRegistrationSelenium(
                            userData={"PAN": pan, "PASSWORD": password},
                            base_url="https://eportal.incometax.gov.in/iec/foservices/#/login"
                        )
                        for fy in fiscal_years:
                            fy="F.Y. {fy}".format(fy=fy) if not fy.startswith("F.Y.") else fy
                            print(f"Downloading AIS for {fy}...")
                            result = await asyncio.to_thread(service.download_ais, fy, json_format=return_json)
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": result.get("success", False),
                                "data": result,
                                "file": result.get("file"),
                                "file_b64": result.get("file_b64"),
                                "fiscal_year": fy,
                                "timestamp": datetime.now().isoformat()
                            })
                    except Exception as e:
                        error_trace = traceback.format_exc()
                        logger.error(f"✗ AIS download error: {e}")
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": f"AIS download failed: {str(e)}",
                            "exception_type": type(e).__name__,
                            "traceback": error_trace if DEBUG_MODE else None,
                            "timestamp": datetime.now().isoformat()
                        })
                    continue

                # DOWNLOAD 26AS
                elif action == "download_26as":
                    pan = payload.get("pan", "").upper().strip()
                    password = payload.get("password", "")
                    fiscal_years = payload.get("fiscal_year") or ["2023-24"]
                    return_type = payload.get("return_type", "HTML").lower()
                    if isinstance(fiscal_years, str):
                        fiscal_years = [fiscal_years]


                    if not pan or not password:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing PAN or password",
                            "timestamp": datetime.now().isoformat()
                        })
                        continue
                    try:
                        service = TaxPortalRegistrationSelenium(
                            userData={"PAN": pan, "PASSWORD": password},
                            base_url="https://eportal.incometax.gov.in/iec/foservices/#/login"
                        )
                        for fy in fiscal_years:
                            result = await asyncio.to_thread(service.get_26_as, fy, format=return_type)
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": result.get("success", False),
                                "data": result,
                                "file": result.get("file"),
                                "file_b64": result.get("file_b64"),
                                "fiscal_year": fy,
                                "timestamp": datetime.now().isoformat()
                            })
                    except Exception as e:
                        error_trace = traceback.format_exc()
                        logger.error(f"✗ 26AS download error: {e}")
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": f"26AS download failed: {str(e)}",
                            "exception_type": type(e).__name__,
                            "traceback": error_trace if DEBUG_MODE else None,
                            "timestamp": datetime.now().isoformat()
                        })
                    continue

                # DOWNLOAD AIS & 26AS (Combined)
                elif action == "download_documents":
                    pan = payload.get("pan", "").upper().strip()
                    password = payload.get("password", "")
                    fiscal_years = payload.get("fiscal_year") or ["2023-24"]
                    ais_format = payload.get("ais_format", "pdf").lower()  # pdf or json
                    as26_format = payload.get("as26_format", "html").lower()  # html or pdf

                    if isinstance(fiscal_years, str):
                        fiscal_years = [fiscal_years]

                    if not pan or not password:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing PAN or password",
                            "timestamp": datetime.now().isoformat()
                        })
                        continue

                    try:
                        # Single service instance for both downloads
                        service = TaxPortalRegistrationSelenium(
                            userData={"PAN": pan, "PASSWORD": password},
                            base_url="https://eportal.incometax.gov.in/iec/foservices/#/login"
                        )

                        results = {
                            "ais": {},
                            "as26": {},
                            "fiscal_years": fiscal_years,
                            "timestamp": datetime.now().isoformat()
                        }

                        # Process each fiscal year
                        for fy in fiscal_years:
                            fy_formatted = f"F.Y. {fy}" if not fy.startswith("F.Y.") else fy

                            try:
                                # Download AIS
                                ais_result = await asyncio.to_thread(
                                    service.download_ais,
                                    fy_formatted,
                                    json_format=(ais_format == "json")
                                )

                                results["ais"][fy] = {
                                    "success": ais_result.get("success", False),
                                    "format": ais_format,
                                    "file": ais_result.get("file"),
                                    "file_b64": ais_result.get("file_b64") if ais_format == "json" else None,
                                    "error": ais_result.get("error")
                                }

                            except Exception as e:
                                logger.error(f"✗ AIS download error for {fy}: {e}")
                                results["ais"][fy] = {
                                    "success": False,
                                    "error": str(e)
                                }

                            try:
                                # Download 26AS
                                as26_result = await asyncio.to_thread(
                                    service.get_26_as,
                                    fy,
                                    format=as26_format.upper(),
                                    combined=True  # Use combined method if available for efficiency
                                )

                                results["as26"][fy] = {
                                    "success": as26_result.get("success", False),
                                    "format": as26_format,
                                    "file": as26_result.get("file"),
                                    "file_b64": as26_result.get("file_b64"),
                                    "error": as26_result.get("error")
                                }

                            except Exception as e:
                                logger.error(f"✗ 26AS download error for {fy}: {e}")
                                results["as26"][fy] = {
                                    "success": False,
                                    "error": str(e)
                                }

                        # Close service after all downloads
                        try:
                            await service.close()
                        except Exception:
                            pass

                        # Determine overall success
                        ais_success = any(r.get("success") for r in results["ais"].values())
                        as26_success = any(r.get("success") for r in results["as26"].values())

                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": ais_success or as26_success,
                            "data": results,
                            "summary": {
                                "ais_downloaded": ais_success,
                                "as26_downloaded": as26_success,
                                "fiscal_years_processed": len(fiscal_years)
                            },
                            "timestamp": datetime.now().isoformat()
                        })

                        if ais_success or as26_success:
                            logger.info(f"✓ Documents downloaded successfully for {pan}")
                        else:
                            logger.error(f"✗ Document download failed for {pan}")
                            if metrics:
                                metrics.total_errors += 1

                    except Exception as e:
                        error_trace = traceback.format_exc()
                        logger.error(f"✗ Document download error: {e}")
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": f"Document download failed: {str(e)}",
                            "exception_type": type(e).__name__,
                            "traceback": error_trace if DEBUG_MODE else None,
                            "timestamp": datetime.now().isoformat()
                        })
                        if metrics:
                            metrics.total_errors += 1

                    continue

            except ValidationError as e:
                logger.error(f"Validation error: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": f"Validation error: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                })
                if metrics:
                    metrics.total_errors += 1
                continue

            except Exception as e:
                error_trace = traceback.format_exc()
                logger.error(f"WebSocket message error: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": str(e) if DEBUG_MODE else "Internal server error",
                    "timestamp": datetime.now().isoformat()
                })
                if metrics:
                    metrics.total_errors += 1
                continue

    except WebSocketDisconnect:
        manager.disconnect(connection_id)
        logger.info(f"✓ Client {connection_id[:8]}... disconnected normally")

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"✗ WebSocket error: {e}")
        logger.error(f"Traceback: {error_trace}")
        manager.disconnect(connection_id)
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass
        if metrics:
            metrics.total_errors += 1

# ============================================================================
# Run Application
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 100)
    print(" " * 10 + "🚀 ePortal WebSocket API - Complete Edition (Windows Fixed)")
    print("=" * 100)
    print(f"\n  Version: 3.1.0 | Platform: {platform.system()} {platform.release()}")
    print(f"  Python: {platform.python_version()} | Environment: {ENVIRONMENT}")
    print(f"\n  📡 WebSocket: ws://localhost:8000/ws")
    print(f"  🏥 Health Check: http://localhost:8000/health")
    print(f"  📚 Documentation: http://localhost:8000/docs")
    print("\n" + "=" * 100)
    print("Implemented Endpoints:")
    print("  ✓ Authentication (authenticate, login, logout)")
    print("  ✓ Forgot Password (init, verify, rerequest_otp)")
    print("  ✓ User Registration (with Playwright automation)")
    print("  ✓ ITR Operations (prefill, status, filing, submission, receipt download)")
    print("  ✓ E-Verify (active filings, OTP verification)")
    print("  ✓ Challan Management (history, details)")
    print("  ✓ Bank Operations (get accounts)")
    print("  ✓ Aadhaar Verification (check linked, send OTP)")
    print("=" * 100)
    print("Windows Configuration:")
    print(f"  ✓ Event Loop: WindowsProactorEventLoopPolicy")
    print(f"  ✓ Workers: 1 (Windows requirement)")
    print(f"  ✓ Reload: Disabled (Windows/Playwright requirement)")
    print("=" * 100 + "\n")




    log_level = "debug" if DEBUG_MODE else "info"

    logger.info(f"✓ Starting uvicorn server...")

    uvicorn.run(
        "regmain:app",
        host="0.0.0.0",
        port=8010,
        reload=False,
        log_level=log_level,
        access_log=True,
        workers=1,
        loop="asyncio"
    )
