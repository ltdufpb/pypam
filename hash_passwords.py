#!/usr/bin/env python3
"""
PyPAM Password Hashing Filter
Reads 'username:password' from stdin and writes 'username:hash' to stdout.
"""

import sys
from argon2 import PasswordHasher


def main():
    ph = PasswordHasher()

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if ":" in line:
                username, password = line.split(":", 1)
                hashed = ph.hash(password.strip())
                sys.stdout.write(f"{username.strip()}:{hashed}\n")
                sys.stdout.flush()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
