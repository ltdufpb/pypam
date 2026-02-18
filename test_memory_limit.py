print("--- Testing Memory Limit (48MB) ---")
print("Allocating memory... the container should be killed shortly.")
data = []
try:
    while True:
        # Allocate 1MB chunks
        data.append(" " * 1024 * 1024)
        if len(data) % 5 == 0:
            print(f"Allocated {len(data)} MB...")
except Exception as e:
    print(f"Caught: {e}")
