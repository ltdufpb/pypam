import os
import time

print("--- Testing PID Limit (15) ---")
for i in range(25):
    try:
        pid = os.fork()
        if pid == 0:
            time.sleep(5)
            os._exit(0)
        else:
            print(f"Spawned process {i + 1}")
    except Exception as e:
        print(f"Fork failed at process {i + 1}: {e}")
        break
