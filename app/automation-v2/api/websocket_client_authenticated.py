"""
Authenticated WebSocket Client Example for ePortal API
Shows how to authenticate and interact with the WebSocket endpoint.
"""

import asyncio
import websockets
import json
from datetime import datetime

class AuthenticatedEPortalClient:
    """WebSocket client with authentication."""

    def __init__(self, url: str = "ws://localhost:8000/ws", api_key: str = "your-client-key-here"):
        self.url = url
        self.api_key = api_key
        self.websocket = None
        self.session_id = None
        self.authenticated = False

    async def connect(self):
        """Connect to WebSocket server."""
        self.websocket = await websockets.connect(self.url)
        print(f"✓ Connected to {self.url}")

        # Receive welcome message
        welcome = await self.websocket.recv()
        print(f"Welcome: {welcome}\n")

    async def authenticate(self):
        """Authenticate with API key."""
        print("=" * 80)
        print("AUTHENTICATING")
        print("=" * 80)

        auth_message = {
            "action": "authenticate",
            "api_key": self.api_key
        }

        await self.websocket.send(json.dumps(auth_message))
        print(f"→ Sent authentication with API key")

        response = await self.websocket.recv()
        data = json.loads(response)

        print(f"\n← Received:")
        print(json.dumps(data, indent=2))

        if data.get("success"):
            self.authenticated = True
            print(f"\n✓ Authentication successful!")
            return True
        else:
            print(f"\n✗ Authentication failed: {data.get('error')}")
            return False

    async def send_message(self, action: str, data: dict = None, session_id: str = None):
        """Send message to server."""
        if not self.authenticated and action != "authenticate":
            print("✗ Not authenticated! Call authenticate() first.")
            return None

        message = {
            "action": action,
            "data": data or {}
        }

        if session_id:
            message["session_id"] = session_id
        elif self.session_id and action not in ["login", "forgot_password_init"]:
            message["session_id"] = self.session_id

        await self.websocket.send(json.dumps(message))
        print(f"\n→ Sent: {action}")
        if data:
            print(f"   Data: {json.dumps(data, indent=2)}")

    async def receive_message(self):
        """Receive message from server."""
        response = await self.websocket.recv()
        data = json.loads(response)

        print(f"\n← Received:")
        print(json.dumps(data, indent=2))

        return data

    async def login(self, pan: str, password: str):
        """Login to ePortal."""
        print("\n" + "=" * 80)
        print("LOGIN")
        print("=" * 80)

        await self.send_message("login", {
            "pan": pan,
            "password": password
        })

        response = await self.receive_message()

        if response.get("success"):
            self.session_id = response.get("session_id")
            print(f"\n✓ Login successful! Session ID: {self.session_id[:16]}...")
            return True
        else:
            print(f"\n✗ Login failed: {response.get('error')}")
            return False

    async def logout(self):
        """Logout from ePortal."""
        print("\n" + "=" * 80)
        print("LOGOUT")
        print("=" * 80)

        await self.send_message("logout", session_id=self.session_id)

        response = await self.receive_message()

        if response.get("success"):
            self.session_id = None
            print(f"\n✓ Logout successful!")
            return True
        else:
            print(f"\n✗ Logout failed: {response.get('error')}")
            return False

    async def forgot_password_init(self, pan: str, method: str, dob: str = None):
        """Initialize forgot password flow."""
        print("\n" + "=" * 80)
        print(f"FORGOT PASSWORD - INIT ({method.upper()})")
        print("=" * 80)

        data = {
            "pan": pan,
            "method": method
        }

        if dob:
            data["dob"] = dob

        await self.send_message("forgot_password_init", data)

        response = await self.receive_message()

        if response.get("success"):
            fp_session_id = response.get("session_id")
            print(f"\n✓ OTP sent! Forgot Password Session ID: {fp_session_id[:16]}...")
            return fp_session_id
        else:
            print(f"\n✗ Failed: {response.get('error')}")
            return None

    async def forgot_password_verify(self, fp_session_id: str, mobile_otp: str,
                                     email_otp: str = None, new_password: str = ""):
        """Verify OTP and reset password."""
        print("\n" + "=" * 80)
        print("FORGOT PASSWORD - VERIFY")
        print("=" * 80)

        data = {
            "session_id": fp_session_id,
            "mobile_otp": mobile_otp,
            "new_password": new_password
        }

        if email_otp:
            data["email_otp"] = email_otp

        await self.send_message("forgot_password_verify", data)

        response = await self.receive_message()

        if response.get("success"):
            print(f"\n✓ Password reset successful!")
            return True
        else:
            print(f"\n✗ Failed: {response.get('error')}")
            return False

    async def get_prefill_data(self, year: int, pan: str):
        """Get prefill data."""
        print("\n" + "=" * 80)
        print(f"GET PREFILL DATA - Year {year}")
        print("=" * 80)

        await self.send_message("prefill", {
            "year": year,
            "pan": pan
        })

        return await self.receive_message()

    async def get_itr_status(self, pan: str):
        """Get ITR status."""
        print("\n" + "=" * 80)
        print("GET ITR STATUS")
        print("=" * 80)

        await self.send_message("itr_status", {
            "pan": pan
        })

        return await self.receive_message()

    async def get_active_filings(self, pan: str):
        """Get active filings."""
        print("\n" + "=" * 80)
        print("GET ACTIVE FILINGS")
        print("=" * 80)

        await self.send_message("active_filings", {
            "pan": pan
        })

        return await self.receive_message()

    async def check_aadhaar(self, pan: str):
        """Check Aadhaar linkage."""
        print("\n" + "=" * 80)
        print("CHECK AADHAAR LINKAGE")
        print("=" * 80)

        await self.send_message("check_aadhaar", {
            "pan": pan
        })

        return await self.receive_message()

    async def close(self):
        """Close WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            print("\n✓ Connection closed")


async def example_login_flow():
    """Example: Login and use ePortal functions."""
    print("\n" + "=" * 80)
    print("EXAMPLE 1: LOGIN FLOW")
    print("=" * 80)

    client = AuthenticatedEPortalClient(api_key="your-client-key-here")

    try:
        # Connect
        await client.connect()

        # Authenticate (REQUIRED FIRST!)
        authenticated = await client.authenticate()
        if not authenticated:
            return

        await asyncio.sleep(1)

        # Login
        login_success = await client.login(
            pan="ABCDE1234F",
            password="your-password-here"
        )

        if login_success:
            await asyncio.sleep(2)

            # Get prefill data
            await client.get_prefill_data(year=2025, pan="ABCDE1234F")

            await asyncio.sleep(2)

            # Get active filings
            await client.get_active_filings(pan="ABCDE1234F")

            await asyncio.sleep(2)

            # Check Aadhaar
            await client.check_aadhaar(pan="ABCDE1234F")

            await asyncio.sleep(2)

            # Logout
            await client.logout()

    finally:
        await client.close()


async def example_forgot_password_email_mobile():
    """Example: Forgot password using email/mobile."""
    print("\n" + "=" * 80)
    print("EXAMPLE 2: FORGOT PASSWORD (EMAIL/MOBILE)")
    print("=" * 80)

    client = AuthenticatedEPortalClient(api_key="your-client-key-here")

    try:
        # Connect
        await client.connect()

        # Authenticate (REQUIRED FIRST!)
        authenticated = await client.authenticate()
        if not authenticated:
            return

        await asyncio.sleep(1)

        # Initialize forgot password with email/mobile
        fp_session_id = await client.forgot_password_init(
            pan="ABCDE1234F",
            method="email_mobile",
            dob="1993-08-09"
        )

        if fp_session_id:
            print("\n" + "=" * 80)
            print("Enter OTPs received:")
            print("=" * 80)

            # In real scenario, get OTPs from user input
            mobile_otp = input("Mobile OTP: ").strip()
            email_otp = input("Email OTP: ").strip()
            new_password = input("New Password: ").strip()

            await asyncio.sleep(1)

            # Verify OTP and set password
            await client.forgot_password_verify(
                fp_session_id=fp_session_id,
                mobile_otp=mobile_otp,
                email_otp=email_otp,
                new_password=new_password
            )

    finally:
        await client.close()


async def example_forgot_password_aadhaar():
    """Example: Forgot password using Aadhaar."""
    print("\n" + "=" * 80)
    print("EXAMPLE 3: FORGOT PASSWORD (AADHAAR)")
    print("=" * 80)

    client = AuthenticatedEPortalClient(api_key="your-client-key-here")

    try:
        # Connect
        await client.connect()

        # Authenticate (REQUIRED FIRST!)
        authenticated = await client.authenticate()
        if not authenticated:
            return

        await asyncio.sleep(1)

        # Initialize forgot password with Aadhaar
        fp_session_id = await client.forgot_password_init(
            pan="ABCDE1234F",
            method="aadhaar"
        )

        if fp_session_id:
            print("\n" + "=" * 80)
            print("Enter OTP received on Aadhaar-linked mobile:")
            print("=" * 80)

            # In real scenario, get OTP from user input
            mobile_otp = input("Mobile OTP: ").strip()
            new_password = input("New Password: ").strip()

            await asyncio.sleep(1)

            # Verify OTP and set password
            await client.forgot_password_verify(
                fp_session_id=fp_session_id,
                mobile_otp=mobile_otp,
                new_password=new_password
            )

    finally:
        await client.close()


async def simple_example():
    """Simplest example showing authentication flow."""
    print("\n" + "=" * 80)
    print("SIMPLE AUTHENTICATION EXAMPLE")
    print("=" * 80)

    # Connect to WebSocket
    async with websockets.connect("ws://localhost:8000/ws") as websocket:
        # 1. Receive welcome message
        welcome = await websocket.recv()
        print(f"Welcome: {welcome}\n")

        # 2. Authenticate with API key
        print("Step 1: Authenticating...")
        auth_msg = {
            "action": "authenticate",
            "api_key": "your-client-key-here"
        }
        await websocket.send(json.dumps(auth_msg))

        auth_response = await websocket.recv()
        print(f"Auth Response: {auth_response}\n")

        # 3. Login
        print("Step 2: Logging in...")
        login_msg = {
            "action": "login",
            "data": {
                "pan": "ABCDE1234F",
                "password": "your-password-here"
            }
        }
        await websocket.send(json.dumps(login_msg))

        login_response = await websocket.recv()
        login_data = json.loads(login_response)
        print(f"Login Response: {json.dumps(login_data, indent=2)}\n")

        if login_data.get("success"):
            session_id = login_data.get("session_id")

            # 4. Use ePortal function
            print("Step 3: Getting active filings...")
            filings_msg = {
                "action": "active_filings",
                "session_id": session_id,
                "data": {
                    "pan": "ABCDE1234F"
                }
            }
            await websocket.send(json.dumps(filings_msg))

            filings_response = await websocket.recv()
            print(f"Filings Response: {filings_response}\n")


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("ePortal WebSocket Client Examples")
    print("=" * 80)
    print("\nChoose an example:")
    print("1. Login and use ePortal functions")
    print("2. Forgot password (Email/Mobile)")
    print("3. Forgot password (Aadhaar)")
    print("4. Simple authentication example")
    print("=" * 80)

    choice = input("\nEnter choice (1-4): ").strip()

    if choice == "1":
        asyncio.run(example_login_flow())
    elif choice == "2":
        asyncio.run(example_forgot_password_email_mobile())
    elif choice == "3":
        asyncio.run(example_forgot_password_aadhaar())
    elif choice == "4":
        asyncio.run(simple_example())
    else:
        print("Invalid choice!")
