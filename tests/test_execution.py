import pytest
import asyncio
import json
import os
from fastapi.testclient import TestClient
from pypam import app, ALLOWLIST_FILE


# Helper to read payload files
def read_payload(name):
    path = os.path.join(os.path.dirname(__file__), "payloads", f"{name}.py")
    with open(path, "r") as f:
        return f.read()


@pytest.fixture(autouse=True)
def setup_user():
    # Ensure there's a test user
    with open(ALLOWLIST_FILE, "w") as f:
        f.write("testuser:testpass\n")
    yield
    if os.path.exists(ALLOWLIST_FILE):
        os.remove(ALLOWLIST_FILE)


def test_websocket_execution_basic():
    client = TestClient(app)
    # Login to establish session
    login_res = client.post(
        "/login", json={"username": "testuser", "password": "testpass"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["success"] == True

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json(
            {
                "code": read_payload("basic"),
            }
        )

        outputs = []
        while True:
            data = websocket.receive_json()
            if data["t"] == "out":
                outputs.append(data["d"])
            elif data["t"] == "end":
                assert data["c"] == 0
                break

        output_str = "".join(outputs)
        assert "Hello from PyPAM!" in output_str


def test_websocket_execution_security_disk():
    client = TestClient(app)
    # Login to establish session
    login_res = client.post(
        "/login", json={"username": "testuser", "password": "testpass"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["success"] == True

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json(
            {
                "code": read_payload("disk_limit"),
            }
        )

        outputs = []
        while True:
            data = websocket.receive_json()
            if data["t"] == "out":
                outputs.append(data["d"])
            elif data["t"] == "end":
                assert data["c"] == 0
                break

        output_str = "".join(outputs)
        assert "Disk limit hit" in output_str
        assert "No space left on device" in output_str


def test_websocket_execution_security_memory():
    client = TestClient(app)
    # Login to establish session
    login_res = client.post(
        "/login", json={"username": "testuser", "password": "testpass"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["success"] == True

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json(
            {
                "code": read_payload("memory_limit"),
            }
        )

        while True:
            data = websocket.receive_json()
            if data["t"] == "end":
                assert data["c"] == 137
                break


def test_websocket_execution_security_pid():
    client = TestClient(app)
    # Login to establish session
    login_res = client.post(
        "/login", json={"username": "testuser", "password": "testpass"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["success"] == True

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json(
            {
                "code": read_payload("pid_limit"),
            }
        )

        outputs = []
        while True:
            data = websocket.receive_json()
            if data["t"] == "out":
                outputs.append(data["d"])
            elif data["t"] == "end":
                assert data["c"] == 0
                break

        output_str = "".join(outputs)
        assert "Fork failed" in output_str


def test_websocket_execution_security_readonly_fs():
    client = TestClient(app)
    # Login to establish session
    login_res = client.post(
        "/login", json={"username": "testuser", "password": "testpass"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["success"] == True

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json(
            {
                "code": read_payload("fs_readonly"),
            }
        )

        outputs = []
        while True:
            data = websocket.receive_json()
            if data["t"] == "out":
                outputs.append(data["d"])
            elif data["t"] == "end":
                assert data["c"] == 0
                break

        output_str = "".join(outputs)
        assert "Write to /etc blocked" in output_str
        assert "Read-only file system" in output_str
