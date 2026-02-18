# PyPAM - Online Python Editor

A secure, web-based Python environment for students to write and execute code in isolated Docker containers.

## 🚀 Deployment Instructions

### 1. Prerequisites (Ubuntu/Debian)
1. Install **Docker Engine** by following the official documentation: [https://docs.docker.com/engine/install/](https://docs.docker.com/engine/install/)
2. Configure user permissions so you can run Docker without sudo:
```bash
sudo usermod -aG docker $USER
# Log out and log back in for group changes to take effect
```

### 2. Initial Setup
Clone the repository and prepare the virtual environment:
```bash
git clone https://github.com/ltdufpb/pypam.git
cd pypam
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Install the Systemd Service
The service ensures the app starts on boot and restarts automatically if it crashes.
```bash
# Copy the service file to the system directory
sudo cp pypam.service /etc/systemd/system/

# Reload systemd and enable the service
sudo systemctl daemon-reload
sudo systemctl enable pypam

# Start the service
sudo systemctl start pypam
```

---

## 🛠️ Management Commands

| Action | Command |
| :--- | :--- |
| **Check Health** | `sudo systemctl status pypam` |
| **View Live Logs** | `sudo journalctl -u pypam -f` |
| **Restart App** | `sudo systemctl restart pypam` |
| **Stop App** | `sudo systemctl stop pypam` |
| **View Crash Logs** | `sudo journalctl -u pypam --since "1 hour ago"` |

---

## 🔒 Security Features
- **Container Isolation**: Students run inside Docker `python:alpine` containers.
- **Resource Caps**: Limited to 48MB RAM and 20% CPU.
- **Network Disabled**: Containers have no internet/LAN access.
- **Unprivileged User**: Code runs as `nobody`, preventing host escalation.
- **File System**: Root filesystem is read-only.
- **Disk Usage Limits**: Writable areas (`/app` and `/tmp`) are limited to 10MB via `tmpfs` to prevent host disk exhaustion.

---

## 🧪 Testing

PyPAM includes an automated test suite to verify both API logic and container security isolation.

```bash
# Enter the virtual environment
source .venv/bin/activate

# Run all tests
pytest -v tests/
```

Manual security payloads for testing via the UI can be found in the `tests/payloads/` directory.
