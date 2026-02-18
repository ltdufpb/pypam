import os
print("--- Testing Disk Limit (10MB) ---")
try:
    with open("/app/bomb.bin", "wb") as f:
        for i in range(20):
            f.write(b"\0" * 1024 * 1024)
            print(f"Wrote {i+1} MB to /app...")
except OSError as e:
    print(f"Disk limit hit: {e}")
