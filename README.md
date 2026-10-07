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

### 3. Initial Configuration
PyPAM requires an administrator account and a student list. All passwords must be hashed before being placed in the final configuration files.

#### Create Administrator
Use the hashing filter to create the `admin.txt` file. **Use single quotes** to prevent your shell from interpreting special characters (like `$` or `&`):
```bash
echo 'admin:your_complex_password_here' | python3 hash_passwords.py > admin.txt
```

#### Create Student List
If you have a table of students (e.g., `students_table.txt` where the 2nd column is the ID), use the pipe-and-filter workflow:
```bash
cat students_table.txt | python3 create_student_passwords.py | python3 hash_passwords.py > students.txt
```
This parses the IDs, generates passwords (ID reversed), hashes them, and saves the result to `students.txt`. All files follow the `user:pass_or_hash` colon-separated format.

### 4. Install the Systemd Service
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

### 5. Enable HTTPS (Recommended)
PyPAM uses **nginx** with a free **Let's Encrypt** certificate.

#### Before you start
- Point your domain to the server.
- Open ports **80** and **443**, and keep port 80 open: certificate renewals use it.
- On **Oracle Cloud**, allow both ports in the subnet's Security List. Then run `sudo iptables -S INPUT` on the server. If the output has a `REJECT` rule, open the ports:
  ```bash
  sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT
  ```
  To keep this after a reboot, add `-A INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT` to `/etc/iptables/rules.v4`, above the `REJECT` line.

#### Configure
Copy `cert.conf.example` to `cert.conf`, and edit `cert.conf` to set `DOMAIN` and `EMAIL`:
```bash
cp cert.conf.example cert.conf
nano cert.conf
```

#### Install
```bash
sudo ./cert.sh install
sudo systemctl daemon-reload
sudo systemctl restart pypam
```
This installs nginx and certbot, gets the certificate, configures nginx and automatic renewal, and checks the result.

To reinstall, or after changing `cert.conf`, run `sudo ./cert.sh install --force`.

#### Commands
| Command | What it does |
| :--- | :--- |
| `sudo ./cert.sh install` | Sets up HTTPS. Add `--force` to reinstall. |
| `sudo ./cert.sh renew` | Renews the certificate if it expires in less than 30 days. Add `--force` to renew anyway, or `--dry-run` to only test. |
| `sudo ./cert.sh check` | Checks the certificate the server is using. Add `--min-days 30` to fail if it expires in less than 30 days (default: 14). |
| `./cert.sh help` | Shows all commands. `./cert.sh help <command>` shows the options of one command. |

#### Renewal
Renewal is automatic: certbot checks twice a day and renews the certificate when it has less than 30 days left. To check that it works:
```bash
sudo ./cert.sh check            # certificate in use, expiry date and renewal schedule
sudo ./cert.sh renew --dry-run  # tests a renewal
```

Let's Encrypt no longer sends expiry e-mails. To be warned in the system log, add a weekly check in `/etc/cron.d/pypam-check-cert`:
```
0 8 * * 1 root /home/ubuntu/pypam/cert.sh check > /dev/null || logger -t pypam "TLS certificate check FAILED"
```

#### Migrating from `setup-https.sh`
On servers set up with the old `setup-https.sh`, the certificate does not renew. Fix it once:
```bash
cd pypam
git pull
cp cert.conf.example cert.conf   # then set DOMAIN (the current domain) and EMAIL
sudo ./cert.sh install
```
Afterwards, `sudo certbot certificates` should list a single certificate, named `pypam`.

---

## 🔄 Updating PyPAM

To update a running instance to the latest version:

```bash
cd pypam
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart pypam
```

If the server was set up with the old `setup-https.sh`, also [migrate it to `cert.sh`](#migrating-from-setup-httpssh) once.

---

## 🎓 New Term: Updating the Student Roster

At the start of each new term, replace the live student list with the new cohort. Last term's students will lose access; the new cohort will be able to log in immediately — **no service restart needed** (PyPAM re-reads `students.txt` on every login).

### 1. Obtain the new term's roster
Save the roster from SIGAA as `students_table.txt` in your local clone, with the student ID in the **2nd column** (existing convention).

### 2. Regenerate `students.txt` locally
```bash
cat students_table.txt | python3 create_student_passwords.py | python3 hash_passwords.py > students.txt
```

### 3. Transfer the file to the live server
```bash
scp students.txt ubuntu@<HOST>:/tmp/students.txt.new
```

### 4. Back up and atomically swap on the live server
```bash
ssh ubuntu@<HOST>
cd /home/ubuntu/pypam
cp students.txt students.txt.bak.$(date +%Y%m%d-%H%M%S)
mv /tmp/students.txt.new students.txt
wc -l students.txt   # should match the local file's line count
```

The backup (`students.txt.bak.YYYYMMDD-HHMMSS`) is your rollback if anything looks wrong — `mv students.txt.bak.<timestamp> students.txt` restores it.

### 5. Verify
Log in to PyPAM as one of the newly enrolled student IDs (password = ID reversed) to confirm the swap took effect.

### Adding only a few students
If you just need to add one or two students mid-term (rather than rolling over the whole cohort), log in to the admin portal at `/admin` and use the **"+ NOVO"** button — it writes to the same `students.txt` file.

---

## 🛠️ Management Commands

| Action | Command |
| :--- | :--- |
| **Check Health** | `sudo systemctl status pypam` |
| **View Live Logs** | `sudo journalctl -u pypam -f` |
| **Restart App** | `sudo systemctl restart pypam` |
| **Stop App** | `sudo systemctl stop pypam` |
| **View Crash Logs** | `sudo journalctl -u pypam --since "1 hour ago"` |
| **Check Certificate** | `sudo ./cert.sh check` |
| **List Certificates** | `sudo certbot certificates` |
| **Renewal Timer** | `systemctl list-timers 'certbot*'` |
| **Test Renewal** | `sudo ./cert.sh renew --dry-run` |
| **Renew Now** | `sudo ./cert.sh renew --force` |

---

## 🔒 Security Features
- **HTTPS**: TLS encryption via nginx + Let's Encrypt with automatic certificate renewal.
- **Secure Cookies**: Session cookies are marked `https_only` when `HTTPS_ENABLED=true`.
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
