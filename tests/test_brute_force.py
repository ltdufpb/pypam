import pytest
import asyncio
import os
import time
import importlib
from fastapi.testclient import TestClient
import pypam
from database.auth_store import ALLOWLIST_FILE
from core import config
from core import security
from cachetools import TTLCache

from routers import auth

from argon2 import PasswordHasher

ph = PasswordHasher()


@pytest.fixture(autouse=True)
def setup_brute_force(monkeypatch):

    monkeypatch.setattr(config, "MAX_FAILED_ATTEMPTS", 2)
    monkeypatch.setattr(config, "BRUTE_FORCE_COOLDOWN", 60)
    
    
    monkeypatch.setattr(security, "MAX_FAILED_ATTEMPTS", 2)
    monkeypatch.setattr(security, "BRUTE_FORCE_COOLDOWN", 60)

   
    nova_cache = TTLCache(maxsize=1000, ttl=2)

   
    monkeypatch.setattr(security, "failed_logins", nova_cache)
    monkeypatch.setattr(auth, "failed_logins", nova_cache)

   
    with open(ALLOWLIST_FILE, "w") as f:
        f.write(f"testuser:{ph.hash('testpass')}\n")
        
    yield
    
    if os.path.exists(ALLOWLIST_FILE):
        os.remove(ALLOWLIST_FILE)


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
