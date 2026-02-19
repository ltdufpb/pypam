#!/usr/bin/env python3
"""
PyPAM Password Hashing Utility
Generates secure Argon2 hashes from a list of usernames and passwords.
"""

import sys
import os
from argon2 import PasswordHasher


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 hash_passwords.py <input_file> <output_file>")
        print("\nExample for students:")
        print("  Create 'raw_students.txt' with: username password")
        print("  Run: python3 hash_passwords.py raw_students.txt students.txt")
        print("\nExample for admin:")
        print("  Create 'raw_admin.txt' with: admin mysecretpass")
        print("  Run: python3 hash_passwords.py raw_admin.txt admin.txt")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    ph = PasswordHasher()

    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found.")
        sys.exit(1)

    print(f"Hashing passwords from {input_path} into {output_path}...")

    count = 0
    with open(input_path, "r") as f_in, open(output_path, "w") as f_out:
        for line in f_in:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if len(parts) >= 2:
                username = parts[0]
                password = parts[1]
                hashed = ph.hash(password)
                f_out.write(f"{username}:{hashed}\n")
                count += 1
            else:
                print(f"Skipping malformed line: {line}")

    print(f"Successfully processed {count} entries.")


if __name__ == "__main__":
    main()
