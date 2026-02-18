import os

print("--- Testing Memory Limit (48MB) ---")
print("Allocating memory... the container should be killed shortly.")
data = []
while True:
    data.append(" " * 1024 * 1024)
    if len(data) % 5 == 0:
        print(f"Allocated {len(data)} MB...")
