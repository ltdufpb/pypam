#!/bin/bash
# Checks that the TLS certificate served by PyPAM is valid: trusted chain, matching
# hostname, and not expiring soon. Can run anywhere with openssl (server or laptop).
# When run as root on the server, also checks that nginx serves the latest certificate
# on disk and shows the renewal timer.
#
# Usage: ./check-cert.sh [DOMAIN] [MIN_DAYS]
#   DOMAIN    defaults to DOMAIN in https.conf
#   MIN_DAYS  fail if the certificate expires in fewer days (default: 14)
#
# Exit status: 0 if everything is OK, 1 otherwise (suitable for cron/monitoring).

set -euo pipefail

. "$(dirname "$0")/https-common.sh"

load_config
DOMAIN="${1:-$DOMAIN}"
MIN_DAYS="${2:-14}"
PORT="${PORT:-443}"
# An explicit DOMAIN argument may be any host; only the configured one must be set.
[ -n "${1:-}" ] || require_domain
command -v openssl > /dev/null || die "openssl is not installed."

FAILED=0
ok() { echo "OK    $*"; }
fail() { echo "FAIL  $*"; FAILED=1; }

echo "Checking https://$DOMAIN:$PORT ..."
echo ""

# Bound the connection time when `timeout` exists (it isn't installed by default on macOS)
TIMEOUT=()
command -v timeout > /dev/null && TIMEOUT=(timeout 20)

handshake="$(echo | ${TIMEOUT[@]+"${TIMEOUT[@]}"} openssl s_client -connect "$DOMAIN:$PORT" -servername "$DOMAIN" \
    -verify_hostname "$DOMAIN" -showcerts 2> /dev/null || true)"
served_cert="$(echo "$handshake" | openssl x509 2> /dev/null || true)"

if [ -z "$served_cert" ]; then
    fail "could not retrieve a certificate from $DOMAIN:$PORT (DNS, firewall or nginx down?)"
    exit 1
fi

echo "$served_cert" | openssl x509 -noout -subject -issuer -startdate -enddate | sed 's/^/      /'
echo "$served_cert" | openssl x509 -noout -ext subjectAltName 2> /dev/null | sed -n 's/^ *\(DNS:.*\)/      SAN: \1/p' || true
echo ""

verify_line="$(echo "$handshake" | grep -m1 'Verify return code:' | sed 's/^ *//' || true)"
if echo "$verify_line" | grep -q 'Verify return code: 0 '; then
    ok "certificate chain is trusted and matches hostname $DOMAIN"
else
    fail "certificate verification failed: ${verify_line:-no verification result}"
fi

end_date="$(echo "$served_cert" | openssl x509 -noout -enddate | cut -d= -f2)"
end_epoch="$(date -d "$end_date" +%s 2> /dev/null || date -j -f '%b %e %T %Y %Z' "$end_date" +%s 2> /dev/null || echo "")"
if [ -n "$end_epoch" ]; then
    days_left=$(( (end_epoch - $(date +%s)) / 86400 ))
    days_msg="$days_left days left, expires $end_date"
else
    days_msg="expires $end_date"
fi

if ! echo "$served_cert" | openssl x509 -noout -checkend 0 > /dev/null; then
    fail "certificate has EXPIRED ($end_date)"
elif ! echo "$served_cert" | openssl x509 -noout -checkend $(( MIN_DAYS * 86400 )) > /dev/null; then
    fail "certificate expires in less than $MIN_DAYS days ($days_msg); is automatic renewal working?"
else
    ok "certificate is valid for at least $MIN_DAYS more days ($days_msg)"
fi

# Server-side checks (only when the certificate files are readable, i.e. as root on the server)
LOCAL_CERT="/etc/letsencrypt/live/$CERT_NAME/cert.pem"
if [ -r "$LOCAL_CERT" ]; then
    served_fp="$(echo "$served_cert" | openssl x509 -noout -fingerprint -sha256)"
    local_fp="$(openssl x509 -in "$LOCAL_CERT" -noout -fingerprint -sha256)"
    if [ "$served_fp" = "$local_fp" ]; then
        ok "nginx is serving the current certificate from $LOCAL_CERT"
    else
        fail "nginx is serving an older certificate than $LOCAL_CERT; run: sudo systemctl reload nginx"
    fi

    if systemctl is-active --quiet certbot.timer 2> /dev/null \
        || systemctl is-active --quiet snap.certbot.renew.timer 2> /dev/null \
        || [ -f /etc/cron.d/certbot-pypam ]; then
        ok "automatic renewal is scheduled"
    else
        fail "no certbot renewal timer or cron job found; run: sudo ./setup-cert-renewal.sh"
    fi
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "Certificate for $DOMAIN is valid."
else
    echo "Certificate for $DOMAIN has problems (see FAIL lines above)."
fi
exit "$FAILED"
