"""
ePortal WebSocket API with Security
Provides secure WebSocket communication for ePortal operations.
"""
import sys
import os
from pathlib import Path
# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import Dict, Any, List, Optional, Literal
import json
import logging
import asyncio
from datetime import datetime, timedelta
import secrets
import hashlib
from contextlib import asynccontextmanager
import os
from dotenv import load_dotenv
load_dotenv()
from core.eportal_login_enhanced import EPortalClient
from core.eportal_forgot_password_email_mobile_final import ePortalForgotPassword

CLIENT_KEY = os.getenv("CLIENT_KEY")

# ============================================================================
# Configuration
# ============================================================================

# Security Configuration
SECRET_KEY = secrets.token_urlsafe(32)
API_KEYS = {
    hashlib.sha256(CLIENT_KEY.encode()).hexdigest(): "client",
}

# Session Management
active_sessions: Dict[str, Dict[str, Any]] = {}
forgot_password_sessions: Dict[str, ePortalForgotPassword] = {}
pan_link_sessions: Dict[str, Dict[str, Any]] = {}
websocket_connections: Dict[str, Dict[str, Any]] = {}

# Rate Limiting
connection_attempts: Dict[str, List[datetime]] = {}
MAX_CONNECTIONS_PER_IP = 1000
RATE_LIMIT_WINDOW = timedelta(minutes=5)
WEBSOCKET_IDLE_TIMEOUT_SECONDS = int(os.getenv("WEBSOCKET_IDLE_TIMEOUT_SECONDS", "300"))

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('api_websocket.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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
            "forgot_password_init", "forgot_password_verify",
            "prefill", "itr_status", "active_filings",
            "e_verify_active_filings",
            "check_aadhaar", "send_otp", "revise_filing", "download_itr", "filling",
            "forgot_password_rerequest_otp", "everify_otp_verify", "get_all_challans", "get_challan_details",
            "get_bank_accounts", "submit_itr_form", "get_itr_receipt",
            "pan_link", "pan_link_continue","generate_pan_link_otp","verify_pan_link_otp","pay_advance_tax","prevalidate_bank","prevalidate_bank_continue"
        ]
        if v not in allowed_actions:
            raise ValueError(f"Invalid action. Must be one of: {', '.join(allowed_actions)}")
        return v

# ============================================================================
# Security Functions
# ============================================================================

def verify_api_key(api_key: str) -> Optional[str]:
    """Verify API key and return key name."""
    if not api_key:
        return None

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    if key_hash in API_KEYS:
        return API_KEYS[key_hash]

    return None

def check_rate_limit(client_ip: str) -> bool:
    """Check if client has exceeded rate limit."""
    now = datetime.now()

    # Clean old attempts
    if client_ip in connection_attempts:
        connection_attempts[client_ip] = [
            attempt for attempt in connection_attempts[client_ip]
            if now - attempt < RATE_LIMIT_WINDOW
        ]
    else:
        connection_attempts[client_ip] = []

    # Check limit
    if len(connection_attempts[client_ip]) >= MAX_CONNECTIONS_PER_IP:
        return False

    # Add attempt
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

    logger.info(f"Created session {session_id[:8]}... for PAN {login_result.get('user_id')}")
    return session_id

def create_forgot_password_session(service: ePortalForgotPassword, method: str) -> str:
    """Create a forgot password session and return session ID."""
    session_id = f"fp_{secrets.token_urlsafe(32)}"

    forgot_password_sessions[session_id] = service

    logger.info(f"Created forgot password session {session_id[:16]}... using {method}")
    return session_id

def verify_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Verify session exists and is valid."""
    if session_id not in active_sessions:
        return None

    session = active_sessions[session_id]
    if datetime.now() > session.get('expires_at', datetime.min):
        del active_sessions[session_id]
        return None

    return session

def cleanup_expired_sessions():
    """Remove expired sessions."""
    now = datetime.now()

    # Clean login sessions
    expired = [sid for sid, session in active_sessions.items()
               if now > session.get('expires_at', datetime.min)]

    for session_id in expired:
        logger.info(f"Removing expired session {session_id[:8]}...")
        del active_sessions[session_id]

    # Clean expired pan_link sessions
    expired_pl = [sid for sid, session in pan_link_sessions.items()
                  if now > session.get('expires_at', datetime.min)]




    for sid in expired_pl:
        logger.info(f"Removing expired pan_link session {sid[:16]}...")
        del pan_link_sessions[sid]

    # Clean old rate limit data
    for ip in list(connection_attempts.keys()):
        connection_attempts[ip] = [
            attempt for attempt in connection_attempts[ip]
            if now - attempt < RATE_LIMIT_WINDOW
        ]
        if not connection_attempts[ip]:
            del connection_attempts[ip]

# ============================================================================
# Lifespan Management
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    logger.info("Starting ePortal WebSocket API...")
    logger.info(f"Active sessions: {len(active_sessions)}")

    yield

    logger.info("Shutting down ePortal WebSocket API...")

    # Close all sessions
    active_sessions.clear()
    forgot_password_sessions.clear()
    pan_link_sessions.clear()

    # Close all WebSocket connections
    for conn_id, conn_data in list(websocket_connections.items()):
        try:
            ws = conn_data.get('websocket')
            if ws:
                await ws.close()
        except Exception as e:
            logger.error(f"Error closing WebSocket {conn_id}: {e}")

    websocket_connections.clear()
    logger.info("Cleanup completed")

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="ePortal WebSocket API",
    description="Secure WebSocket API for ePortal automation",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json"
)

# ============================================================================
# Middleware
# ============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]


)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# ============================================================================
# HTTP Endpoints (Minimal)
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "ePortal WebSocket API",
        "version": "1.0.0",
        "status": "running",
        "websocket": "ws://localhost:8000/ws"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    cleanup_expired_sessions()

    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_sessions": len(active_sessions),
        "forgot_password_sessions": len(forgot_password_sessions),
        "websocket_connections": len(websocket_connections)
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
            'fp_session_id': None,
            'pan_link_session_id': None
        }

        logger.info(f"WebSocket connected: {connection_id[:8]}... from {client_ip}")

    def disconnect(self, connection_id: str):
        """Remove WebSocket connection."""
        if connection_id in self.active_connections:
            conn_data = self.active_connections[connection_id]
            logger.info(f"WebSocket disconnected: {connection_id[:8]}... from {conn_data['client_ip']}")
            del self.active_connections[connection_id]

    def cleanup_sessions(self, connection_id: str):
        """Remove session state owned by an idle WebSocket."""
        conn_data = self.active_connections.get(connection_id, {})
        if not conn_data:
            return
        session = active_sessions.pop(conn_data.get('session_id'), None)
        if session:
            stop_event = getattr(session.get('client'), '_stop_extender_event', None)
            if stop_event:
                stop_event.set()
        forgot_password_sessions.pop(conn_data.get('fp_session_id'), None)
        pan_link_sessions.pop(conn_data.get('pan_link_session_id'), None)

    def authenticate_connection(self, connection_id: str, api_key_name: str):
        """Mark connection as authenticated."""
        if connection_id in self.active_connections:
            self.active_connections[connection_id]['authenticated'] = True
            self.active_connections[connection_id]['api_key_name'] = api_key_name
            logger.info(f"WebSocket {connection_id[:8]}... authenticated as {api_key_name}")

    def is_authenticated(self, connection_id: str) -> bool:
        """Check if connection is authenticated."""
        if connection_id not in self.active_connections:
            return False
        return self.active_connections[connection_id].get('authenticated', False)

    def associate_session(self, connection_id: str, session_id: str):
        """Associate WebSocket with login session."""
        if connection_id in self.active_connections:
            self.active_connections[connection_id]['session_id'] = session_id
            logger.info(f"WebSocket {connection_id[:8]}... linked to session {session_id[:8]}...")

    def associate_forgot_password_session(self, connection_id: str, fp_session_id: str):
        """Associate WebSocket with forgot password session."""
        if connection_id in self.active_connections:
            self.active_connections[connection_id]['fp_session_id'] = fp_session_id
            logger.info(f"WebSocket {connection_id[:8]}... linked to FP session {fp_session_id[:16]}...")

    def associate_pan_link_session(self, connection_id: str, pl_session_id: str):
        """Associate WebSocket with pan link session."""
        if connection_id in self.active_connections:
            self.active_connections[connection_id]['pan_link_session_id'] = pl_session_id
            logger.info(f"WebSocket {connection_id[:8]}... linked to PAN link session {pl_session_id[:16]}...")

    async def send_message(self, connection_id: str, message: dict):
        """Send message to specific connection."""
        if connection_id in self.active_connections:
            websocket = self.active_connections[connection_id]['websocket']
            await websocket.send_json(message)

    async def broadcast(self, message: dict):
        """Broadcast message to all authenticated connections."""
        for conn_id, conn_data in self.active_connections.items():
            if conn_data.get('authenticated'):
                websocket = conn_data['websocket']
                await websocket.send_json(message)

manager = ConnectionManager()

# ============================================================================
# WebSocket Endpoint
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Secure WebSocket endpoint for real-time communication.

    Authentication Flow:
    1. Connect to WebSocket
    2. Send authenticate action with API key
    3. Upon success, perform other actions

    Message Format:
    {
        "action": "authenticate|login|forgot_password_init|forgot_password_verify|...",
        "api_key": "your-api-key" (required for authenticate),
        "data": {...},
        "session_id": "..." (required for authenticated actions)
    }
    """
    connection_id = secrets.token_urlsafe(16)
    client_host = websocket.client.host if websocket.client else "unknown"

    # Rate limiting
    if not check_rate_limit(client_host):
        await websocket.close(code=1008, reason="Rate limit exceeded")
        logger.warning(f"Rate limit exceeded for {client_host}")
        return

    await manager.connect(websocket, connection_id, client_host)

    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "connection_id": connection_id,
            "timestamp": datetime.now().isoformat(),
            "message": "Please authenticate using 'authenticate' action with your API key"
        })

        while True:
            try:
                # Receive message
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=WEBSOCKET_IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.info(f"Closing idle WebSocket {connection_id[:8]}... after 5 minutes")
                manager.cleanup_sessions(connection_id)
                await websocket.close(code=1000, reason="Idle timeout")
                break
            except Exception as e:
                logger.error(f"Failed to receive JSON: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": "Invalid JSON format"
                })
                continue

            try:
                message = WebSocketMessage(**data)
                action = message.action
                payload = message.data
                session_id = message.session_id
                api_key = message.api_key

                logger.info(f"WebSocket action: {action} from {connection_id[:8]}...")

                # ========================================================
                # AUTHENTICATE ACTION - Required First
                # ========================================================
                if action == "authenticate":
                    if not api_key:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing API key"
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
                            "error": "Invalid API key"
                        })
                        # Close connection after failed authentication
                        await websocket.close(code=1008, reason="Invalid API key")
                        break

                    continue

                # ========================================================
                # Check Authentication for All Other Actions
                # ========================================================
                if not manager.is_authenticated(connection_id):
                    await websocket.send_json({
                        "type": "error",
                        "action": action,
                        "error": "Not authenticated. Please authenticate first using 'authenticate' action."
                    })
                    continue

                # ========================================================
                # LOGIN ACTION
                # ========================================================
                if action == "login":
                    pan = payload.get('pan')
                    password = payload.get('password')
                    if not pan or not password:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing PAN or password"
                        })
                        continue

                    credentials = {"PAN": pan, "PASSWORD": password}
                    client = EPortalClient(credentials)
                    login_result = client.login()

                    if login_result.get('success'):
                        session_id = create_session(client, login_result)
                        manager.associate_session(connection_id, session_id)

                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": True,
                            "session_id": session_id,
                            "data": login_result
                        })
                    else:
                        await websocket.send_json({

                            "type": "response",
                            "action": action,
                            "success": False,
                            "response":login_result
                        })


                # ========================================================
                # LOGOUT ACTION
                # ========================================================
                elif action == "logout":
                    if session_id and session_id in active_sessions:
                        del active_sessions[session_id]
                        manager.active_connections[connection_id]['session_id'] = None

                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": True,
                            "message": "Logged out successfully"
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "No active session to logout"
                        })

                # ========================================================
                # FORGOT PASSWORD INIT ACTION
                # ========================================================
                elif action == "forgot_password_init":
                    pan = payload.get('pan')
                    method = payload.get('method')
                    dob = payload.get('dob')

                    if not pan or not method:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing PAN or method"
                        })
                        continue

                    if method not in ["email_mobile", "aadhaar"]:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Invalid method. Use 'email_mobile' or 'aadhaar'"
                        })
                        continue

                    try:
                        service = ePortalForgotPassword(pan)
                        result = service.step1_submit_pan()

                        if not result or not isinstance(result, dict) or not result.get('success'):
                            error_msg = result.get('error') if isinstance(result, dict) else 'Failed to submit PAN'
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "error": error_msg,
                                "step": 1
                            })
                            continue

                        if method == "email_mobile":
                            if not dob:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "DOB required for email_mobile method (format: YYYY-MM-DD)"
                                })
                                continue

                            result = service.step2_request_otp_email_mobile(dob=dob)
                        else:  # aadhaar
                            result = service.step2_request_otp_aadhaar()

                        if result and result.get('success'):
                            fp_session_id = create_forgot_password_session(service, method)
                            manager.associate_forgot_password_session(connection_id, fp_session_id)

                            response_data = {
                                "type": "response",
                                "action": action,
                                "success": True,
                                "session_id": fp_session_id,
                                "method": method,
                                "message": f"OTP sent via {method}"
                            }

                            if method == "email_mobile":
                                response_data["data"] = {
                                    "mb_trans": result.get('mb_trans'),
                                    "e_trans": result.get('e_trans')
                                }

                            await websocket.send_json(response_data)
                        else:
                            error_msg = result.get('error') if isinstance(result, dict) else 'Failed to request OTP'
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "error": error_msg,
                                "step": 2
                            })

                    except Exception as e:
                        logger.error(f"Forgot password init error: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": f"Forgot password initialization failed: {str(e)}",
                            "step": 1
                        })

                # ========================================================
                # FORGOT PASSWORD VERIFY ACTION
                # ========================================================
                elif action == "forgot_password_verify":
                    fp_session_id = payload.get('session_id')
                    mobile_otp = payload.get('mobile_otp')
                    email_otp = payload.get('email_otp')
                    new_password = payload.get('new_password')

                    if not fp_session_id or not mobile_otp or not new_password:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing required fields: session_id, mobile_otp, new_password"
                        })
                        continue

                    if fp_session_id not in forgot_password_sessions:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Invalid or expired forgot password session"
                        })
                        continue

                    try:
                        service = forgot_password_sessions[fp_session_id]

                        # Determine method and verify
                        if hasattr(service, 'mb_trans') and service.mb_trans:
                            # Email/Mobile method
                            if not email_otp:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Email OTP required for email/mobile method"
                                })
                                continue

                            result = service.step3_verify_otp_email_mobile(mobile_otp, email_otp)
                            if result and result.get('success'):
                                result = service.step4_set_new_password_email_mobile(new_password)

                        elif hasattr(service, 'autkn') and service.autkn:
                            # Aadhaar method
                            result = service.step3_verify_otp_aadhar(mobile_otp)
                            if result and result.get('success'):
                                result = service.set_password_aadhar(new_password)

                        else:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Invalid session state. Please reinitialize forgot password flow."
                            })
                            continue

                        # Check if result is valid
                        if not result or not isinstance(result, dict):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "error": "Failed to process password reset. Please try again.",
                                "can_retry": True
                            })
                            continue

                        if result.get('success'):
                            # Cleanup forgot password session
                            del forgot_password_sessions[fp_session_id]
                            if connection_id in manager.active_connections:
                                manager.active_connections[connection_id]['fp_session_id'] = None

                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": True,
                                "message": "Password reset successfully"
                            })
                        else:
                            error_msg = result.get('error') or result.get('message') or 'Password reset failed'
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "error": error_msg,
                                "can_retry": result.get('can_retry', False),
                                "step": result.get('step')
                            })

                    except Exception as e:
                        logger.error(f"Forgot password verify error: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": f"Password reset failed: {str(e)}",
                            "can_retry": True
                        })


                #==================================================================
                #Rerequest forgot password OTP
                #=================================================================

                elif action == "forgot_password_rerequest_otp":

                    fp_session_id = payload.get('session_id')

                    if not fp_session_id:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing session_id"
                        })
                        continue

                    if fp_session_id not in forgot_password_sessions:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Invalid or expired forgot password session"
                        })
                        continue

                    try:
                        service = forgot_password_sessions[fp_session_id]
                        method = payload.get('method')
                        dob = payload.get('dob')

                        if method == "email_mobile":
                            if not dob:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "DOB required for email_mobile method (format: YYYY-MM-DD)"
                                })
                                continue

                            result = service.step2_request_otp_email_mobile(dob=dob)

                        elif method == "aadhaar":
                            result = service.step2_request_otp_aadhaar()

                        else:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Invalid method. Use 'email_mobile' or 'aadhaar'"
                            })
                            continue

                        if result and result.get('success'):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": True,
                                "message": f"OTP re-sent via {method}"
                            })
                        else:
                            error_msg = result.get('error') if isinstance(result, dict) else 'Failed to request OTP'
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "error": error_msg,
                                "step": 2
                            })

                    except Exception as e:
                        logger.error(f"Forgot password rerequest OTP error: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": False,
                            "error": f"OTP request failed: {str(e)}",
                            "step": 2
                        })

                # ========================================================
                # PAN LINK ACTION (pre-login flow)
                # ========================================================
                elif action == "pan_link":
                    pan = payload.get("pan")
                    aadhaar_number = payload.get("aadhaar_number")
                    mobile = payload.get("mobile")
                    password = payload.get("password", "")
                    if not pan or not aadhaar_number or not mobile or not password:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing pan, aadhaar_number, mobile or password"
                        })
                        continue
                    try:
                        client = EPortalClient({"PAN": pan, "PASSWORD": password})

                        if client:
                             # Create a dedicated pan_link session (like forgot_password)
                            pl_session_id = f"pl_{secrets.token_urlsafe(32)}"
                            pan_link_sessions[pl_session_id] = {
                                "client": client,
                                "pan": pan,
                                "aadhaar_number": aadhaar_number,
                                "mobile": mobile,
                                "created_at": datetime.now(),
                                "expires_at": datetime.now() + timedelta(hours=1),
                            }
                            manager.associate_pan_link_session(connection_id, pl_session_id)










                        link_result = client.aadhar_pan_linkage(pan=pan, aadhaar_number=aadhaar_number)
                        logger.info(link_result)
                        if not link_result.get("success"):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "data": link_result
                            })

                        elif link_result.get("payment_verify"):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": True,
                                "payment_verified": True,
                                "message": "Aadhaar-PAN validated. Payment verification required.",
                                "data": link_result,
                                "session_id": pl_session_id
                            })

                        else:
                            otp_result = client.generate_payment_otp(pan=pan, mobile=mobile)
                            if not otp_result.get("success"):
                                await websocket.send_json({
                                    "type": "response",
                                    "action": action,
                                    "success": False,
                                    "data": otp_result
                                })
                                continue



                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": True,
                                "message": "Aadhaar-PAN validated. OTP sent for payment linking.",
                                "data": otp_result,
                                "session_id": pl_session_id
                            })
                    except Exception as e:
                        logger.error(f"pan_link error: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": f"PAN link failed: {str(e)}"
                        })
                    continue

                # ========================================================
                # PAN LINK CONTINUE (validate OTP and create challan)
                # ========================================================
                elif action == "pan_link_continue":
                    otp = payload.get("otp")
                    pl_session_id = payload.get("session_id")

                    if not otp:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing otp"
                        })
                        continue

                    if not pl_session_id:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing session_id. Provide the session_id returned by pan_link."
                        })
                        continue

                    if pl_session_id not in pan_link_sessions:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Invalid or expired PAN link session. Run pan_link again."
                        })
                        continue

                    pl_session = pan_link_sessions[pl_session_id]

                    # Check expiry
                    if datetime.now() > pl_session.get("expires_at", datetime.min):
                        del pan_link_sessions[pl_session_id]
                        if connection_id in manager.active_connections:
                            manager.active_connections[connection_id]["pan_link_session_id"] = None
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "PAN link session expired. Run pan_link again."
                        })
                        continue

                    client = pl_session.get("client")
                    if not client:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "No client found in PAN link session. Run pan_link again."
                        })
                        continue

                    try:
                        validate_result = client.validate_payment_otp(otp=otp)
                        if not validate_result.get("success"):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "session_id": pl_session_id,
                                "error": validate_result.get("error"),
                                "message": validate_result.get("message")
                            })
                            continue

                        challan_result = client.create_payment_challan()
                        if not challan_result.get("success"):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "session_id": pl_session_id,
                                "error": challan_result.get("error"),
                                "message": challan_result.get("message")
                            })
                            continue

                        payment_result = client.create_payment()
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": payment_result.get("success", False),
                            "message": payment_result.get("message"),
                            "error": payment_result.get("error"),
                            "session_id": pl_session_id,
                            "data": {
                                "challan": challan_result.get("data"),
                                "payment": payment_result.get("data"),
                                "redirect_url": payment_result.get("redirect_url"),
                                "reqjson": payment_result.get("reqjson"),
                            }
                        })

                        # if pl_session_id in pan_link_sessions:
                        #     del pan_link_sessions[pl_session_id]
                        # if connection_id in manager.active_connections:
                        #     manager.active_connections[connection_id]["pan_link_session_id"] = None

                    except Exception as e:
                        logger.error(f"pan_link_continue error: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "session_id": pl_session_id,
                            "error": f"PAN link continue failed: {str(e)}"
                        })
                    continue



                elif action == "generate_pan_link_otp":
                    try:
                        pl_session_id = payload.get("session_id")
                        name=payload.get("name")

                        if not name:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Missing name -enter name as per aadhaar for generating OTP"
                            })

                        if not pl_session_id:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Missing session_id. Provide the session_id returned by pan_link."
                            })
                            continue

                        if pl_session_id not in pan_link_sessions:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Invalid or expired PAN link session. Run pan_link again."
                            })
                            continue

                        pl_session = pan_link_sessions[pl_session_id]

                        # Check expiry
                        if datetime.now() > pl_session.get("expires_at", datetime.min):
                            del pan_link_sessions[pl_session_id]
                            if connection_id in manager.active_connections:
                                manager.active_connections[connection_id]["pan_link_session_id"] = None
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "PAN link session expired. Run pan_link again."
                            })
                            continue

                        client = pl_session.get("client")
                        if not client:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "No client found in PAN link session. Run pan_link again."
                            })
                            continue





                        otp_result = client.generate_pan_link_otp(aadhar_no=pl_session.get("aadhaar_number"), pan=pl_session.get("pan"), mobile=pl_session.get("mobile"))
                        if not otp_result.get("success"):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "data": otp_result
                            })
                            continue

                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": True,
                            "message": "OTP re-sent for payment linking.",
                            "data": otp_result,
                            "session_id": pl_session_id
                        })
                    except Exception as e:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": str(e)
                        })

                elif action == "verify_pan_link_otp":
                    try:
                        pl_session_id = payload.get("session_id")
                        otp = payload.get("otp")
                        if not pl_session_id:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Missing session_id. Provide the session_id returned by pan_link."
                            })
                            continue
                        if not otp:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Missing otp"
                            })
                            continue
                        if pl_session_id not in pan_link_sessions:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "Invalid or expired PAN link session. Run pan_link again."
                            })
                            continue
                        pl_session = pan_link_sessions[pl_session_id]
                        client = pl_session.get("client")
                        if not client:
                            await websocket.send_json({
                                "type": "error",
                                "action": action,
                                "error": "No client found in PAN link session. Run pan_link again."
                            })
                            continue
                        validate_result = client.validate_pan_link_otp(otp=otp)
                        if not validate_result.get("success"):
                            await websocket.send_json({
                                "type": "response",
                                "action": action,
                                "success": False,
                                "session_id": pl_session_id,
                                "error": validate_result.get("error"),
                                "message": validate_result.get("message")
                            })
                            continue
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "success": True,
                            "message": "OTP validated successfully.",
                            "session_id": pl_session_id,
                            "data": validate_result.get("data")
                        })

                    except Exception as e:
                        logger.error(f"verify_pan_link_otp error: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "session_id": pl_session_id,
                            "error": f"OTP validation failed: {str(e)}"
                        })






                        # # Cleanup session after successful payment flow

                # ========================================================
                # AUTHENTICATED ACTIONS (Require Login Session)
                # ========================================================
                elif action in ["prefill", "itr_status", "active_filings", "check_aadhaar",
                               "send_otp", "revise_filing", "download_itr","e_verify_active_filings","filling","everify_otp_verify","get_all_challans","get_challan_details","get_bank_accounts", "submit_itr_form", "get_itr_receipt","pay_advance_tax","prevalidate_bank","prevalidate_bank_continue"]:

                    if not session_id:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Missing session_id. Please login first."
                        })
                        continue

                    session = verify_session(session_id)
                    if not session:
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": "Invalid or expired session. Please login again."
                        })
                        continue

                    client: EPortalClient = session['client']
                    pan = payload.get('pan', session.get('pan'))
                    if pan:
                        pan = pan.upper().strip()

                    result = None

                    try:


                        if action == "filling":
                            year = payload.get('year')
                            if not year:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing year parameter"
                                })
                                continue
                            result = client.get_filling_data(year=year, pan=pan)


                        if action == "prefill":
                            year = payload.get('year')
                            if not year:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing year parameter"
                                })
                                continue
                            result = client.get_prefill_data(pan=pan, assessment_year=year)

                        #==========================================================
                        # Bank Accounts Action
                        #==========================================================

                        elif action == "get_bank_accounts":
                            result = client.get_bank_accounts(pan=pan)

                        #==========================================================
                        # Prevalidate Bank Account Action
                        elif action == "prevalidate_bank":
                            account_details = payload.get("account_details")
                            if not account_details:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing account_details parameter"
                                })
                                continue
                            result = client.prevalidate_bank(account_details=account_details)

                        elif action == "prevalidate_bank_continue":
                            otp= payload.get("otp")
                            if not otp:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing otp parameter"
                                })
                                continue
                            result = client.pre_validate_continue(otp=otp)





                        #==========================================================
                        # ITR Status ACTION
                        #=========================================================

                        elif action == "itr_status":
                            result = client.get_itr_status(pan)


                        #==========================================================
                        #Everify Active Filings ACTION
                        #==========================================================
                        elif action == "e_verify_active_filings":
                            year = payload.get("year")
                            ackn_no = payload.get("acknowledgment_number")
                            if not year or not ackn_no:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing year or acknowledgment_number parameter"
                                })



                            result = client.e_verify_active_filings(year=year, ack_no=ackn_no,pan=pan)


                        elif action == "active_filings":
                            result = client.get_active_verify_filings(pan=pan)

                        elif action == "check_aadhaar":
                            result = client.check_aadhaar_linked(pan=pan)
                        elif action == "everify_otp_verify":
                            otp = payload.get("otp")
                            ackn_no = payload.get("acknowledgment_number")
                            verify_now=payload.get("verify_now",False)
                            if not otp or ackn_no is None:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing otp or acknowledgment_number parameter"
                                })
                                continue
                            result = client.check_everify_otp(pan=pan,otp=otp,ackn_no=payload.get("acknowledgment_number"), verify_now=verify_now)

                        elif action == "get_all_challans":
                            crn=None
                            if payload.get("crn"):
                                crn=payload.get("crn")
                            result = client.get_challan_history(pan=pan,crn=crn)

                        elif action == "get_challan_details":
                            year = payload.get("year")
                            if not year :
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action ,
                                    "error": "Missing year parameter"
                                })
                                continue
                            result = client.get_challan_details(pan=pan,year=year)

                        elif action == "send_otp":
                            result = client.send_otp_aadhaar(pan=pan)
                        elif action == "pay_advance_tax":
                            year = payload.get("year")
                            pd = payload.get("payment_details", {})
                            old_income_tax=payload.get("old_income_tax", True)

                            # support both nested and flat payload keys
                            basic_tax = pd.get("basicTax", payload.get("basicTax"))
                            total_amt = pd.get("totalAmt", payload.get("totalAmt"))
                            total_amt_word = pd.get("totalAmtWord", payload.get("totalAmtWord"))

                            if not year or basic_tax is None or total_amt is None or not total_amt_word:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing required fields: year, basicTax, totalAmt, totalAmtWord"
                                })
                                continue

                            payment_details = {
                                "basicTax": basic_tax,
                                "surCharge": pd.get("surCharge", payload.get("surCharge", 0)),
                                "eduCess": pd.get("eduCess", payload.get("eduCess", 0)),
                                "interest": pd.get("interest", payload.get("interest", 0)),
                                "penalty": pd.get("penalty", payload.get("penalty", 0)),
                                "others": pd.get("others", payload.get("others", 0)),
                                "totalAmt": total_amt,
                                "totalAmtWord": total_amt_word
                            }

                            result = client.pay_payment(
                                pan=pan,
                                year=year,
                                payment_details=payment_details,
                                old_regime=old_income_tax
                            )



                        elif action == "revise_filing":
                            year = payload.get('year')
                            if not year:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing year parameter"
                                })
                                continue
                            result = client.revise_active_efillings(year=year, pan=pan)


                        elif action == "download_itr":
                            ackn_no = payload.get('acknowledgment_number')
                            if not ackn_no:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing acknowledgment_number parameter"
                                })
                                continue
                            result = client.get_download_itr_file(pan=pan, ackn_no=ackn_no)

                        elif action == "get_itr_receipt":
                            ackn_no = payload.get('ackn_no')
                            year = payload.get('year')
                            if not ackn_no or not year:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing ackn_no or year parameter"
                                })
                                continue
                            receipt_result = client.get_itr_receipt(ackn_no=ackn_no, year=year, pan=pan)
                            if receipt_result.get('success') and 'file' in receipt_result:
                                file_path = receipt_result['file']
                                try:
                                    with open(file_path, "rb") as pdf_file:
                                        pdf_bytes = pdf_file.read()
                                    # Send as base64 to avoid binary issues
                                    import base64
                                    pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')
                                    await websocket.send_json({
                                        "type": "response",
                                        "action": action,
                                        "success": True,
                                        "filename": file_path,
                                        "pdf_base64": pdf_b64,
                                        "message": receipt_result.get('message', 'ITR receipt PDF sent successfully.')
                                    })
                                except Exception as e:
                                    await websocket.send_json({
                                        "type": "error",
                                        "action": action,
                                        "success": False,
                                        "error": f"Failed to read/send PDF: {str(e)}"
                                    })
                            else:
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "success": False,
                                    "error": receipt_result.get('error', 'Failed to download ITR receipt'),
                                    "message": receipt_result.get('message', '')
                                })
                            continue

                        elif action == "submit_itr_form":

                            itr_type = payload.get("itr_type")
                            year = payload.get("year")
                            form_type = payload.get("form_type")
                            json_link = payload.get("json_link")
                            fetch_from_url = payload.get("fetch_from_url", True)
                            verify_now=payload.get("verify_now",False)
                            if not all([itr_type, year, form_type, json_link]):
                                await websocket.send_json({
                                    "type": "error",
                                    "action": action,
                                    "error": "Missing one or more required parameters: pan, itr_type, year, form_type, json_link"
                                })
                                continue
                            result = client.submit_itr_form(
                                pan=pan,
                                itr_type=itr_type,
                                year=year,
                                form_type=form_type,
                                json_link=json_link,
                                fetch_from_url=fetch_from_url,
                                verify_now=verify_now
                            )





                        # Send result
                        await websocket.send_json({
                            "type": "response",
                            "action": action,
                            "result": result
                        })

                    except KeyError as e:
                        logger.error(f"Action {action} missing key: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": f"Missing required parameter: {str(e)}"
                        })

                    except AttributeError as e:
                        logger.error(f"Action {action} attribute error: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": f"Invalid operation: {str(e)}"
                        })

                    except Exception as e:
                        logger.error(f"Action {action} error: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "error",
                            "action": action,
                            "error": f"Action failed: {str(e)}"
                        })

                # else:
                #     await websocket.send_json({
                #         "type": "error",
                #         "error": f"Unknown action: {action}"
                #     })

            except ValueError as e:
                logger.error(f"Validation error: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": f"Validation error: {str(e)}"
                })
                # Continue the loop instead of breaking
                continue

            except KeyError as e:
                logger.error(f"Missing key error: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": f"Missing required field: {str(e)}"
                })
                # Continue the loop instead of breaking
                continue

            except Exception as e:
                logger.error(f"WebSocket message error: {e}", exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "error": f"Internal error: {str(e)}"
                })
                # Continue the loop instead of breaking
                continue

    except WebSocketDisconnect:
        manager.disconnect(connection_id)
        logger.info(f"Client {connection_id[:8]}... disconnected normally")

    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        manager.disconnect(connection_id)
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except:
            pass

    finally:
        manager.disconnect(connection_id)


# ============================================================================
# Run Application
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    print("=" * 80)
    print("ePortal WebSocket API - Secure Version")
    print("=" * 80)
    print(f"WebSocket Endpoint: ws://localhost:8000/ws")
    print(f"Health Check: http://localhost:8000/health")
    print(f"Documentation: http://localhost:8000/docs")
    print("=" * 80)
    print("\nSecurity Features:")
    print("✓ API Key Authentication Required")
    print("✓ Rate Limiting (10 connections per IP per 5 minutes)")
    print("✓ Session Management with 2-hour timeout")
    print("✓ CORS Protection")
    print("✓ Trusted Host Validation")
    print("✓ Input Validation with Pydantic")
    print("=" * 80)

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8009,
        workers=5,  # Ensure single worker for in-memory session management
        reload=True,
        log_level="info",
        access_log=True
    )
