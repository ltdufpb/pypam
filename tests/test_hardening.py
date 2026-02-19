import pytest
import asyncio
import os
import importlib
from fastapi.testclient import TestClient
import pypam

# Security tests from the provided example
HARDENING_TESTS = {
    # === 1. RESOURCE EXHAUSTION ===
    "mem_bomb_list": "x = [0] * (10**10)",
    "cpu_infinite_loop": "while True:\n    pass",
    "disk_fill": "f = open('/tmp/bomb.txt','w')\nwhile True:\n    f.write('A'*10**6)",
    "fork_bomb": "import os\nwhile True:\n    os.fork()",
    # === 2. OS ESCAPE & SENSITIVE FILES ===
    "read_passwd": "print(open('/etc/passwd').read())",
    "read_shadow": "try:\n    print(open('/etc/shadow').read())\nexcept PermissionError: print('Permission Denied')",
    "os_system": "import os\nos.system('whoami && id')",
    "write_system": "try:\n    open('/etc/cron.d/backdoor','w').write('pwned')\nexcept OSError: print('Read-only FS')",
    # === 3. NETWORK ACCESS ===
    "socket_connect": "import socket\ntry:\n    s=socket.socket()\n    s.settimeout(1)\n    s.connect(('8.8.8.8', 53))\nexcept Exception as e: print(f'Network Blocked: {e}')",
    # === 4. DANGEROUS IMPORTS & BUILTINS ===
    "importlib_bypass": "import importlib\ntry:\n    importlib.import_module('os').system('id')\nexcept Exception as e: print(f'Blocked: {e}')",
    "dunder_import": "__import__('os').system('id')",
}


from argon2 import PasswordHasher

ph = PasswordHasher()


@pytest.fixture(autouse=True)
def setup_pypam(monkeypatch):
    # Set a reasonable timeout for testing
    monkeypatch.setenv("EXECUTION_TIMEOUT", "10")
    importlib.reload(pypam)

    with open(pypam.ALLOWLIST_FILE, "w") as f:
        f.write(f"testuser:{ph.hash('testpass')}\n")
    yield
    if os.path.exists(pypam.ALLOWLIST_FILE):
        os.remove(pypam.ALLOWLIST_FILE)


@pytest.mark.parametrize("test_name", HARDENING_TESTS.keys())
def test_security_hardening_scenarios(test_name):
    code = HARDENING_TESTS[test_name]
    client = TestClient(pypam.app)

    # Login to establish session
    login_res = client.post(
        "/login", json={"username": "testuser", "password": "testpass"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["success"] == True

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"code": code})

        outputs = []
        exit_code = None
        while True:
            try:
                data = websocket.receive_json()
                if data["t"] == "out":
                    outputs.append(data["d"])
                elif data["t"] == "end":
                    exit_code = data["c"]
                    break
            except Exception:
                break

        output_str = "".join(outputs)

        # Validation based on the scenario
        if "mem_bomb" in test_name:
            # Should be OOM Killed (137) or hit Python MemoryError (1)
            assert exit_code in [1, 137]
        elif "cpu_infinite" in test_name:
            # Should hit timeout
            assert "[Execution Timeout]" in output_str
            assert exit_code == 137
        elif "fork_bomb" in test_name:
            # Should hit PID limit (15)
            assert exit_code in [0, 1, 137]
        elif "read_shadow" in test_name:
            # Must NOT see actual shadow content
            assert "Permission Denied" in output_str
            assert "root:" not in output_str
        elif "write_system" in test_name:
            # Must NOT be able to write to /etc
            assert "Read-only FS" in output_str or "Permission denied" in output_str
        elif "socket_connect" in test_name:
            # Must fail due to network_disabled=True
            assert (
                "Network Blocked" in output_str
                or "Network is unreachable" in output_str
            )
        elif "os_system" in test_name:
            # Should run as 'nobody' (UID 65534)
            assert "uid=65534(nobody)" in output_str
            # And it should NOT be root
            assert "uid=0(root)" not in output_str
        elif "read_passwd" in test_name:
            # Reading /etc/passwd is allowed in Alpine, but it's the container's
            assert "root:x:0:0:root" in output_str
            assert "ubuntu" not in output_str
