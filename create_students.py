#!/usr/bin/env python3
"""
PyPAM User Creation Utility
Generates secure Argon2 hashes for students and administrators.
"""

import sys
import os
from argon2 import PasswordHasher

ph = PasswordHasher()

def get_password_hash(password):
    """Computes the Argon2 hash for a given plaintext password."""
    return ph.hash(password)

def process_students(input_path="input.txt", output_path="students.txt"):
    """
    Reads a list of students from input_path and generates a hashed credentials file.
    Expected input format: any_prefix username
    """
    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found.")
        return

    print(f"Processing students from {input_path}...")
    count = 0
    with open(input_path, "r") as input_file, open(output_path, "w") as output_file:
        for line in input_file:
            parts = line.split()
            if len(parts) >= 2:
                username = parts[1]
                # Default password is the username reversed
                password = username[::-1]
                hashed = get_password_hash(password)
                output_file.write(f"{username}:{hashed}\n")
                count += 1
    
    print(f"Successfully created {output_path} with {count} students.")

def create_admin(username, password):
    """Creates the admin.txt file with a hashed password."""
    hashed = get_password_hash(password)
    with open("admin.txt", "w") as f:
        f.write(f"{username}:{hashed}\n")
    print(f"Successfully created admin.txt for user: {username}")

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--admin":
        if len(sys.argv) != 4:
            print("Usage: python3 create_students.py --admin <username> <password>")
        else:
            create_admin(sys.argv[2], sys.argv[3])
    else:
        process_students()

if __name__ == "__main__":
    main()
