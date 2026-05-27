import asyncio
import logging
from fastapi import APIRouter, Request

from database.auth_store import get_allowlist, save_allowlist, get_admin_creds
from core.security import check_brute_force, verify_password, get_password_hash, failed_logins

router = APIRouter()
logger = logging.getLogger("pypam")

@router.post("/admin/login")
async def admin_login(data: dict, request: Request):
    """
    Endpoint for administrator authentication.
    """
    ip = request.client.host
    username = (data.get("username") or "").strip()

    can_attempt, wait_time = check_brute_force(ip)
    if not can_attempt:
        logger.warning(
            f"Brute-force blocked for {ip} (Waiting {wait_time}s)",
            extra={"user": username or "unknown"},
        )
        return {"success": False, "msg": f"Wait {wait_time}s before trying again."}

    creds = get_admin_creds()
    if creds:
        u, p = creds
        if username == u and verify_password(data.get("password"), p):
            logger.info(
                f"Admin Login: {username} (Successful)", extra={"user": username}
            )
            failed_logins.pop(ip, None)
            request.session["user"] = username
            request.session["role"] = "admin"
            return {"success": True}

    # Record failure
    failed_logins[ip] = failed_logins.get(ip, 0) + 1

    # Artificial delay to thwart automated attacks
    await asyncio.sleep(1)

    logger.warning(
        f"Admin Login: {username} (Failed - Invalid credentials)",
        extra={"user": username},
    )
    return {"success": False}

@router.post("/admin/get_users")
async def get_users(request: Request):
    """
    Fetches the list of students. Passwords are excluded for security.
    """
    if request.session.get("role") != "admin":
        return JSONResponse({"success": False, "msg": "Unauthorized"}, status_code=403)
        
    users = get_allowlist()
    return {"success": True, "users": sorted(list(users.keys()))}


@router.post("/admin/save_user")
async def save_user(data: dict, request: Request):
    """
    Updates an existing student or creates a new one.
    Handles password resetting (blank password = no change).
    """
    if request.session.get("role") != "admin":
        return JSONResponse({"success": False, "msg": "Unauthorized"}, status_code=403)

    u = request.session.get("user")
    new_u = (data.get("username") or "").strip()
    new_p = (data.get("password") or "").strip()
    old_u = (data.get("old_username") or "").strip()

    if not new_u or ":" in new_u:
        return {"success": False, "msg": "Invalid username"}

    users = get_allowlist()
    if old_u and old_u in users:
        # Edit existing student logic
        if new_p:
            final_p = get_password_hash(new_p)
        else:
            final_p = users[old_u]

        if old_u != new_u:
            del users[old_u]  # Handle username change
            logger.info(
                f"Student renamed: {old_u} to {new_u} (Admin: {u})", extra={"user": u}
            )
        else:
            logger.info(
                f"Student password updated: {new_u} (Admin: {u})", extra={"user": u}
            )
        users[new_u] = final_p
    else:
        # New student logic
        if not new_p:
            return {"success": False, "msg": "Password required"}
        users[new_u] = get_password_hash(new_p)
        logger.info(f"New student created: {new_u} (Admin: {u})", extra={"user": u})

    save_allowlist(users)
    return {"success": True}


@router.post("/admin/delete_user")
async def delete_user(data: dict, request: Request):
    """
    Deletes a student from the database.
    """
    if request.session.get("role") != "admin":
        return JSONResponse({"success": False, "msg": "Unauthorized"}, status_code=403)

    u = request.session.get("user")
    target = (data.get("username") or "").strip()
    users = get_allowlist()
    if target in users:
        del users[target]
        save_allowlist(users)
        logger.info(f"Student deleted: {target} (Admin: {u})", extra={"user": u})
    return {"success": True}