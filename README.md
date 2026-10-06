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
PyPAM uses **nginx** as a reverse proxy for SSL termination with free **Let's Encrypt** certificates.

> **Before you start:** the domain must point to the server, and ports **80** and **443** must be open. On **Oracle Cloud**, open them in **both** the subnet's Security List **and** the instance firewall (OCI Ubuntu images block them in `/etc/iptables/rules.v4`). Port 80 must **stay** open: Let's Encrypt uses it to validate every renewal.

#### Configure the domain
The domain is never committed to the repository. It lives in `https.conf`, which is git-ignored:
```bash
cp https.conf.example https.conf
nano https.conf   # set DOMAIN=example.com and EMAIL=you@example.com
```
`DOMAIN` and `EMAIL` can also be overridden with environment variables (`sudo DOMAIN=example.com ./setup-https.sh`).

#### Run the setup
```bash
sudo ./setup-https.sh
```

This will:
- Install nginx and certbot
- Obtain the certificate (`issue-cert.sh`)
- Install `nginx/pypam.conf`, which proxies HTTP (port 80) → HTTPS (port 443) → PyPAM (port 8000)
- Set up automatic certificate renewal (`setup-cert-renewal.sh`)
- Verify the certificate being served (`check-cert.sh`)

After running the script, reload the systemd service to enable secure session cookies:
```bash
sudo systemctl daemon-reload
sudo systemctl restart pypam
```

The script is idempotent: re-run it whenever you change `https.conf`.

#### Certificate scripts
`setup-https.sh` runs these scripts in order. Each one can also be run on its own:

| Script | What it does |
| :--- | :--- |
| `sudo ./issue-cert.sh` | Obtains the certificate for `DOMAIN` with certbot's **webroot** method. nginx keeps running and serves the challenge from `/var/www/certbot`. Before the first certificate exists, it temporarily enables `nginx/acme-bootstrap.conf`. The certificate is stored in `/etc/letsencrypt/live/pypam/`, whatever the domain is. |
| `sudo ./setup-cert-renewal.sh` | Enables automatic renewal, installs the hook that reloads nginx after a renewal, removes obsolete certificates from older setups, and runs a renewal dry run. |
| `./check-cert.sh [DOMAIN] [MIN_DAYS]` | Checks the certificate actually served on port 443 (see below). |

nginx gets the domain only from the generated file `/etc/nginx/snippets/pypam-server-name.conf` (`server_name <DOMAIN>;`). `nginx/pypam.conf` itself contains no domain, and the site is matched by name, so other sites on the same nginx are not affected.

#### Automatic certificate renewal
Let's Encrypt certificates are valid for 90 days. Renewal works like this:
1. The `certbot.timer` systemd timer runs `certbot renew` twice a day. If certbot came from snap, `snap.certbot.renew.timer` does this instead; if no timer exists, the cron job `/etc/cron.d/certbot-pypam` is installed.
2. certbot renews the certificate once it has fewer than 30 days left, using the webroot method saved in `/etc/letsencrypt/renewal/pypam.conf`. nginx does **not** need to be stopped.
3. The deploy hook `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` reloads nginx, so it serves the new certificate.

To confirm renewal is set up and works:
```bash
systemctl list-timers 'certbot*'       # next scheduled run
sudo certbot renew --dry-run           # full renewal test against the staging server
sudo certbot certificates              # certificates and expiry dates
sudo journalctl -u certbot --since "7 days ago"   # renewal logs
```

> **Why the certificate used to expire:** older versions of `setup-https.sh` obtained it with certbot's `standalone` method. certbot reuses that method on every renewal, and it needs port 80 free. Since nginx is always on port 80, every automatic renewal failed silently.

#### Verifying the certificate
`check-cert.sh` connects to the server like a browser would and checks the certificate it is actually served. It works from the server or from any machine with `openssl`:
```bash
./check-cert.sh                  # uses DOMAIN from https.conf
./check-cert.sh example.com 30   # explicit domain; fail if less than 30 days left
```
It prints the subject, issuer and validity dates, followed by `OK`/`FAIL` lines for these checks:
- the chain is trusted and matches the hostname;
- the certificate is not expired and expires in more than `MIN_DAYS` (default 14);
- **only when run as root on the server:** nginx serves the same certificate as `/etc/letsencrypt/live/pypam/` (i.e. it was reloaded after renewal), and a renewal timer or cron job exists.

The exit status is `0` when everything is OK and `1` otherwise. Let's Encrypt no longer e-mails expiry warnings, so you may want a weekly check whose failures show up in the system mail/logs:
```bash
# /etc/cron.d/pypam-check-cert
0 8 * * 1 root /home/ubuntu/pypam/check-cert.sh > /dev/null || logger -t pypam "TLS certificate check FAILED"
```
Without the script, you can also check the dates with `echo | openssl s_client -connect example.com:443 -servername example.com 2>/dev/null | openssl x509 -noout -dates`.

#### Migrating an existing server
Servers set up with an older version of `setup-https.sh` use the `standalone` method, so their certificate will not renew. To migrate (once):
```bash
cd pypam
git pull
cp https.conf.example https.conf   # set DOMAIN to the server's current domain, and EMAIL
sudo ./setup-https.sh
./check-cert.sh
```
This issues a new certificate with the webroot method (stored as `pypam`), switches nginx to the new configuration, and removes the old certificate. Afterwards, `sudo certbot certificates` should list only `pypam`.

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
| **Check Certificate** | `sudo ./check-cert.sh` |
| **List Certificates** | `sudo certbot certificates` |
| **Renewal Timer** | `systemctl list-timers 'certbot*'` |
| **Test Renewal** | `sudo certbot renew --dry-run` |

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
