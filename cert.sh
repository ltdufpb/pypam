#!/bin/bash
# Manages the PyPAM TLS certificate (Let's Encrypt) and the nginx setup that serves it.
# Supported systems: Debian 13+ and Ubuntu 24.04+.
#
# Run './cert.sh help' for the commands and their options.

set -euo pipefail

PROG="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Certbot lineage name: certificate files live in /etc/letsencrypt/live/$CERT_NAME/
CERT_NAME="pypam"
WEBROOT="/var/www/certbot"
SERVER_NAME_SNIPPET="/etc/nginx/snippets/pypam-server-name.conf"
RENEWAL_CONF="/etc/letsencrypt/renewal/$CERT_NAME.conf"
LOCAL_CERT="/etc/letsencrypt/live/$CERT_NAME/cert.pem"
PYPAM_SITE="/etc/nginx/sites-enabled/pypam"
BOOTSTRAP_SITE="/etc/nginx/sites-enabled/pypam-acme-bootstrap.conf"
CRON_FILE="/etc/cron.d/certbot-pypam"
DEPLOY_HOOK="/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh"

# DOMAIN and EMAIL are read only from the config file (never from options or the environment)
CONFIG_FILE="$SCRIPT_DIR/cert.conf"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die() {
    echo "ERROR: $*" >&2
    exit 1
}

usage_error() {
    local cmd="$1"
    shift
    if [ $# -gt 0 ]; then
        echo "$PROG: $*" >&2
    fi
    echo "Try '$PROG help${cmd:+ $cmd}'." >&2
    exit 2
}

# Reads the config file; stops if it is missing or does not set DOMAIN and EMAIL.
load_config() {
    [ -f "$CONFIG_FILE" ] \
        || die "config file not found: $CONFIG_FILE. Run 'cp cert.conf.example cert.conf' and set DOMAIN and EMAIL."
    # Ignore DOMAIN/EMAIL inherited from the environment
    DOMAIN=""
    EMAIL=""
    # shellcheck source=cert.conf.example
    . "$CONFIG_FILE"
    require_domain
    require_email
}

require_root() {
    [ "$(id -u)" -eq 0 ] || die "this command must be run as root (use sudo)."
}

require_domain() {
    if [ -z "${DOMAIN:-}" ] || [ "$DOMAIN" = "example.com" ]; then
        die "DOMAIN is not set in $CONFIG_FILE."
    fi
}

require_email() {
    if [ -z "${EMAIL:-}" ] || [ "$EMAIL" = "you@example.com" ]; then
        die "EMAIL is not set in $CONFIG_FILE."
    fi
}

is_installed() {
    [ -f "$RENEWAL_CONF" ]
}

# Prints the first domain of the installed certificate (readable by root only).
installed_domain() {
    openssl x509 -in "$LOCAL_CERT" -noout -ext subjectAltName 2> /dev/null \
        | grep -o 'DNS:[^,[:space:]]*' | head -n1 | cut -d: -f2 || true
}

reload_or_start_nginx() {
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
    else
        systemctl start nginx
    fi
}

# Writes the only domain-specific piece of the nginx configuration.
write_server_name_snippet() {
    mkdir -p "$(dirname "$SERVER_NAME_SNIPPET")"
    echo "server_name $DOMAIN;" > "$SERVER_NAME_SNIPPET"
}

# ---------------------------------------------------------------------------
# Installation steps
# ---------------------------------------------------------------------------

install_packages() {
    if ! command -v nginx > /dev/null || ! command -v certbot > /dev/null; then
        echo "==> Installing nginx and certbot..."
        apt-get update
        apt-get install -y nginx certbot
    fi
}

RESTORE_PYPAM_SITE=""

cleanup_bootstrap() {
    rm -f "$BOOTSTRAP_SITE"
    if [ -n "$RESTORE_PYPAM_SITE" ]; then
        ln -sf /etc/nginx/sites-available/pypam "$PYPAM_SITE"
        RESTORE_PYPAM_SITE=""
    fi
    if nginx -t -q 2> /dev/null; then
        systemctl reload nginx || true
    fi
}

# Obtains the certificate with the webroot method, so later renewals work while nginx
# keeps running. With "force", requests a new certificate even if the current one is valid.
issue_certificate() {
    local force="$1" bootstrapped=""

    echo "==> Creating ACME challenge directory..."
    mkdir -p "$WEBROOT"

    if [ -e "$PYPAM_SITE" ] && nginx -t -q 2> /dev/null; then
        echo "==> Using the existing PyPAM nginx site to answer the ACME challenge..."
        reload_or_start_nginx
    else
        echo "==> Enabling temporary nginx site for the ACME challenge..."
        if [ -e "$PYPAM_SITE" ]; then
            # The PyPAM site can't load yet (certificate missing): disable it meanwhile.
            rm -f "$PYPAM_SITE"
            RESTORE_PYPAM_SITE=1
        fi
        bootstrapped=1
        trap cleanup_bootstrap EXIT
        cp "$SCRIPT_DIR/nginx/acme-bootstrap.conf" "$BOOTSTRAP_SITE"
        nginx -t
        reload_or_start_nginx
    fi

    local renew_mode="--keep-until-expiring"
    [ -z "$force" ] || renew_mode="--force-renewal"

    echo "==> Obtaining certificate for $DOMAIN from Let's Encrypt..."
    certbot certonly --webroot \
        -w "$WEBROOT" \
        --cert-name "$CERT_NAME" \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --email "$EMAIL" \
        "$renew_mode" \
        --renew-with-new-domains

    if [ -n "$bootstrapped" ]; then
        cleanup_bootstrap
        trap - EXIT
    elif systemctl is-active --quiet nginx; then
        systemctl reload nginx
    fi
    echo "    Certificate files: /etc/letsencrypt/live/$CERT_NAME/"
}

install_nginx_site() {
    echo "==> Installing nginx configuration..."
    cp "$SCRIPT_DIR/nginx/pypam.conf" /etc/nginx/sites-available/pypam
    ln -sf /etc/nginx/sites-available/pypam "$PYPAM_SITE"
    nginx -t
    systemctl enable nginx
    reload_or_start_nginx
}

setup_renewal() {
    if ! grep -Eq '^\s*authenticator\s*=\s*webroot' "$RENEWAL_CONF"; then
        die "$RENEWAL_CONF does not use the webroot authenticator, so renewal would fail while nginx is running."
    fi

    echo "==> Enabling the certbot renewal timer..."
    if systemctl cat certbot.timer > /dev/null 2>&1; then
        systemctl enable --now certbot.timer
    elif systemctl cat snap.certbot.renew.timer > /dev/null 2>&1; then
        systemctl enable --now snap.certbot.renew.timer
    else
        echo "    No certbot systemd timer found, installing a cron job instead."
        cat > "$CRON_FILE" << 'CRON'
# Renew Let's Encrypt certificates twice a day (certbot only renews when < 30 days left)
17 3,15 * * * root certbot renew -q
CRON
    fi

    echo "==> Installing hook to reload nginx after each renewal..."
    mkdir -p "$(dirname "$DEPLOY_HOOK")"
    cat > "$DEPLOY_HOOK" << 'HOOK'
#!/bin/bash
systemctl reload nginx
HOOK
    chmod +x "$DEPLOY_HOOK"

    # Older setups obtained a certificate for the same domain with the standalone
    # authenticator, whose renewal always fails while nginx holds port 80. Remove such
    # lineages once nginx no longer uses them.
    echo "==> Looking for obsolete certificates for $DOMAIN..."
    local conf name cert
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
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_install() {
    local opts force="" no_check=""
    opts="$(getopt -n "$PROG install" -o c:fh -l config:,force,no-check,help -- "$@")" \
        || usage_error install
    eval set -- "$opts"
    while true; do
        case "$1" in
            -c | --config) CONFIG_FILE="$2"; shift 2 ;;
            -f | --force) force=1; shift ;;
            --no-check) no_check=1; shift ;;
            -h | --help) cmd_help install; return 0 ;;
            --) shift; break ;;
        esac
    done
    [ $# -eq 0 ] || usage_error install "unexpected argument: $1"

    load_config
    require_root

    if is_installed && [ -z "$force" ]; then
        die "PyPAM HTTPS is already installed for $(installed_domain). Use '$PROG install --force' to reinstall, or '$PROG renew' to renew the certificate."
    fi

    install_packages
    echo "==> Writing nginx server_name for $DOMAIN..."
    write_server_name_snippet
    issue_certificate "$force"
    install_nginx_site
    setup_renewal

    echo "==> Restarting PyPAM service..."
    systemctl restart pypam

    if [ -z "$no_check" ]; then
        echo ""
        echo "==> Checking the certificate in use..."
        cmd_check
    fi

    echo ""
    echo "Done! PyPAM is now available at: https://$DOMAIN"
}

cmd_renew() {
    local opts force="" dry_run="" no_check=""
    opts="$(getopt -n "$PROG renew" -o fnc:h -l force,dry-run,config:,no-check,help -- "$@")" \
        || usage_error renew
    eval set -- "$opts"
    while true; do
        case "$1" in
            -f | --force) force=1; shift ;;
            -n | --dry-run) dry_run=1; shift ;;
            -c | --config) CONFIG_FILE="$2"; shift 2 ;;
            --no-check) no_check=1; shift ;;
            -h | --help) cmd_help renew; return 0 ;;
            --) shift; break ;;
        esac
    done
    [ $# -eq 0 ] || usage_error renew "unexpected argument: $1"

    load_config
    require_root
    is_installed || die "the '$CERT_NAME' certificate is not installed. Run '$PROG install' first."

    local certbot_args=(renew --cert-name "$CERT_NAME")
    [ -z "$force" ] || certbot_args+=(--force-renewal)
    [ -z "$dry_run" ] || certbot_args+=(--dry-run)

    echo "==> Renewing certificate '$CERT_NAME'..."
    certbot "${certbot_args[@]}"

    if [ -z "$dry_run" ] && [ -z "$no_check" ]; then
        echo ""
        echo "==> Checking the certificate in use..."
        cmd_check --config "$CONFIG_FILE"
    fi
}

# Checks the certificate actually served on the HTTPS port: trusted chain, matching
# hostname, not expiring soon. As root on the server, also checks that nginx serves the
# certificate on disk and that renewal is scheduled. Returns 1 if any check fails.
cmd_check() {
    local opts min_days=14 port=443
    opts="$(getopt -n "$PROG check" -o m:p:c:h -l min-days:,port:,config:,help -- "$@")" \
        || usage_error check
    eval set -- "$opts"
    while true; do
        case "$1" in
            -m | --min-days) min_days="$2"; shift 2 ;;
            -p | --port) port="$2"; shift 2 ;;
            -c | --config) CONFIG_FILE="$2"; shift 2 ;;
            -h | --help) cmd_help check; return 0 ;;
            --) shift; break ;;
        esac
    done
    [ $# -eq 0 ] || usage_error check "unexpected argument: $1"
    [[ $min_days =~ ^[0-9]+$ ]] || usage_error check "--min-days must be a whole number: $min_days"
    if ! [[ $port =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        usage_error check "--port must be a number from 1 to 65535: $port"
    fi

    load_config
    local domain="$DOMAIN"
    command -v openssl > /dev/null || die "openssl is not installed."

    local failed=0
    ok() { echo "OK    $*"; }
    fail() { echo "FAIL  $*"; failed=1; }

    echo "Checking https://$domain:$port ..."
    echo ""

    local handshake served_cert
    handshake="$(echo | timeout 20 openssl s_client -connect "$domain:$port" -servername "$domain" \
        -verify_hostname "$domain" -showcerts 2> /dev/null || true)"
    served_cert="$(echo "$handshake" | openssl x509 2> /dev/null || true)"

    if [ -z "$served_cert" ]; then
        fail "could not retrieve a certificate from $domain:$port (DNS, firewall or nginx down?)"
        return 1
    fi

    echo "$served_cert" | openssl x509 -noout -subject -issuer -startdate -enddate | sed 's/^/      /'
    echo "$served_cert" | openssl x509 -noout -ext subjectAltName 2> /dev/null | sed -n 's/^ *\(DNS:.*\)/      SAN: \1/p' || true
    echo ""

    local verify_line
    verify_line="$(echo "$handshake" | grep -m1 'Verify return code:' | sed 's/^ *//' || true)"
    if [[ $verify_line == "Verify return code: 0 "* ]]; then
        ok "certificate chain is trusted and matches hostname $domain"
    else
        fail "certificate verification failed: ${verify_line:-no verification result}"
    fi

    local end_date days_left days_msg
    end_date="$(echo "$served_cert" | openssl x509 -noout -enddate | cut -d= -f2)"
    days_left=$(( ($(date -d "$end_date" +%s) - $(date +%s)) / 86400 ))
    days_msg="$days_left days left, expires $end_date"

    if ! echo "$served_cert" | openssl x509 -noout -checkend 0 > /dev/null; then
        fail "certificate has EXPIRED ($end_date)"
    elif ! echo "$served_cert" | openssl x509 -noout -checkend $(( min_days * 86400 )) > /dev/null; then
        fail "certificate expires in less than $min_days days ($days_msg); is automatic renewal working?"
    else
        ok "certificate is valid for at least $min_days more days ($days_msg)"
    fi

    # Server-side checks (only when the certificate files are readable, i.e. as root on the server)
    if [ -r "$LOCAL_CERT" ]; then
        local served_fp local_fp
        served_fp="$(echo "$served_cert" | openssl x509 -noout -fingerprint -sha256)"
        local_fp="$(openssl x509 -in "$LOCAL_CERT" -noout -fingerprint -sha256)"
        if [ "$served_fp" = "$local_fp" ]; then
            ok "nginx is serving the current certificate from $LOCAL_CERT"
        else
            fail "nginx is serving an older certificate than $LOCAL_CERT; run: sudo systemctl reload nginx"
        fi

        if systemctl is-active --quiet certbot.timer 2> /dev/null \
            || systemctl is-active --quiet snap.certbot.renew.timer 2> /dev/null \
            || [ -f "$CRON_FILE" ]; then
            ok "automatic renewal is scheduled"
        else
            fail "no certbot renewal timer or cron job found; run: sudo $PROG install --force"
        fi
    fi

    echo ""
    if [ "$failed" -eq 0 ]; then
        echo "Certificate for $domain is valid."
    else
        echo "Certificate for $domain has problems (see FAIL lines above)."
    fi
    return "$failed"
}

cmd_help() {
    case "${1:-}" in
        "")
            cat << EOF
Usage: $PROG <command> [options]

Manages the PyPAM TLS certificate (Let's Encrypt) and the nginx setup that serves it.

Commands:
  install   Install nginx and certbot, obtain the certificate, configure nginx and
            automatic renewal (run once; --force to reinstall). Requires root.
  renew     Renew the certificate now if it is due (--force: renew anyway). Requires root.
  check     Check the certificate this server serves on the HTTPS port.
  help      Show this help, or the help of a command: $PROG help <command>

The domain and e-mail are read only from cert.conf next to this script
(cp cert.conf.example cert.conf), or from the file given with --config.
Every command except help stops if that file is missing or does not set DOMAIN and EMAIL.

Run '$PROG help <command>' or '$PROG <command> --help' for the options of a command.
EOF
            ;;
        install)
            cat << EOF
Usage: sudo $PROG install [options]

Sets up HTTPS for PyPAM: installs nginx and certbot if missing, obtains the Let's Encrypt
certificate with the webroot method (stored as '$CERT_NAME'), installs nginx/pypam.conf,
configures automatic renewal, removes obsolete certificates from older setups, restarts
PyPAM and checks the certificate being served.

DOMAIN and EMAIL (for the Let's Encrypt account) are read only from the configuration file.

Refuses to run when the certificate is already installed, unless --force is given.

Options:
  -c, --config FILE     configuration file (default: cert.conf next to this script)
  -f, --force           reinstall: overwrite the nginx and renewal setup and request a
                        new certificate (also used to change the domain)
      --no-check        do not check the certificate at the end
  -h, --help            show this help

Examples:
  sudo $PROG install
  sudo $PROG install --force
EOF
            ;;
        renew)
            cat << EOF
Usage: sudo $PROG renew [options]

Renews the '$CERT_NAME' certificate. certbot only renews it when it expires in less than
30 days, unless --force is given. nginx is reloaded after a renewal. Renewal also runs
automatically twice a day, so this command is only needed to renew by hand or to test.

Options:
  -f, --force           renew even if the certificate is not due yet
  -n, --dry-run         test the renewal against the Let's Encrypt staging server,
                        without saving a certificate
  -c, --config FILE     configuration file (default: cert.conf next to this script)
      --no-check        do not check the certificate afterwards
  -h, --help            show this help

Examples:
  sudo $PROG renew
  sudo $PROG renew --dry-run
  sudo $PROG renew --force
EOF
            ;;
        check)
            cat << EOF
Usage: $PROG check [options]

Run on the server where '$PROG install' was (or will be) run. Connects to DOMAIN from
the configuration file like a browser and checks the certificate actually served:
the chain is trusted and matches the domain, and it is valid for more than --min-days days.
When run as root on the server, also checks that nginx serves the certificate on disk
(it was reloaded after the last renewal) and that automatic renewal is scheduled.

Exit status: 0 if every check passes, 1 if any fails, 2 on a usage error.

Options:
  -m, --min-days DAYS   fail if the certificate expires in fewer days (default: 14)
  -p, --port PORT       HTTPS port (default: 443)
  -c, --config FILE     configuration file (default: cert.conf next to this script)
  -h, --help            show this help

Examples:
  $PROG check
  $PROG check -m 30
EOF
            ;;
        help)
            echo "Usage: $PROG help [command]"
            ;;
        *)
            usage_error "" "unknown command: $1"
            ;;
    esac
}

main() {
    local cmd="${1:-}"
    [ $# -eq 0 ] || shift
    case "$cmd" in
        install) cmd_install "$@" ;;
        renew) cmd_renew "$@" ;;
        check) cmd_check "$@" ;;
        help | -h | --help)
            [ $# -le 1 ] || usage_error "" "too many arguments"
            cmd_help "$@"
            ;;
        "")
            cmd_help >&2
            exit 2
            ;;
        *) usage_error "" "unknown command: $cmd" ;;
    esac
}

main "$@"
