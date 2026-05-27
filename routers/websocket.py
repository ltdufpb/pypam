import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.config import user_lock
from core.security import check_code_safety
from services.docker_sandbox import execute_user_code

router = APIRouter()
logger = logging.getLogger("pypam")

@router.websocket("/ws")
async def run_code(ws: WebSocket):
    """
    Main execution hub. Manages the real-time bridge between the student and
    their isolated Python environment.
    """
    await ws.accept()

    # Early capacity check
    if user_lock.locked():
        logger.warning(
            "Resource Exhaustion: Server busy (Max users reached)",
            extra={"user": "system"},
        )
        await ws.send_json({"t": "out", "d": "\n[Server Busy] Please wait...\n"})
        await ws.send_json({"t": "end", "c": 1})
        await ws.close()
        return

    username = ws.session.get("user")
    role = ws.session.get("role")

    if not username or role != "student":
        logger.warning("Unauthorized WebSocket access attempt")
        await ws.send_json({"t": "auth_error"})
        await ws.close()
        return

    # Enter the semaphore context to reserve an execution slot
    async with user_lock:
        try:
            ip = ws.client.host

            # Protocol Start: Receive code from client
            data = await ws.receive_json()
            code = data.get("code", "")

            if not code:
                logger.info(
                    f"Empty code submitted by {username}", extra={"user": username}
                )
                return

            # --- Layer B: AST safety check BEFORE writing to disk ---
            safety_error = check_code_safety(code)
            if safety_error is not None:
                logger.warning(
                    f"MISBEHAVIOR: Blocked code from {username}: {safety_error}",
                    extra={"user": username},
                )
                await ws.send_json({"t": "out", "d": f"\n{safety_error}\n"})
                await ws.send_json({"t": "end", "c": 1})
                return

            # Passa o controle para a função isolada no services/docker_sandbox.py
            await execute_user_code(code, ws, username)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(
                f"WebSocket endpoint error for {username}: {e}",
                extra={"user": username},
            )
            try:
                await ws.close()
            except Exception:
                pass