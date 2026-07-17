import os
import threading

os.environ["CLIENT_KEY"] = "test-key"
os.environ["WEBSOCKET_IDLE_TIMEOUT_SECONDS"] = "300"

from main import ConnectionManager, active_sessions


def test_idle_session_cleanup():
    manager = ConnectionManager()
    stop_event = threading.Event()
    active_sessions["session"] = {
        "client": type("Client", (), {"_stop_extender_event": stop_event})()
    }
    manager.active_connections["connection"] = {
        "session_id": "session",
        "fp_session_id": None,
        "pan_link_session_id": None,
    }

    manager.cleanup_sessions("connection")

    assert "session" not in active_sessions
    assert stop_event.is_set()


if __name__ == "__main__":
    test_idle_session_cleanup()
    print("idle cleanup check passed")
