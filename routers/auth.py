import asyncio
import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from core.security import check_brute_force, verify_password, failed_logins
from database.auth_store import get_allowlist

router = APIRouter()
logger = logging.getLogger("pypam")
ph = PasswordHasher()

@router.post("/login")
async def login(data: dict, request: Request):
    """
    Endpoint for student authentication.

    Args:
        data (dict): JSON containing 'username' and 'password'.
    """
    ip = request.client.host
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    can_attempt, wait_time = check_brute_force(ip)
    if not can_attempt:
        logger.warning(
            f"Brute-force blocked for {ip} (Waiting {wait_time}s)",
            extra={"user": username or "unknown"},
        )
        return {"success": False, "msg": f"Wait {wait_time}s before trying again."}

    users = get_allowlist()
    if username in users:
        stored_password = users[username]
        if verify_password(password, stored_password):
            logger.info(
                f"Student Login: {username} (Successful)", extra={"user": username}
            )
            failed_logins.pop(ip, None)
            request.session["user"] = username
            request.session["role"] = "student"
            return {"success": True}

    # Record failure
    failed_logins[ip] = failed_logins.get(ip, 0) + 1

    # Artificial delay to thwart automated attacks
    await asyncio.sleep(1)

    logger.warning(
        f"Student Login: {username} (Failed - Invalid credentials)",
        extra={"user": username},
    )
    return {"success": False}

@router.get("/me")
async def me(request: Request):
    username = request.session.get("user")
    role = request.session.get("role")
    if username and role == "student":
        return {"authenticated": True, "username": username}
    return {"authenticated": False}


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"success": True}
