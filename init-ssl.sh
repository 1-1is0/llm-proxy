#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# 1. Load environment variables from .env if it exists in the current directory
if [ -f .env ]; then
    echo "Loading environment variables from .env..."
    while IFS= read -r line || [ -n "$line" ]; do
        # Ignore comments and empty lines
        if [[ ! "$line" =~ ^# ]] && [[ ! -z "$line" ]]; then
            # Extract variable name and value
            name=$(echo "$line" | cut -d'=' -f1 | xargs)
            value=$(echo "$line" | cut -d'=' -f2- | xargs)
            # Strip surrounding single or double quotes
            value=$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
            export "$name=$value"
        fi
    done < .env
fi

# 2. Check and assign variables
if [ -z "$DOMAIN" ]; then
    echo "Error: DOMAIN environment variable is not set and was not found in .env."
    exit 1
fi

if [ -z "$EMAIL" ]; then
    echo "Error: EMAIL environment variable is not set and was not found in .env."
    exit 1
fi

if [ -z "$PUBLIC_IP" ]; then
    echo "Error: PUBLIC_IP environment variable is not set and was not found in .env."
    echo "Please set the PUBLIC_IP environment variable or add it to your .env file."
    echo "Example: export PUBLIC_IP=1.2.3.4"
    exit 1
fi

echo "Target Domain: $DOMAIN"
echo "Target Email: $EMAIL"
echo "Target Public IP (from env): $PUBLIC_IP"

# 2. Verify DNS Resolution
echo "Resolving DNS for $DOMAIN..."
RESOLVED_IP=$(python3 -c "import socket; print(socket.gethostbyname('$DOMAIN'))" 2>/dev/null || true)

if [ -z "$RESOLVED_IP" ]; then
    echo "Error: Could not resolve DNS for $DOMAIN."
    echo "Please ensure you have configured your DNS A record for $DOMAIN to point to $PUBLIC_IP."
    exit 1
fi

if [ "$RESOLVED_IP" != "$PUBLIC_IP" ]; then
    echo "Error: DNS resolution mismatch."
    echo "  $DOMAIN currently resolves to: $RESOLVED_IP"
    echo "  Expected Public IP from env:  $PUBLIC_IP"
    echo "Please ensure the DNS A record points to $PUBLIC_IP and has propagated."
    exit 1
fi

echo "DNS verification passed! $DOMAIN correctly points to $PUBLIC_IP."

# 3. Create necessary directories
echo "Creating config directories..."
mkdir -p certbot/conf/live/$DOMAIN
mkdir -p certbot/www

# 4. Check if we already have a real certificate
if [ -f "certbot/conf/live/$DOMAIN/privkey.pem" ] && [ -f "certbot/conf/live/$DOMAIN/fullchain.pem" ]; then
    # Check if it's self-signed or real
    # If the certificate contains "localhost" or isn't issued by Let's Encrypt, we should replace it
    ISSUER=$(openssl x509 -issuer -noout -in certbot/conf/live/$DOMAIN/fullchain.pem || echo "")
    if [[ "$ISSUER" == *"Let's Encrypt"* ]] || [[ "$ISSUER" == *"R3"* ]] || [[ "$ISSUER" == *"E1"* ]]; then
        echo "A valid SSL certificate issued by Let's Encrypt already exists."
        echo "Skipping initialization. Run 'docker compose up -d' to start the services."
        exit 0
    else
        echo "Found existing self-signed/dummy certificate. Proceeding to obtain real SSL..."
    fi
fi

# 5. Create dummy certificate if none exists
if [ ! -f "certbot/conf/live/$DOMAIN/privkey.pem" ]; then
    echo "Generating dummy self-signed certificate for Nginx startup..."
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -keyout certbot/conf/live/$DOMAIN/privkey.pem \
        -out certbot/conf/live/$DOMAIN/fullchain.pem \
        -subj "/CN=localhost"
fi

# 6. Start Nginx
echo "Starting Nginx in background..."
docker compose up -d nginx

# 7. Run Certbot to get the real certificate
echo "Running Certbot to request real Let's Encrypt SSL certificate..."
docker compose run --rm certbot certonly \
    --webroot -w /var/www/html \
    --email "$EMAIL" \
    -d "$DOMAIN" \
    --agree-tos --no-eff-email \
    --force-renewal

# 8. Reload Nginx configuration to pick up the new certificate
echo "Reloading Nginx to apply real SSL certificate..."
docker compose exec nginx nginx -s reload

echo "========================================================="
echo " SSL Initialization completed successfully!"
echo " Your application is now served securely at: https://$DOMAIN"
echo "========================================================="
