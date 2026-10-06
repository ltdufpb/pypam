#!/bin/bash
# shellcheck disable=SC2034  # variables are used by the scripts that source this file
# Shared helpers for the PyPAM HTTPS scripts. Source it, don't run it.
#
# Configuration priority: command-line argument > environment variable > https.conf

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTTPS_CONF="${HTTPS_CONF:-$SCRIPT_DIR/https.conf}"

# Certbot lineage name: certificate files live in /etc/letsencrypt/live/$CERT_NAME/
CERT_NAME="pypam"
WEBROOT="/var/www/certbot"
SERVER_NAME_SNIPPET="/etc/nginx/snippets/pypam-server-name.conf"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

load_config() {
    local env_domain="${DOMAIN:-}" env_email="${EMAIL:-}"
    if [ -f "$HTTPS_CONF" ]; then
        # shellcheck source=https.conf.example
        . "$HTTPS_CONF"
    fi
    DOMAIN="${env_domain:-${DOMAIN:-}}"
    EMAIL="${env_email:-${EMAIL:-}}"
}

require_root() {
    [ "$(id -u)" -eq 0 ] || die "this script must be run as root (use sudo)."
}

require_domain() {
    if [ -z "${DOMAIN:-}" ] || [ "$DOMAIN" = "example.com" ]; then
        die "DOMAIN is not set. Run 'cp https.conf.example https.conf' and set DOMAIN (or export DOMAIN=...)."
    fi
}

require_email() {
    if [ -z "${EMAIL:-}" ] || [ "$EMAIL" = "you@example.com" ]; then
        die "EMAIL is not set. Set EMAIL in https.conf or pass it as the first argument."
    fi
}

# Writes the only domain-specific piece of the nginx configuration.
write_server_name_snippet() {
    mkdir -p "$(dirname "$SERVER_NAME_SNIPPET")"
    echo "server_name $DOMAIN;" > "$SERVER_NAME_SNIPPET"
}
