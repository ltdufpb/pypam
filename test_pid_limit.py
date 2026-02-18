import os
import time

print("--- Testing PID Limit (15) ---")
# This attempt at a 'fork bomb' is stopped by pids_limit=15
for i in range(25):
    try:
        pid = os.fork()
        if pid == 0:  # I am the child
            time.sleep(5)
            os._exit(0)
        else:
            print(f"Spawned process {i + 1} (PID: {pid})")
    except Exception as e:
        print(f"Fork failed at process {i + 1}: {e}")
        break
