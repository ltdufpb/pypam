#!/usr/bin/env python3

"""
PyPAM - Prof. Alan Moraes' Online Python Editor
===============================================================================
1. HIGH-LEVEL ARCHITECTURAL OVERVIEW
===============================================================================
PyPAM is built on a modern asynchronous stack designed for high concurrency
and security. The architecture follows a client-server model where the server
acts as a secure orchestrator between web clients and transient Docker
containers.
Key Technologies:
- Backend: FastAPI (Python 3.10+) utilizing ASGI for asynchronous I/O.
- Execution: Docker Engine via the Docker SDK for Python.
- Communication: WebSockets (PEP 3156) for low-latency terminal emulation.
- Frontend: Vanilla JavaScript SPA with CodeMirror 5 for IDE-like features.

Execution Lifecycle:
1. Student writes code in the CodeMirror editor (Frontend).
2. Code is transmitted via a persistent WebSocket connection.
3. Server validates credentials and acquires an execution slot (Semaphore).
4. Server scaffolds a unique temporary environment on the host filesystem.
5. A Docker container is spawned with strict hardware and software isolation.
6. Stdin/Stdout/Stderr are bridged between the container's TTY and the
   client's browser in real-time.
7. Upon termination, the container and all temporary files are destroyed.
===============================================================================
2. SECURITY ARCHITECTURE (DEEP DIVE)
===============================================================================
Executing arbitrary user code is inherently dangerous. PyPAM implements
defense-in-depth through five distinct layers:
Layer 1: User Authentication
- Every execution request is verified against a local student database.
- Active session tracking prevents resource exhaustion by limiting one
  concurrent execution per user.
Layer 2: Network Isolation
- Containers are created with 'network_disabled=True'.
- This prevents the student's code from scanning the host network,
  accessing external APIs, or being used in botnets.
Layer 3: Resource Constraints (Control Groups)
- RAM is capped at 48MB. Exceeding this triggers the OOM killer.
- CPU is throttled to 20% of a single core via 'nano_cpus'.
- PID Limit (15) prevents 'fork bombs' (recursive process creation).
Layer 4: Filesystem Hardening
- The root filesystem is 'read_only=True'.
- A 'tmpfs' is mounted at /tmp to allow small, non-persistent writes.
- The user's script is mounted via a volume with limited permissions.
- 'os.chmod' is used on the host to ensure the container user (nobody) can
  read the script but not interfere with other students' data.

Layer 5: Process Privilege
- The container process runs as UID 65534 (nobody). Even if a student
  escapes the Python interpreter, they lack root privileges to exploit
  kernel vulnerabilities or host filesystem mounts.
===============================================================================
3. LOW-LEVEL IMPLEMENTATION NOTES
===============================================================================
- Docker Socket: The server requires access to /var/run/docker.sock.
- Asynchronous Bridge: The 'forward_output' inner function uses
  'run_in_executor' because the Docker SDK's socket.read() is a blocking
  operation that would otherwise stall the FastAPI event loop.
- Terminal Proxy: Mobile keyboards often don't trigger on 'div' elements.
  The terminal uses a hidden 'input' element to proxy focus and keystrokes.
"""

import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from routers import auth, admin, websocket

from core.config import SECRET_KEY, HTTPS_ENABLED, PORT, lifespan

# --- LOGGING SETUP ---
logger = logging.getLogger("pypam")

# --- APP INITIALIZATION ---
app = FastAPI(title="PyPAM - Python Automated Monitor", lifespan=lifespan)

# --- STATIC FILES CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.mount(
    "/static", 
    StaticFiles(directory=os.path.join(BASE_DIR, "templates", "static")), 
    name="static"
)

# --- MIDDLEWARES ---
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=HTTPS_ENABLED,
)

# --- GLOBAL ERROR HANDLING ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global handler to catch unhandled errors and prevent leaking tracebacks."""
    logger.exception(f"Unhandled server error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "msg": "Internal server error. Please contact the administrator.",
        },
    )

# --- TEMPLATES CONFIGURATION ---
templates = Jinja2Templates(directory="templates")

# --- INTERFACE ROUTERS (FRONTEND) ---
@app.get("/", response_class=HTMLResponse)
async def get_student_ui(request: Request):
    """Retorna a interface principal do estudante (Login + Editor)."""
    return templates.TemplateResponse(request=request, name="student.html")

@app.get("/admin", response_class=HTMLResponse)
async def get_admin_ui(request: Request):
    """Retorna o painel administrativo do professor."""
    return templates.TemplateResponse(request=request, name="admin.html")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(websocket.router)

if __name__ == "__main__":
    import uvicorn
    # Start the production server on all interfaces
    uvicorn.run(app, host="0.0.0.0", port=PORT)

