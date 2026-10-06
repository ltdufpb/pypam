#!/bin/bash
# Setup script for PyPAM HTTPS with nginx and Let's Encrypt.
# Run this on the server as root (or with sudo), after creating https.conf:
#   cp https.conf.example https.conf   # then set DOMAIN and EMAIL
#   sudo ./setup-https.sh [your-email@example.com]
# Safe to re-run: it also migrates older installations to the current setup.

set -euo pipefail

. "$(dirname "$0")/https-common.sh"

require_root
load_config
EMAIL="${1:-$EMAIL}"
require_domain
require_email
export DOMAIN EMAIL

echo "==> Installing nginx and certbot..."
apt-get update
apt-get install -y nginx certbot

echo "==> Writing nginx server_name for $DOMAIN..."
write_server_name_snippet

"$SCRIPT_DIR/issue-cert.sh" "$EMAIL"

echo "==> Installing nginx configuration..."
cp "$SCRIPT_DIR/nginx/pypam.conf" /etc/nginx/sites-available/pypam
ln -sf /etc/nginx/sites-available/pypam /etc/nginx/sites-enabled/pypam

echo "==> Testing nginx configuration..."
nginx -t

echo "==> Starting nginx..."
systemctl enable nginx
if systemctl is-active --quiet nginx; then
    systemctl reload nginx
else
    systemctl start nginx
fi

"$SCRIPT_DIR/setup-cert-renewal.sh"

echo "==> Restarting PyPAM service..."
systemctl restart pypam

echo ""
echo "==> Checking the certificate in use..."
"$SCRIPT_DIR/check-cert.sh"

echo ""
echo "Done! PyPAM is now available at:"
echo "  https://$DOMAIN"
