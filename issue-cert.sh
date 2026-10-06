#!/bin/bash
# Obtains (or re-issues) the Let's Encrypt certificate for PyPAM using the webroot
# method, so later renewals work while nginx keeps running.
# Run on the server as root: sudo ./issue-cert.sh [your-email@example.com]

set -euo pipefail

. "$(dirname "$0")/https-common.sh"

require_root
load_config
EMAIL="${1:-$EMAIL}"
require_domain
require_email

BOOTSTRAP_CONF="/etc/nginx/sites-enabled/pypam-acme-bootstrap.conf"
PYPAM_SITE="/etc/nginx/sites-enabled/pypam"
RESTORE_PYPAM_SITE=""
BOOTSTRAPPED=""

if ! command -v nginx > /dev/null || ! command -v certbot > /dev/null; then
    echo "==> Installing nginx and certbot..."
    apt-get update
    apt-get install -y nginx certbot
fi

echo "==> Creating ACME challenge directory..."
mkdir -p "$WEBROOT"
write_server_name_snippet

cleanup_bootstrap() {
    rm -f "$BOOTSTRAP_CONF"
    if [ -n "$RESTORE_PYPAM_SITE" ]; then
        ln -sf /etc/nginx/sites-available/pypam "$PYPAM_SITE"
    fi
    if nginx -t -q 2> /dev/null; then
        systemctl reload nginx || true
    fi
}

if [ -e "$PYPAM_SITE" ] && nginx -t -q 2> /dev/null; then
    echo "==> Using the existing PyPAM nginx site to answer the ACME challenge..."
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
    else
        systemctl start nginx
    fi
else
    echo "==> Enabling temporary nginx site for the ACME challenge..."
    if [ -e "$PYPAM_SITE" ]; then
        # The PyPAM site can't load yet (certificate missing): disable it meanwhile.
        rm -f "$PYPAM_SITE"
        RESTORE_PYPAM_SITE=1
    fi
    BOOTSTRAPPED=1
    trap cleanup_bootstrap EXIT
    cp "$SCRIPT_DIR/nginx/acme-bootstrap.conf" "$BOOTSTRAP_CONF"
    nginx -t
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
    else
        systemctl start nginx
    fi
fi

echo "==> Obtaining certificate for $DOMAIN from Let's Encrypt..."
certbot certonly --webroot \
    -w "$WEBROOT" \
    --cert-name "$CERT_NAME" \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --keep-until-expiring

# Pick up the new certificate (in bootstrap mode, cleanup_bootstrap does it on exit)
if [ -z "$BOOTSTRAPPED" ] && systemctl is-active --quiet nginx; then
    systemctl reload nginx
fi

echo ""
echo "Certificate files: /etc/letsencrypt/live/$CERT_NAME/"
