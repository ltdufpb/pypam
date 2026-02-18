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
import asyncio
import docker
import logging
import time
from collections import defaultdict
from docker.types import Mount
import tempfile
import shutil
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager


# --- LOGGING CONFIGURATION ---
# We use a filter to ensure the username is included in every log entry.
class UserFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "user"):
            record.user = "system"
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s [%(user)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pypam")
logger.addFilter(UserFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for the FastAPI application."""
    logger.info("PyPAM Service Starting")
    logger.info(f"Port: {PORT}, Max Users: {MAX_CONCURRENT_USERS}")
    yield
    logger.info("PyPAM Service Stopping")


# --- CONFIGURATION & LIMITS ---
# PORT: The port the FastAPI server will listen on.
PORT = int(os.getenv("PORT", 8000))

# DOCKER_IMAGE: A lightweight Python image. Alpine is used for fast startup.
DOCKER_IMAGE = "python:3.14-alpine"

# MAX_CONCURRENT_USERS: Max number of students running code at the same time.
# This prevents the host from running out of file descriptors or memory.
MAX_CONCURRENT_USERS = 10

# MEM_LIMIT: Memory limit for the container (cgroups).
MEM_LIMIT = "48m"

# DISK_LIMIT: Max disk space for writable areas (tmpfs).
DISK_LIMIT = "10m"

# CPU_LIMIT_NANO: CPU limit in nanoseconds (0.20 = 20% of one core).
CPU_LIMIT_NANO = int(0.20 * 1e9)

# EXECUTION_TIMEOUT: Max time a script can run (in seconds).
# Increased to 300s (5m) to allow slow typing during input().
EXECUTION_TIMEOUT = int(os.getenv("EXECUTION_TIMEOUT", 300))

# --- BRUTE-FORCE PROTECTION ---
# MAX_FAILED_ATTEMPTS: Failed logins allowed before a cooldown is triggered.
MAX_FAILED_ATTEMPTS = int(os.getenv("MAX_FAILED_ATTEMPTS", 5))
# BRUTE_FORCE_COOLDOWN: Cooldown duration in seconds (10 minutes).
BRUTE_FORCE_COOLDOWN = int(os.getenv("BRUTE_FORCE_COOLDOWN", 600))

# failed_logins: Tracks {ip_address: {"count": int, "last_attempt": float}}
failed_logins = defaultdict(lambda: {"count": 0, "last_attempt": 0})


def check_brute_force(ip: str):
    """
    Checks if an IP is currently in a cooldown state.
    """
    record = failed_logins[ip]
    now = time.time()
    if record["count"] >= MAX_FAILED_ATTEMPTS:
        time_passed = now - record["last_attempt"]
        if time_passed < BRUTE_FORCE_COOLDOWN:
            return False, int(BRUTE_FORCE_COOLDOWN - time_passed)
        # Reset count after cooldown
        record["count"] = 0
    return True, 0


# --- AUTHENTICATION STATE ---

ALLOWLIST_FILE = "students.txt"  # Schema: username:password
ADMIN_CREDS_FILE = "admin.txt"  # Schema: username:password

# active_sessions: Thread-safe set (in async context) to prevent duplicate logins.
active_sessions = set()


def get_allowlist():
    """
    Parses the student credentials file.

    Returns:
        dict: A mapping of {username: password}.
    """
    if not os.path.exists(ALLOWLIST_FILE):
        return {}
    users = {}
    with open(ALLOWLIST_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if ":" in line:
                u, p = line.split(":", 1)
                users[u] = p
    return users


def save_allowlist(users):
    """
    Persists the student credentials dictionary to the filesystem.

    Args:
        users (dict): The mapping of {username: password} to save.
    """
    with open(ALLOWLIST_FILE, "w") as f:
        for u, p in users.items():
            f.write(f"{u}:{p}\n")


def get_admin_creds():
    """
    Parses the administrator credentials file.

    Returns:
        tuple: (username, password). Defaults to ('admin', 'admin123').
    """
    if not os.path.exists(ADMIN_CREDS_FILE):
        return "admin", "admin123"
    with open(ADMIN_CREDS_FILE, "r") as f:
        line = f.read().strip()
        if ":" in line:
            return line.split(":", 1)
    return "admin", "admin123"


# --- DOCKER ENGINE CONNECTIVITY ---
try:
    # Connect to the local Docker daemon
    client = docker.from_env()
    try:
        # Check if the desired image exists locally
        client.images.get(DOCKER_IMAGE)
    except docker.errors.ImageNotFound:
        # Pull the image if it's missing (happens on first run)
        logger.info(f"Pulling image {DOCKER_IMAGE}...")
        client.images.pull(DOCKER_IMAGE)
except Exception as e:
    logger.critical(f"Docker is not ready: {e}")
    exit(1)

# Initialize the semaphore to enforce the concurrency limit
user_lock = asyncio.Semaphore(MAX_CONCURRENT_USERS)
app = FastAPI(lifespan=lifespan)


@app.post("/login")
async def login(data: dict, request: Request):
    """
    Endpoint for student authentication.

    Args:
        data (dict): JSON containing 'username' and 'password'.
    """
    ip = request.client.host
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    can_attempt, wait_time = check_brute_force(ip)
    if not can_attempt:
        logger.warning(
            f"Brute-force blocked for {ip} (Waiting {wait_time}s)",
            extra={"user": username or "unknown"},
        )
        return {"success": False, "msg": f"Wait {wait_time}s before trying again."}

    users = get_allowlist()
    if username in users and users[username] == password:
        logger.info(f"Student Login: {username} (Successful)", extra={"user": username})
        failed_logins.pop(ip, None)
        return {"success": True}

    # Record failure
    failed_logins[ip]["count"] += 1
    failed_logins[ip]["last_attempt"] = time.time()

    # Artificial delay to thwart automated attacks
    await asyncio.sleep(1)

    logger.warning(
        f"Student Login: {username} (Failed - Invalid credentials)",
        extra={"user": username},
    )
    return {"success": False}


@app.post("/admin/login")
async def admin_login(data: dict, request: Request):
    """
    Endpoint for administrator authentication.
    """
    ip = request.client.host
    u, p = get_admin_creds()
    username = data.get("username")

    can_attempt, wait_time = check_brute_force(ip)
    if not can_attempt:
        logger.warning(
            f"Brute-force blocked for {ip} (Waiting {wait_time}s)",
            extra={"user": username or "admin"},
        )
        return {"success": False, "msg": f"Wait {wait_time}s before trying again."}

    if username == u and data.get("password") == p:
        logger.info(f"Admin Login: {username} (Successful)", extra={"user": username})
        failed_logins.pop(ip, None)
        return {"success": True}

    # Record failure
    failed_logins[ip]["count"] += 1
    failed_logins[ip]["last_attempt"] = time.time()

    # Artificial delay
    await asyncio.sleep(1)

    logger.warning(
        f"Admin Login: {username} (Failed - Invalid credentials)",
        extra={"user": username},
    )
    return {"success": False}


@app.post("/admin/get_users")
async def get_users(data: dict):
    """
    Fetches the list of students. Passwords are excluded for security.
    """
    u, p = get_admin_creds()
    if data.get("admin_u") != u or data.get("admin_p") != p:
        return {"success": False}
    users = get_allowlist()
    return {"success": True, "users": sorted(list(users.keys()))}


@app.post("/admin/save_user")
async def save_user(data: dict):
    """
    Updates an existing student or creates a new one.
    Handles password resetting (blank password = no change).
    """
    u, p = get_admin_creds()
    if data.get("admin_u") != u or data.get("admin_p") != p:
        logger.warning(
            f"Admin attempt without credentials: {data.get('admin_u')}",
            extra={"user": data.get("admin_u") or "unknown"},
        )
        return {"success": False, "msg": "Access denied"}

    new_u = (data.get("username") or "").strip()
    new_p = (data.get("password") or "").strip()
    old_u = (data.get("old_username") or "").strip()

    if not new_u or ":" in new_u:
        return {"success": False, "msg": "Invalid username"}

    users = get_allowlist()
    if old_u and old_u in users:
        # Edit existing student logic
        final_p = new_p if new_p else users[old_u]
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
        users[new_u] = new_p
        logger.info(f"New student created: {new_u} (Admin: {u})", extra={"user": u})

    save_allowlist(users)
    return {"success": True}


@app.post("/admin/delete_user")
async def delete_user(data: dict):
    """
    Deletes a student from the database.
    """
    u, p = get_admin_creds()
    if data.get("admin_u") != u or data.get("admin_p") != p:
        return {"success": False, "msg": "Access denied"}

    target = (data.get("username") or "").strip()
    users = get_allowlist()
    if target in users:
        del users[target]
        save_allowlist(users)
        logger.info(f"Student deleted: {target} (Admin: {u})", extra={"user": u})
    return {"success": True}


@app.websocket("/ws")
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

    username = None
    users = get_allowlist()

    # Enter the semaphore context to reserve an execution slot
    async with user_lock:
        container = None
        # Create a unique sandbox directory on the host
        temp_dir = tempfile.mkdtemp(prefix="pypam_")
        # Grant full permissions so the non-root container user can write files
        os.chmod(temp_dir, 0o777)
        has_sent_output = False

        try:
            # Protocol Start: Receive config and code from client
            data = await ws.receive_json()
            username = (data.get("username") or "").strip()
            password = (data.get("password") or "").strip()
            code = data.get("code", "")
            ip = ws.client.host

            can_attempt, wait_time = check_brute_force(ip)
            if not can_attempt:
                logger.warning(
                    f"Brute-force blocked (WS) for {ip} (Waiting {wait_time}s)",
                    extra={"user": username or "unknown"},
                )
                await ws.send_json(
                    {"t": "out", "d": f"\n[Access Denied] Wait {wait_time}s.\n"}
                )
                await ws.send_json({"t": "end", "c": 1})
                return

            # Security double-check: verify credentials again within the socket
            if username not in users or users[username] != password:
                # Record failure
                failed_logins[ip]["count"] += 1
                failed_logins[ip]["last_attempt"] = time.time()
                await asyncio.sleep(1)

                logger.warning(
                    f"Unauthorized WS access attempt: {username}",
                    extra={"user": username},
                )
                await ws.send_json(
                    {"t": "out", "d": "\n[Access Denied] Invalid credentials.\n"}
                )
                await ws.send_json({"t": "end", "c": 1})
                return

            failed_logins.pop(ip, None)

            # Prevent concurrent sessions for the same user
            if username in active_sessions:
                logger.warning(
                    f"Concurrent session attempt: {username}", extra={"user": username}
                )
                await ws.send_json(
                    {"t": "out", "d": "\n[Access Denied] User already active.\n"}
                )
                await ws.send_json({"t": "end", "c": 1})
                return

            active_sessions.add(username)
            if not code:
                logger.info(
                    f"Empty code submitted by {username}", extra={"user": username}
                )
                return

            # Persist the student's code to the sandbox directory
            script_path = os.path.join(temp_dir, "script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            os.chmod(script_path, 0o644)

            # Define the sandbox container
            logger.info(f"Spawning container for {username}", extra={"user": username})
            container = client.containers.create(
                DOCKER_IMAGE,
                command=["python3", "-u", "/app/script.py"],  # -u disables buffering
                working_dir="/app",
                stdin_open=True,  # Allow interactive input
                tty=True,  # Allocate a pseudo-TTY for better interactive behavior
                detach=True,
                network_disabled=True,  # Isolation
                mem_limit=MEM_LIMIT,  # RAM limit
                memswap_limit=MEM_LIMIT,  # Hard limit (RAM + Swap)
                nano_cpus=CPU_LIMIT_NANO,  # CPU limit
                pids_limit=15,  # Process limit
                read_only=True,  # Protect root filesystem
                # Use tmpfs with explicit size and world-writable permissions
                tmpfs={
                    "/app": f"size={DISK_LIMIT},mode=1777",
                    "/tmp": f"size={DISK_LIMIT},mode=1777",
                },
                # Mount the script as read-only on top of the tmpfs
                volumes={script_path: {"bind": "/app/script.py", "mode": "ro"}},
                user="65534:65534",  # Run as 'nobody' (unprivileged)
                environment={"PYTHONIOENCODING": "utf-8", "PYTHON_COLORS": "0"},
            )

            # IMPORTANT: We attach to the socket BEFORE starting the container.
            # This ensures we don't miss the first few bytes of output.
            socket = container.attach_socket(
                params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
            )
            container.start()

            # Background worker to pull data from Docker and push to Browser
            async def forward_output():
                nonlocal has_sent_output
                loop = asyncio.get_event_loop()
                while True:
                    try:
                        # run_in_executor prevents blocking the main event loop
                        data = await loop.run_in_executor(None, socket.read, 1024)
                        if not data:
                            break
                        has_sent_output = True
                        await ws.send_json(
                            {"t": "out", "d": data.decode(errors="replace")}
                        )
                    except Exception as e:
                        logger.debug(
                            f"Output forwarding error for {username}: {e}",
                            extra={"user": username},
                        )
                        break

            output_task = asyncio.create_task(forward_output())

            # Interactive loop: Wait for user input or container death
            start_time = asyncio.get_event_loop().time()
            try:
                while True:
                    container.reload()
                    if container.status != "running":
                        break

                    # Enforce global execution timeout
                    if asyncio.get_event_loop().time() - start_time > EXECUTION_TIMEOUT:
                        logger.warning(
                            f"MISBEHAVIOR: Execution timeout for {username}",
                            extra={"user": username},
                        )
                        await ws.send_json(
                            {
                                "t": "out",
                                "d": f"\n[Execution Timeout] Script killed after {EXECUTION_TIMEOUT}s.\n",
                            }
                        )
                        container.kill()
                        break

                    try:
                        # Small timeout allows us to check 'container.status' periodically
                        msg = await asyncio.wait_for(ws.receive_json(), timeout=0.2)
                        if msg.get("t") == "in":
                            # Forward browser keystrokes to the container's stdin
                            os.write(socket.fileno(), msg.get("d").encode())
                    except asyncio.TimeoutError:
                        pass
                    except Exception as e:
                        logger.debug(
                            f"Input handling error for {username}: {e}",
                            extra={"user": username},
                        )
                        break
            except Exception as e:
                logger.error(
                    f"Container loop error for {username}: {e}",
                    extra={"user": username},
                )

            # Cleanup output task
            try:
                await asyncio.wait_for(output_task, timeout=2.0)
            except Exception:
                output_task.cancel()

            container.reload()
            state = container.attrs["State"]
            exit_code = state["ExitCode"]
            oom_killed = state.get("OOMKilled", False)

            if oom_killed:
                logger.warning(
                    f"MISBEHAVIOR: OOM Killed for {username} (Memory Limit: {MEM_LIMIT})",
                    extra={"user": username},
                )
                await ws.send_json(
                    {
                        "t": "out",
                        "d": f"\n[Resource Limit] Out of Memory: Script exceeded {MEM_LIMIT}.\n",
                    }
                )
            elif exit_code == 137:
                # 137 often means SIGKILL, which we use for timeout, but could also be OOM if not caught by flag
                # Since we check oom_killed first, this is likely our manual kill (timeout) or PID limit hit
                if asyncio.get_event_loop().time() - start_time >= EXECUTION_TIMEOUT:
                    pass  # Already logged as timeout
                else:
                    logger.warning(
                        f"MISBEHAVIOR: Container killed for {username} (Likely PID limit/Fork Bomb or external kill)",
                        extra={"user": username},
                    )
                    await ws.send_json(
                        {
                            "t": "out",
                            "d": "\n[Resource Limit] Script terminated (Likely hit process limit).\n",
                        }
                    )
            elif exit_code != 0:
                logger.info(
                    f"Execution finished for {username} with error (Exit Code: {exit_code})",
                    extra={"user": username},
                )
            else:
                logger.info(
                    f"Execution finished for {username} (Successful)",
                    extra={"user": username},
                )

            # Error recovery: If no output was sent but exit code is non-zero,
            # it's likely a Python compilation error or startup crash.
            if not has_sent_output and exit_code != 0:
                try:
                    logs = container.logs().decode(errors="replace")
                    if logs:
                        await ws.send_json({"t": "out", "d": logs})
                except Exception as e:
                    logger.error(
                        f"Failed to fetch logs for {username}: {e}",
                        extra={"user": username},
                    )

            await ws.send_json({"t": "end", "c": exit_code})

        except Exception as e:
            logger.exception(
                f"Exception during code execution for {username or 'unknown'}",
                extra={"user": username or "unknown"},
            )
            await ws.send_json({"t": "out", "d": f"\nSystem Error: {e}\n"})
            await ws.send_json({"t": "end", "c": 1})
        finally:
            # RESOURCE CLEANUP (CRITICAL)
            if username and username in active_sessions:
                active_sessions.remove(username)
            if container:
                try:
                    container.remove(force=True)
                except Exception as e:
                    logger.error(
                        f"Failed to remove container for {username}: {e}",
                        extra={"user": username},
                    )
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(
                        f"Failed to remove temp dir for {username}: {e}",
                        extra={"user": username},
                    )


# --- FRONTEND TEMPLATES ---

# SHARED_CSS: Visual identity of the app. Defined once to ensure consistency.
SHARED_CSS = """
:root {
    --primary: #007acc;
    --success: #28a745;
    --danger: #d73a49;
    --secondary: #e9ecef;
    --secondary-text: #495057;
    --bg-gray: #f8f9fa;
    --border: #dee2e6;
    --text: #1c1e21;
    --radius: 12px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{height:100vh;overflow:hidden;background:var(--bg-gray);font-family:system-ui,-apple-system,sans-serif;color:var(--text)}
.view-container {
    position:absolute;top:0;left:0;right:0;bottom:0;
    display:none;flex-direction:column;
}
.login-screen {
    display:flex;align-items:center;justify-content:center;height:100%;
}
.login-box {
    background:#fff;padding:40px;border-radius:16px;
    box-shadow:0 10px 30px rgba(0,0,0,0.08);text-align:center;width:90%;max-width:400px;
}
h2 { margin-bottom:24px;font-size:24px;font-weight:600 }
input, .btn {
    font-family:inherit;padding:12px 16px;margin:8px 0;border-radius:var(--radius);border:1px solid var(--border);outline:none;font-size:16px;
}
input[type="text"], input[type="password"] {
    width:100%;background:#fff;color:var(--text);transition:border-color 0.2s;
}
input:focus { border-color:var(--primary);box-shadow:0 0 0 2px rgba(0,122,204,0.1) }
.btn {
    cursor:pointer;font-weight:600;transition:all 0.2s;text-align:center;border:none;
}
.btn-primary { background:var(--primary);color:#fff;width:100%; }
.btn-primary:hover { opacity:0.9; }
.btn-success { background:var(--success);color:#fff; }
.btn-success:hover { opacity:0.9; }
.btn-danger { background:var(--danger);color:#fff; }
.btn-danger:hover { opacity:0.9; }
.btn-secondary { background:var(--secondary);color:var(--secondary-text); }
.btn-secondary:hover { background:#dde1e3; }

.app-header { display:flex;justify-content:space-between;align-items:center;padding:10px 15px;background:var(--secondary);border-bottom:1px solid var(--border) }
.user-label { color:var(--secondary-text);font-weight:600;font-size:14px }
.logout-btn { background:none;border:none;color:var(--secondary-text);cursor:pointer;font-size:18px;padding:4px;display:flex;align-items:center }

/* CodeMirror Integration styles */
.CodeMirror { height: 100%; font-family: "Fira Code", monospace; font-size: 14px; line-height: 1.6; background: #fff; }
.CodeMirror-gutters { background: #f8f9fa; border-right: 1px solid #e5e7eb; }
.CodeMirror-linenumber { color: #adb5bd; padding: 0 8px; }
.cm-s-default .cm-keyword { color: #0000ff; font-weight: bold; }
.cm-s-default .cm-string { color: #a31515; }
.cm-s-default .cm-comment { color: #008000; font-style: italic; }
.cm-s-default .cm-variable-2 { color: #001080; }
.cm-s-default .cm-def { color: #795e26; }
.cm-s-default .cm-builtin { color: #0000ff; }
.cm-s-default .cm-number { color: #098658; }
.cm-s-default .cm-operator { color: #333; }
"""

# HTML snippet for the standardized login card
LOGIN_BOX_TEMPLATE = """
    <div class="login-screen">
        <div class="login-box">
            <h2>{title}</h2>
            <input type="text" id="username" name="username" placeholder="Username" autocomplete="username" autocapitalize="none" autocorrect="off" onkeydown="if(event.key==='Enter') {login_func}()"/>
            <input type="password" id="password" name="password" placeholder="Password" autocomplete="current-password" onkeydown="if(event.key==='Enter') {login_func}()"/>
            <button class="btn btn-primary" onclick="{login_func}()">ENTER</button>
            <div id="error-msg" style="color:var(--danger);font-size:14px;margin-top:12px;height:1.4em;"></div>
        </div>
    </div>
"""

# HTML snippet for the consistent app header
HEADER_TEMPLATE = """
    <div class="app-header">
        <span class="user-display"></span>
        <div style="display:flex;gap:10px;">
            <button class="logout-btn" title="Limpar código" onclick="clearEditor()">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 3 21 3 19 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
            </button>
            <button class="logout-btn" title="Sair" onclick="doLogout()">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>
            </button>
        </div>
    </div>
"""

# --- MAIN STUDENT UI ---
HTML = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=yes">
<title>PyPAM - Prof. Alan Moraes' Online Python Editor</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.13/codemirror.min.css">
<style>
{SHARED_CSS}
#editor-container {{ flex:1; overflow:hidden; background:#fff; position:relative; }}
#terminal{{flex:1;padding:20px;font-family:"Fira Code",monospace;font-size:14px;line-height:1.6;color:#333;background:#f9fafb;white-space:pre-wrap;word-break:break-word;outline:none;overflow-y:auto;}}
#run, #back {{ width:100%; border-radius:0; margin:0; padding:16px; font-size:16px; }}
.cursor{{display:inline-block;width:8px;height:1.2em;background:#333;vertical-align:middle;animation:b 1s step-end infinite}}
@keyframes b{{50%{{opacity:0}}}}
</style>
</head>
<body>
<div id="login-view" class="view-container">
    {LOGIN_BOX_TEMPLATE.format(title="Student Access", login_func="doLogin")}
</div>
<div id="editor-view" class="view-container">
    {HEADER_TEMPLATE}
    <div id="editor-container">
        <textarea id="code-editor"></textarea>
    </div>
    <button id="run" class="btn btn-success" onclick="start()">▶ EXECUTE</button>
</div>
<div id="terminal-view" class="view-container">
    {HEADER_TEMPLATE}
    <div id="terminal" tabindex="0"></div>
    <!-- The hidden input below triggers the mobile keyboard and captures data for the terminal -->
    <input type="text" id="term-input" style="position:absolute; opacity:0; pointer-events:none; left:-9999px;" autocapitalize="none" autocorrect="off" autocomplete="off" spellcheck="false">
    <button id="back" class="btn btn-secondary" onclick="back()">← Back</button>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.13/codemirror.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.13/mode/python/python.min.js"></script>
<script>
var ws, term=document.getElementById("terminal"), termInput=document.getElementById("term-input"), editor;

// Initialize CodeMirror with Python mode and mobile-aware input style
editor = CodeMirror.fromTextArea(document.getElementById("code-editor"), {{
    mode: "python",
    lineNumbers: true,
    indentUnit: 4,
    smartIndent: true,
    tabSize: 4,
    indentWithTabs: false,
    inputStyle: "contenteditable", // contenteditable handles line breaks better on Android/SwiftKey
    extraKeys: {{"Tab": function(cm) {{ cm.replaceSelection("    ", "end"); }} }},
    viewportMargin: Infinity,
    autocapitalize: false,
    spellcheck: false,
    autocorrect: false
}});

function showView(id) {{
    document.querySelectorAll(".view-container").forEach(d => d.style.display = "none");
    var target = document.getElementById(id);
    target.style.display = "flex";
    var u = localStorage.getItem("pypam_u");
    // Update all username labels in headers
    if(u) {{ target.querySelectorAll(".user-display").forEach(el => el.innerText = u); }}
    // CodeMirror needs a refresh if it was initialized while hidden
    if(id === "editor-view") setTimeout(() => editor.refresh(), 10);
    // Focus terminal proxy on mobile
    if(id === "terminal-view") setTimeout(() => termInput.focus(), 50);
}}

// Clicking the terminal area focuses the hidden input to show the keyboard
term.onclick = () => termInput.focus();

// Terminal Input Logic: Maps standard keyboard events to TTY control characters
termInput.onkeydown = (e) => {{
    if(!ws || ws.readyState!==1) return;
    if(e.key === "Enter") ws.send(JSON.stringify({{t:"in",d:"\\n"}}));
    else if(e.key === "Backspace") ws.send(JSON.stringify({{t:"in",d:"\\x7f"}}));
    else if(e.ctrlKey && e.key === "c") ws.send(JSON.stringify({{t:"in",d:"\\x03"}}));
}};

// Handles characters sent by mobile predictive text/autocorrect
termInput.oninput = (e) => {{
    if(!ws || ws.readyState!==1) return;
    if(e.inputType === "insertText" && e.data) {{
        ws.send(JSON.stringify({{t:"in", d:e.data}}));
    }}
    termInput.value = ""; // Clear proxy immediately
}};

async function doLogin() {{
    var u = document.getElementById("username").value.trim();
    var p = document.getElementById("password").value.trim();
    if(!u || !p) return;
    try {{
        var res = await fetch("/login", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{username: u, password: p}})
        }});
        var data = await res.json();
        if(data.success) {{
            // Persist credentials locally for session maintenance
            localStorage.setItem("pypam_u", u);
            localStorage.setItem("pypam_p", p);
            initApp();
        }} else {{ document.getElementById("error-msg").innerText = "Invalid username or password."; }}
    }} catch(e) {{ document.getElementById("error-msg").innerText = "Connection error."; }}
}}

function doLogout() {{
    if(confirm("Deseja realmente sair?")) {{
        localStorage.removeItem("pypam_u");
        localStorage.removeItem("pypam_p");
        initApp();
    }}
}}

function clearEditor() {{
    if(confirm("Deseja realmente limpar todo o código?")) {{
        editor.setValue("");
    }}
}}

function initApp() {{
    var u = localStorage.getItem("pypam_u"), p = localStorage.getItem("pypam_p");
    if(u && p) {{ showView("editor-view"); }}
    else {{ 
        document.getElementById("username").value = ""; 
        document.getElementById("password").value = ""; 
        showView("login-view"); 
    }}
}}

function start(){{
    var code = editor.getValue();
    var u=localStorage.getItem("pypam_u"), p=localStorage.getItem("pypam_p");
    showView("terminal-view");
    term.innerHTML=""; term.focus();
    
    var proto=location.protocol==="https:"?"wss:":"ws:";
    ws=new WebSocket(proto+"//"+location.host+"/ws");
    
    ws.onopen=function(){{ 
        addCursor(); 
        ws.send(JSON.stringify({{code:code, username:u, password:p}})); 
    }};
    
    ws.onmessage=function(e){{
        var m=JSON.parse(e.data);
        if(m.t==="out") processTermData(m.d);
        else if(m.t==="end"){{ 
            append("\\n\\n[Status: "+m.c+"]"); 
            removeCursor(); 
            ws.close(); 
        }}
    }};
}}

// Process terminal output, manually handling backspace characters
function processTermData(text) {{
    text = text.replace(/\\r/g, "");
    for (var i = 0; i < text.length; i++) {{
        var char = text[i];
        if (char === "\\b" || char === "\\x08" || char === "\\x7f") {{ removeLast(); }} 
        else {{ appendChar(char); }}
    }}
    term.scrollTop = term.scrollHeight;
}}

function appendChar(char){{ 
    var c = document.getElementById("cur"); 
    c.parentNode.insertBefore(document.createTextNode(char), c); 
}}

function append(txt) {{ processTermData(txt); }}

function removeLast(){{
    var c = document.getElementById("cur");
    while (c.previousSibling) {{
        var node = c.previousSibling;
        if (node.nodeType === 3) {{ 
            if (node.length > 0) {{ node.deleteData(node.length - 1, 1); return; }} 
            else {{ node.remove(); }}
        }} else {{ return; }}
    }}
}}

function back(){{ if(ws){{ws.close();ws=null;}} showView("editor-view"); }}

function addCursor(){{ 
    var c=document.createElement("span"); 
    c.className="cursor"; 
    c.id="cur"; 
    term.appendChild(c); 
}}

function removeCursor(){{ 
    var c=document.getElementById("cur"); 
    if(c)c.remove(); 
}}

initApp();
</script>
</body>
</html>"""

# --- ADMIN UI ---
ADMIN_HTML = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=yes">
<title>PyPAM Admin - Prof. Alan Moraes' Online Python Editor</title>
<style>
{SHARED_CSS}
body{{overflow-y:auto;}}
#container {{ max-width:600px;margin:0 auto;padding:20px }}
header {{ display:flex;justify-content:space-between;align-items:center;margin-bottom:20px }}
.user-card {{ background:#fff;padding:16px;border-radius:var(--radius);margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.05);display:flex;justify-content:space-between;align-items:center }}
.user-info {{ font-weight:600;font-size:16px;color:var(--text) }}
.user-actions {{ display:flex;gap:8px }}
#modal {{ position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);display:none;align-items:center;justify-content:center;z-index:100;padding:20px }}
#modal-box {{ background:#fff;padding:24px;border-radius:16px;width:100%;max-width:400px;text-align:center }}
.modal-input {{ width:100%;margin-bottom:12px }}
</style>
</head>
<body>
    <div id="admin-login" class="view-container">
        {LOGIN_BOX_TEMPLATE.format(title="PyPAM Admin", login_func="doAdminLogin")}
    </div>

    <div id="container" style="display:none">
        <header>
            <h2 style="font-size:20px">Students</h2>
            <button class="btn btn-success" onclick="openModal()">+ NEW</button>
        </header>
        <div id="user-list"></div>
    </div>

    <div id="modal">
        <div id="modal-box">
            <h3 id="modal-title" style="margin-bottom:16px">New Student</h3>
            <p id="modal-desc" style="color:var(--secondary-text);font-size:14px;margin-bottom:16px"></p>
            <input type="text" id="m_u" class="modal-input" placeholder="Username" autocomplete="username" />
            <input type="password" id="m_p" class="modal-input" placeholder="Password" autocomplete="new-password" />
            <div style="display:flex;gap:10px;margin-top:10px">
                <button class="btn btn-secondary" style="flex:1" onclick="closeModal()">CANCEL</button>
                <button class="btn btn-primary" style="flex:1" onclick="handleModalSave()">SAVE</button>
            </div>
        </div>
    </div>

<script>
var admin_u, admin_p, editing_u = null;

async function doAdminLogin() {{
    var u = document.getElementById("username").value, p = document.getElementById("password").value;
    var res = await fetch("/admin/login", {{
        method: "POST", headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{username: u, password: p}})
    }});
    var data = await res.json();
    if(data.success) {{
        admin_u = u; admin_p = p;
        document.getElementById("admin-login").style.display = "none";
        document.getElementById("container").style.display = "block";
        loadUsers();
    }} else document.getElementById("error-msg").innerText = "Invalid admin.";
}}

async function loadUsers() {{
    var res = await fetch("/admin/get_users", {{
        method: "POST", 
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{admin_u: admin_u, admin_p: admin_p}})
    }});
    var data = await res.json();
    if(data.success) renderUsers(data.users);
}}

function renderUsers(users) {{
    var list = document.getElementById("user-list");
    list.innerHTML = "";
    users.forEach(u => {{
        let div = document.createElement("div");
        div.className = "user-card";
        div.innerHTML = `
            <div class="user-info">${{u}}</div>
            <div class="user-actions">
                <button class="btn btn-primary" style="padding:8px 12px" onclick="openModal('${{u}}')">Edit</button>
                <button class="btn btn-danger" style="padding:8px 12px" onclick="deleteUser('${{u}}')">✕</button>
            </div>
        `;
        list.appendChild(div);
    }});
}}

function openModal(u = null) {{
    editing_u = u;
    document.getElementById("modal").style.display = "flex";
    document.getElementById("m_u").value = u || "";
    document.getElementById("m_p").value = "";
    if(u) {{
        document.getElementById("modal-title").innerText = "Edit Student";
        document.getElementById("modal-desc").innerText = "Leave password blank to keep the current one.";
    }} else {{
        document.getElementById("modal-title").innerText = "New Student";
        document.getElementById("modal-desc").innerText = "Admin: hand over the device to the student.";
    }}
}}

function closeModal() {{ document.getElementById("modal").style.display = "none"; }}

async function handleModalSave() {{
    var u = document.getElementById("m_u").value.trim(), p = document.getElementById("m_p").value.trim();
    if(!u || (!editing_u && !p)) return;
    var res = await fetch("/admin/save_user", {{
        method: "POST", 
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{admin_u: admin_u, admin_p: admin_p, old_username: editing_u, username: u, password: p}})
    }});
    var data = await res.json();
    if(data.success) {{ closeModal(); loadUsers(); }} else alert(data.msg);
}}

async function deleteUser(u) {{
    if(confirm("Delete " + u + "?")) {{
        await fetch("/admin/delete_user", {{
            method: "POST", 
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{admin_u: admin_u, admin_p: admin_p, username: u}})
        }});
        loadUsers();
    }}
}}

// Reveal the login screen on load
document.getElementById("admin-login").style.display = "flex";
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/admin", response_class=HTMLResponse)
def admin():
    return ADMIN_HTML


if __name__ == "__main__":
    import uvicorn

    # Start the production server on all interfaces
    uvicorn.run(app, host="0.0.0.0", port=PORT)
