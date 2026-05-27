import os
import asyncio
import secrets
from fastapi import FastAPI
from contextlib import asynccontextmanager

# --- SECURITY CONTEXT ---
# SECRET_KEY: Used to sign session cookies.
SECRET_KEY = os.getenv("SESSION_SECRET", secrets.token_hex(32))


# PORT: The port the FastAPI server will listen on.
PORT = int(os.getenv("PORT", 8000))

# HTTPS_ENABLED: Set to "true" when behind an HTTPS reverse proxy (e.g. nginx).
# This ensures session cookies are marked as secure (sent only over HTTPS).
HTTPS_ENABLED = os.getenv("HTTPS_ENABLED", "false").lower() == "true"

# DOCKER_IMAGE: A lightweight Python image. Alpine is used for fast startup.
DOCKER_IMAGE = "python:3.13-alpine"

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

# Initialize the semaphore to enforce the concurrency limit
user_lock = asyncio.Semaphore(MAX_CONCURRENT_USERS)

# active_sessions: Thread-safe set (in async context) to prevent duplicate logins.
active_sessions = set()

# --- LIFESPAN MANAGEMENT ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events for the FastAPI application.
    Clears active user sessions to ensure a clean state upon restart.
    """
    # ---- STARTUP ----
    active_sessions.clear()
    yield 
    # ---- SHUTDOWN ----
    active_sessions.clear()