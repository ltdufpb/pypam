import os
import ast
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cachetools import TTLCache
from core.config import MAX_FAILED_ATTEMPTS, BRUTE_FORCE_COOLDOWN
from database.auth_store import ADMIN_CREDS_FILE

ph = PasswordHasher()

# failed_logins: TTL Cache automatically clears entries after the cooldown.
# This prevents memory exhaustion from spoofed IP addresses.
failed_logins = TTLCache(maxsize=1000, ttl=BRUTE_FORCE_COOLDOWN)

def verify_password(plain_password, hashed_password):
    """
    Verifies a plain text password against an Argon2 hash.
    Plaintext passwords are not supported and will fail.
    """
    try:
        # We only support Argon2 hashes
        if not hashed_password.startswith("$argon2"):
            return False
        return ph.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def get_password_hash(password):
    """Generates a secure Argon2 hash for the given password."""
    return ph.hash(password)

def check_brute_force(ip: str):
    """
    Checks if an IP is currently in a cooldown state.
    """
    count = failed_logins.get(ip, 0)
    if count >= MAX_FAILED_ATTEMPTS:
        return False, BRUTE_FORCE_COOLDOWN // 60
    return True, 0

# --- AST CODE SAFETY CHECKER ---

_FORBIDDEN_MODULES = frozenset({"subprocess", "ctypes", "importlib", "multiprocessing"})
_FORBIDDEN_BUILTINS = frozenset({"eval", "exec", "compile", "__import__"})
_FORBIDDEN_ATTRS = frozenset(
    {
        "system",
        "popen",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
        "__subclasses__",
    }
)


def check_code_safety(code: str) -> str | None:
    """
    AST-based pre-execution check. Returns a Portuguese error string if
    dangerous patterns are found, or None if code appears safe.
    SyntaxErrors pass through so Python reports them naturally.

    Note: Primary kernel-level defense is the seccomp profile (Layer A).
    This function is Layer B: user-facing feedback.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None  # Let the container report syntax errors naturally

    for node in ast.walk(tree):
        # import subprocess / import ctypes / import importlib
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_MODULES:
                    return (
                        f"[Código Bloqueado] Uso do módulo '{root}' "
                        "não é permitido neste ambiente."
                    )

        # from subprocess import run
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _FORBIDDEN_MODULES:
                    return (
                        f"[Código Bloqueado] Uso do módulo '{root}' "
                        "não é permitido neste ambiente."
                    )

        # __import__(...) / eval(...) / exec(...) / compile(...)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in _FORBIDDEN_BUILTINS:
                    return (
                        f"[Código Bloqueado] Chamada à função '{node.func.id}' "
                        "não é permitida neste ambiente."
                    )
            # os.system(...) / os.popen(...) / x.__subclasses__() / etc.
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in _FORBIDDEN_ATTRS:
                    return (
                        f"[Código Bloqueado] Uso de '{node.func.attr}' "
                        "não é permitido neste ambiente."
                    )

        # x.__subclasses__ (reference without call)
        elif isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_ATTRS:
                return (
                    f"[Código Bloqueado] Uso de '{node.attr}' "
                    "não é permitido neste ambiente."
                )

    return None