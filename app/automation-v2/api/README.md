# ePortal FastAPI with WebSocket

Production-ready FastAPI application with WebSocket support for ePortal automation.

## 🔐 Security Features

- **API Key Authentication**: Header-based API key validation
- **Bearer Token Authentication**: Session-based bearer token for API endpoints
- **Session Management**: 2-hour session timeout with automatic cleanup
- **CORS Protection**: Configurable allowed origins
- **Trusted Host Middleware**: Prevents host header attacks
- **Password Hashing**: SHA-256 hashing for API keys
- **Rate Limiting Ready**: Can be extended with rate limiting
- **Input Validation**: Pydantic models with validators
- **Error Handling**: Comprehensive exception handling

## 📦 Installation

```powershell
# Install dependencies
pip install -r requirements.txt
```

## 🚀 Quick Start

### 1. Update API Keys (IMPORTANT!)

Before running, update the API keys in `eportal_api.py`:

```python
API_KEYS = {
    "admin": hashlib.sha256("your-admin-key-here".encode()).hexdigest(),
    "client": hashlib.sha256("your-client-key-here".encode()).hexdigest(),
}
```

Generate secure keys:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2. Start the Server

```powershell
# Run with uvicorn
python api/eportal_api.py

# Or use uvicorn directly
uvicorn api.eportal_api:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Access API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## 📡 API Endpoints

### Authentication

#### POST `/api/v1/login`
Login to ePortal and create a session.

**Headers:**
- `X-API-Key`: Your API key

**Request:**
```json
{
  "pan": "ABCDE1234F",
  "password": "your-password-here"
}
```

**Response:**
```json
{
  "success": true,
  "session_id": "abc123...",
  "message": "Login successful",
  "data": {
    "req_id": "123456",
    "user_id": "ABCDE1234F",
    "role": "IND",
    "expires_at": "2025-11-20T12:00:00"
  }
}
```

### Data Retrieval

All endpoints require:
- `X-API-Key` header
- `Authorization: Bearer <session_id>` header

#### POST `/api/v1/prefill`
Get prefill data for assessment year.

**Request:**
```json
{
  "session_id": "abc123...",
  "pan": "ABCDE1234F",
  "year": 2025
}
```

#### POST `/api/v1/itr-status`
Get ITR filing status.

#### POST `/api/v1/active-filings`
Get active e-verification filings.

#### POST `/api/v1/check-aadhaar`
Check if Aadhaar is linked to PAN.

#### POST `/api/v1/send-otp`
Send OTP to Aadhaar-linked mobile.

#### POST `/api/v1/download-itr`
Download ITR file by acknowledgment number.

**Request:**
```json
{
  "session_id": "abc123...",
  "pan": "ABCDE1234F",
  "acknowledgment_number": "123456789"
}
```

#### POST `/api/v1/revise-filing`
Validate revised return.

**Request:**
```json
{
  "session_id": "abc123...",
  "pan": "ABCDE1234F",
  "year": 2025
}
```

#### DELETE `/api/v1/logout`
Logout and destroy session.

**Query Parameters:**
- `session_id`: Session ID to logout

### Health Check

#### GET `/health`
Check API health and status.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-11-20T10:00:00",
  "active_sessions": 5,
  "websocket_connections": 2
}
```

## 🔌 WebSocket Connection

### Connection URL
```
ws://localhost:8000/ws
```

### Message Format

**Client → Server:**
```json
{
  "action": "login|prefill|itr_status|active_filings|check_aadhaar|send_otp",
  "data": {...},
  "session_id": "..."
}
```

**Server → Client:**
```json
{
  "type": "response|error|connection",
  "action": "...",
  "success": true,
  "result": {...}
}
```

### Actions

#### Login
```json
{
  "action": "login",
  "data": {
    "pan": "ABCDE1234F",
    "password": "your-password-here"
  }
}
```

**Response:**
```json
{
  "type": "response",
  "action": "login",
  "success": true,
  "session_id": "abc123...",
  "data": {
    "req_id": "123456",
    "user_id": "ABCDE1234F",
    "role": "IND"
  }
}
```

#### Get Prefill Data
```json
{
  "action": "prefill",
  "session_id": "abc123...",
  "data": {
    "year": 2025,
    "pan": "ABCDE1234F"
  }
}
```

#### Get ITR Status
```json
{
  "action": "itr_status",
  "session_id": "abc123...",
  "data": {
    "pan": "ABCDE1234F"
  }
}
```

#### Get Active Filings
```json
{
  "action": "active_filings",
  "session_id": "abc123...",
  "data": {
    "pan": "ABCDE1234F"
  }
}
```

#### Check Aadhaar
```json
{
  "action": "check_aadhaar",
  "session_id": "abc123...",
  "data": {
    "pan": "ABCDE1234F"
  }
}
```

## 💻 Client Examples

### HTTP Client (Python)

```python
from api.http_client_example import EPortalHTTPClient

client = EPortalHTTPClient(api_key="your-client-key-here")

# Login
result = client.login(pan="ABCDE1234F", password="your-password-here")

# Get data
prefill = client.get_prefill_data(year=2025, pan="ABCDE1234F")
filings = client.get_active_filings(pan="ABCDE1234F")

# Logout
client.logout()
```

### WebSocket Client (Python)

```python
from api.websocket_client_example import EPortalWebSocketClient
import asyncio

async def main():
    client = EPortalWebSocketClient()
    await client.connect()

    # Login
    await client.login(pan="ABCDE1234F", password="your-password-here")

    # Get data
    await client.get_prefill_data(year=2025, pan="ABCDE1234F")
    await client.get_active_filings(pan="ABCDE1234F")

    await client.close()

asyncio.run(main())
```

### JavaScript WebSocket Client

```javascript
const ws = new WebSocket('ws://localhost:8000/ws');

ws.onopen = () => {
  // Login
  ws.send(JSON.stringify({
    action: 'login',
    data: {
      pan: 'ABCDE1234F',
      password: 'your-password-here'
    }
  }));
};

ws.onmessage = (event) => {
  const response = JSON.parse(event.data);
  console.log('Received:', response);

  if (response.type === 'response' && response.action === 'login' && response.success) {
    const sessionId = response.session_id;

    // Get prefill data
    ws.send(JSON.stringify({
      action: 'prefill',
      session_id: sessionId,
      data: {
        year: 2025,
        pan: 'ABCDE1234F'
      }
    }));
  }
};
```

## 🔒 Production Deployment

### Environment Variables

Create `.env` file:

```env
# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
SECRET_KEY=your-secret-key-here

# API Keys (SHA-256 hashed)
ADMIN_API_KEY_HASH=...
CLIENT_API_KEY_HASH=...

# CORS
ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com

# Session
SESSION_TIMEOUT_HOURS=2

# Logging
LOG_LEVEL=INFO
LOG_FILE=api_eportal.log
```

### Security Checklist

- [ ] Change default API keys
- [ ] Use HTTPS in production
- [ ] Configure proper CORS origins
- [ ] Set up rate limiting
- [ ] Enable logging and monitoring
- [ ] Use environment variables for secrets
- [ ] Set up firewall rules
- [ ] Enable request validation
- [ ] Implement IP whitelisting (if needed)
- [ ] Regular security audits

### Run with Gunicorn (Production)

```powershell
# Install gunicorn
pip install gunicorn

# Run with workers
gunicorn api.eportal_api:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Docker Deployment

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api.eportal_api:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 📊 Logging

Logs are written to:
- `api_eportal.log` - API operations
- Console output - Real-time monitoring

## 🧪 Testing

Test with curl:

```bash
# Health check
curl http://localhost:8000/health

# Login
curl -X POST http://localhost:8000/api/v1/login \
  -H "X-API-Key: your-client-key-here" \
  -H "Content-Type: application/json" \
  -d '{"pan":"ABCDE1234F","password":"your-password-here"}'

# Get prefill data
curl -X POST http://localhost:8000/api/v1/prefill \
  -H "X-API-Key: your-client-key-here" \
  -H "Authorization: Bearer <session_id>" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<session_id>","pan":"ABCDE1234F","year":2025}'
```

## 📝 Notes

- Sessions expire after 2 hours of inactivity
- WebSocket connections are automatically cleaned up on disconnect
- All sensitive data should be transmitted over HTTPS in production
- API keys should be rotated regularly
- Monitor `api_eportal.log` for security events

## 🐛 Troubleshooting

### Connection Refused
- Ensure server is running: `python api/eportal_api.py`
- Check firewall settings
- Verify port 8000 is available

### Invalid API Key
- Generate new API key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- Hash it: `python -c "import hashlib; print(hashlib.sha256(b'your-key').hexdigest())"`
- Update `API_KEYS` in `eportal_api.py`

### Session Expired
- Login again to get new session ID
- Increase `SESSION_TIMEOUT_HOURS` if needed

### WebSocket Disconnects
- Check network stability
- Implement reconnection logic in client
- Monitor server logs for errors
