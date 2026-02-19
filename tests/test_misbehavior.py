import pytest
import asyncio
import os
import importlib
import pypam
from fastapi.testclient import TestClient


def read_payload(name):
    path = os.path.join(os.path.dirname(__file__), "payloads", f"{name}.py")
    with open(path, "r") as f:
        return f.read()


@pytest.fixture(autouse=True)
def setup_user(monkeypatch):
    # Set a short timeout for tests
    monkeypatch.setenv("EXECUTION_TIMEOUT", "2")
    # Reload pypam to apply the new environment variable
    importlib.reload(pypam)

    with open(pypam.ALLOWLIST_FILE, "w") as f:
        f.write("testuser:testpass\n")
    yield
    if os.path.exists(pypam.ALLOWLIST_FILE):
        os.remove(pypam.ALLOWLIST_FILE)


@pytest.mark.timeout(10)
def test_execution_timeout():
    # Use pypam.app after reload
    client = TestClient(pypam.app)

    # Login to establish session
    login_res = client.post(
        "/login", json={"username": "testuser", "password": "testpass"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["success"] == True

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json(
            {
                "code": read_payload("infinite"),
            }
        )

        timeout_msg_received = False
        while True:
            try:
                data = websocket.receive_json()
                if data["t"] == "out" and "[Execution Timeout]" in data["d"]:
                    timeout_msg_received = True
                if data["t"] == "end":
                    break
            except Exception:
                break

        assert timeout_msg_received
