# PyPAM - Online Python Editor

A secure, web-based Python environment for students to write and execute code in isolated Docker containers.

## 🚀 Deployment Instructions

### 1. Prerequisites (Ubuntu/Debian)
Ensure Docker is installed and your user has permissions:
```bash
sudo apt update && sudo apt install docker.io -y
sudo usermod -aG docker $USER
# Log out and log back in for group changes to take effect
```

### 2. Initial Setup
Clone the repository and prepare the virtual environment:
```bash
cd /home/ubuntu/pypam
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

### 4. GitHub Synchronization
Once you create the remote repository at GitHub, sync your local repo using:
```bash
git remote add origin http://github.com/ltdufpb/pypam.git
git branch -M main
git push -u origin main
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
- **File System**: Root filesystem is read-only; `/tmp` is non-persistent.
