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
            # We only remove the trailing newline, preserving potential
            # intentional spaces in the password itself.
            line = line.rstrip("\n\r")
            if not line or line.startswith("#"):
                continue

            if ":" in line:
                username, password = line.split(":", 1)
                # We strip the username but keep the password EXACTLY as provided
                hashed = ph.hash(password)
                sys.stdout.write(f"{username.strip()}:{hashed}\n")
                sys.stdout.flush()

    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
