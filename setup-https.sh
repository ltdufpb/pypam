#!/bin/bash
# Setup script for PyPAM HTTPS with nginx and Let's Encrypt
# Run this on the Oracle Cloud instance as root (or with sudo)

set -euo pipefail

DOMAIN="a88aec8c.sslip.io"
EMAIL="${1:-}"

if [ -z "$EMAIL" ]; then
    echo "Usage: sudo ./setup-https.sh your-email@example.com"
    echo "  The email is required by Let's Encrypt for certificate expiry notices."
    exit 1
fi

echo "==> Installing nginx and certbot..."
apt-get update
apt-get install -y nginx certbot python3-certbot-nginx

echo "==> Stopping nginx temporarily..."
systemctl stop nginx || true

echo "==> Creating ACME challenge directory..."
mkdir -p /var/www/certbot

echo "==> Obtaining SSL certificate from Let's Encrypt..."
certbot certonly --standalone \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL"

echo "==> Installing nginx configuration..."
cp nginx/pypam.conf /etc/nginx/sites-available/pypam
ln -sf /etc/nginx/sites-available/pypam /etc/nginx/sites-enabled/pypam
rm -f /etc/nginx/sites-enabled/default

echo "==> Testing nginx configuration..."
nginx -t

echo "==> Starting nginx..."
systemctl enable nginx
systemctl start nginx

echo "==> Setting up automatic certificate renewal..."
# Certbot installs a systemd timer by default, but let's make sure
systemctl enable certbot.timer
systemctl start certbot.timer

# Reload nginx after each renewal so it picks up the new certificate
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh << 'HOOK'
#!/bin/bash
systemctl reload nginx
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

echo "==> Restarting PyPAM service..."
systemctl restart pypam

echo ""
echo "Done! PyPAM is now available at:"
echo "  https://$DOMAIN"
echo ""
echo "Certificates will auto-renew via certbot.timer."
echo "To check renewal: sudo certbot renew --dry-run"
