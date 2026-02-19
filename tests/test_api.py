import pytest
from httpx import AsyncClient, ASGITransport
from pypam import app, ALLOWLIST_FILE, ADMIN_CREDS_FILE
import os
import shutil


# Fixture to provide a clean state for each test
@pytest.fixture(autouse=True)
def clean_state():
    # Store old files to restore them later if needed
    backup_allowlist = f"{ALLOWLIST_FILE}.bak"
    backup_admin = f"{ADMIN_CREDS_FILE}.bak"
    if os.path.exists(ALLOWLIST_FILE):
        shutil.copy(ALLOWLIST_FILE, backup_allowlist)
    if os.path.exists(ADMIN_CREDS_FILE):
        shutil.copy(ADMIN_CREDS_FILE, backup_admin)

    # Create fresh files
    with open(ALLOWLIST_FILE, "w") as f:
        f.write("testuser:testpass\n")
    with open(ADMIN_CREDS_FILE, "w") as f:
        f.write("admin:admin123\n")

    yield

    # Restore backups
    if os.path.exists(backup_allowlist):
        shutil.move(backup_allowlist, ALLOWLIST_FILE)
    elif os.path.exists(ALLOWLIST_FILE):
        os.remove(ALLOWLIST_FILE)

    if os.path.exists(backup_admin):
        shutil.move(backup_admin, ADMIN_CREDS_FILE)
    elif os.path.exists(ADMIN_CREDS_FILE):
        os.remove(ADMIN_CREDS_FILE)


@pytest.mark.asyncio
async def test_student_login():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Valid login
        response = await ac.post(
            "/login", json={"username": "testuser", "password": "testpass"}
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}

        # Invalid login
        response = await ac.post(
            "/login", json={"username": "testuser", "password": "wrong"}
        )
        assert response.status_code == 200
        assert response.json() == {"success": False}


@pytest.mark.asyncio
async def test_admin_login():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/admin/login", json={"username": "admin", "password": "admin123"}
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}


@pytest.mark.asyncio
async def test_admin_user_management():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Login first to establish session
        login_res = await ac.post(
            "/admin/login", json={"username": "admin", "password": "admin123"}
        )
        assert login_res.status_code == 200
        assert login_res.json()["success"] == True

        # Get users
        response = await ac.post("/admin/get_users")
        assert response.status_code == 200
        assert "testuser" in response.json()["users"]

        # Save new user
        new_user = {"username": "newstudent", "password": "newpassword"}
        response = await ac.post("/admin/save_user", json=new_user)
        assert response.status_code == 200
        assert response.json()["success"] == True

        # Verify user was saved
        response = await ac.post("/admin/get_users")
        assert "newstudent" in response.json()["users"]

        # Delete user
        delete_data = {"username": "newstudent"}
        response = await ac.post("/admin/delete_user", json=delete_data)
        assert response.status_code == 200
        assert response.json()["success"] == True

        # Verify user was deleted
        response = await ac.post("/admin/get_users")
        assert "newstudent" not in response.json()["users"]
