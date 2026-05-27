import os

# --- AUTHENTICATION STATE ---
ALLOWLIST_FILE = "students.txt"  # Schema: username:password
ADMIN_CREDS_FILE = "admin.txt"  # Schema: username:password


def get_allowlist():
    """
    Parses the student credentials file. Format: username:password_hash
    """
    if not os.path.exists(ALLOWLIST_FILE):
        return {}
    users = {}
    with open(ALLOWLIST_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if ":" in line:
                u, p = line.split(":", 1)
                users[u.strip()] = p.strip()
    return users

def save_allowlist(users):
    """
    Persists the student credentials dictionary to the filesystem using the
    colon-separated format.
    """
    with open(ALLOWLIST_FILE, "w") as f:
        for u, p in users.items():
            f.write(f"{u}:{p}\n")

def get_admin_creds():
    """
    Parses the administrator credentials file. Format: username:password_hash
    """
    if not os.path.exists(ADMIN_CREDS_FILE):
        return None
    with open(ADMIN_CREDS_FILE, "r") as f:
        line = f.read().strip()
        if ":" in line:
            u, p = line.split(":", 1)
            return u.strip(), p.strip()
    return None