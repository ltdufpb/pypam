import pytest
import asyncio
import os
import time
import importlib
from fastapi.testclient import TestClient
import pypam


from argon2 import PasswordHasher

ph = PasswordHasher()


@pytest.fixture(autouse=True)
def setup_brute_force(monkeypatch):
    # Set very short limits for quick testing using environment variables
    monkeypatch.setenv("MAX_FAILED_ATTEMPTS", "2")
    monkeypatch.setenv("BRUTE_FORCE_COOLDOWN", "2")

    # Reload pypam to apply the new environment variables
    importlib.reload(pypam)

    # Clear the failed logins tracking for each test (it's a global in the module)
    pypam.failed_logins.clear()

    with open(pypam.ALLOWLIST_FILE, "w") as f:
        f.write(f"testuser:{ph.hash('testpass')}\n")
    yield
    if os.path.exists(pypam.ALLOWLIST_FILE):
        os.remove(pypam.ALLOWLIST_FILE)


def test_login_brute_force_protection():
    # Use the reloaded app
    client = TestClient(pypam.app)

    # Attempt 1: Fail
    response = client.post(
        "/login", json={"username": "testuser", "password": "wrongpassword"}
    )
    assert response.status_code == 200
    assert response.json()["success"] == False

    # Attempt 2: Fail (This hits the MAX_FAILED_ATTEMPTS limit of 2)
    response = client.post(
        "/login", json={"username": "testuser", "password": "wrongpassword"}
    )
    assert response.json()["success"] == False

    # Attempt 3: Should be blocked immediately by rate limiter
    response = client.post(
        "/login", json={"username": "testuser", "password": "wrongpassword"}
    )
    assert response.json()["success"] == False
    assert "Wait" in response.json().get("msg", "")

    # Wait for cooldown (2 seconds as configured)
    time.sleep(2.1)

    # Attempt 4: Should be allowed to try again (and succeed with correct password)
    response = client.post(
        "/login", json={"username": "testuser", "password": "testpass"}
    )
    assert response.json()["success"] == True
