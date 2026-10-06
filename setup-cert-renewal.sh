#!/bin/bash
# Configures automatic renewal of the PyPAM Let's Encrypt certificate.
# Run on the server as root, after issue-cert.sh: sudo ./setup-cert-renewal.sh

set -euo pipefail

. "$(dirname "$0")/https-common.sh"

require_root
load_config
require_domain

RENEWAL_CONF="/etc/letsencrypt/renewal/$CERT_NAME.conf"

[ -f "$RENEWAL_CONF" ] || die "$RENEWAL_CONF not found. Run issue-cert.sh first."
if ! grep -Eq '^\s*authenticator\s*=\s*webroot' "$RENEWAL_CONF"; then
    die "$RENEWAL_CONF does not use the webroot authenticator, so renewal would fail while nginx is running. Run issue-cert.sh first."
fi

echo "==> Enabling the certbot renewal timer..."
if systemctl cat certbot.timer > /dev/null 2>&1; then
    systemctl enable --now certbot.timer
elif systemctl cat snap.certbot.renew.timer > /dev/null 2>&1; then
    systemctl enable --now snap.certbot.renew.timer
else
    echo "    No certbot systemd timer found, installing a cron job instead."
    cat > /etc/cron.d/certbot-pypam << 'CRON'
# Renew Let's Encrypt certificates twice a day (certbot only renews when < 30 days left)
17 3,15 * * * root certbot renew -q
CRON
fi

echo "==> Installing hook to reload nginx after each renewal..."
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh << 'HOOK'
#!/bin/bash
systemctl reload nginx
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

# Older setups obtained a certificate for the same domain with the standalone
# authenticator, whose renewal always fails while nginx holds port 80. Remove such
# lineages once nginx no longer uses them.
echo "==> Looking for obsolete certificates for $DOMAIN..."
for conf in /etc/letsencrypt/renewal/*.conf; do
    [ -f "$conf" ] || continue
    name="$(basename "$conf" .conf)"
    [ "$name" = "$CERT_NAME" ] && continue
    grep -Eq '^\s*authenticator\s*=\s*standalone' "$conf" || continue
    cert="/etc/letsencrypt/live/$name/cert.pem"
    [ -f "$cert" ] || continue
    openssl x509 -in "$cert" -noout -ext subjectAltName 2> /dev/null | grep -Eq "DNS:${DOMAIN//./\\.}(,|$)" || continue
    if grep -rqs "/etc/letsencrypt/live/$name/" /etc/nginx; then
        echo "    WARNING: obsolete certificate '$name' is still referenced by nginx; not removing it."
        continue
    fi
    echo "    Removing obsolete standalone certificate '$name'..."
    certbot delete --cert-name "$name" --non-interactive
done

echo "==> Testing renewal (dry run against the Let's Encrypt staging server)..."
certbot renew --dry-run --cert-name "$CERT_NAME"

echo ""
systemctl list-timers --all 'certbot*' 'snap.certbot*' 2> /dev/null || true
echo ""
echo "Automatic renewal is configured."
