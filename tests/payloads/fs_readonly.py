import os
print("--- Testing Root Filesystem Read-Only ---")
try:
    with open("/etc/pwned", "w") as f:
        f.write("test")
except Exception as e:
    print(f"Write to /etc blocked as expected: {e}")
