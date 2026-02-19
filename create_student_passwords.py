#!/usr/bin/env python3
"""
PyPAM Student Table Parser
Reads a table from stdin and writes 'ID:ID_reversed' to stdout.
Assumes ID is the second column (index 1).
"""

import sys


def main():
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if len(parts) >= 2:
                # Username is the ID (2nd column)
                username = parts[1]
                # Password is the reverse of the ID
                password = username[::-1]
                sys.stdout.write(f"{username}:{password}\n")
                sys.stdout.flush()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
