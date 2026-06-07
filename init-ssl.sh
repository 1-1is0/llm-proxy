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
    echo "Warning: DNS resolution mismatch."
    echo "  $DOMAIN currently resolves to: $RESOLVED_IP"
    echo "  Expected Public IP from env:  $PUBLIC_IP"
    echo "  Note: If you are using Cloudflare DNS Proxy (Orange Cloud), this mismatch is normal."
    echo "  We will proceed, but make sure Cloudflare is forwarding HTTP traffic to your VPS."
    echo "  Press Ctrl+C to abort if this is incorrect, or wait 5 seconds to proceed..."
    sleep 5
else
    echo "DNS verification passed! $DOMAIN correctly points to $PUBLIC_IP."
fi

# 3. Create SSL directory
echo "Creating SSL configuration directory..."
mkdir -p nginx/ssl

# 4. Check if SSL certificate exists
CERT_EXISTS=false
IS_DUMMY=false

if [ -f "nginx/ssl/cert.pem" ] && [ -f "nginx/ssl/key.pem" ]; then
    CERT_EXISTS=true
    # Check if the certificate is self-signed/dummy (issued by localhost)
    ISSUER=$(openssl x509 -issuer -noout -in nginx/ssl/cert.pem || echo "")
    if [[ "$ISSUER" == *"localhost"* ]] || [[ "$ISSUER" == *"CN=localhost"* ]]; then
        IS_DUMMY=true
    fi
fi

if [ "$CERT_EXISTS" = false ]; then
    echo "No SSL certificate found at nginx/ssl/cert.pem."
    echo "Generating a temporary self-signed certificate for initial Nginx startup..."
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -keyout nginx/ssl/key.pem \
        -out nginx/ssl/cert.pem \
        -subj "/CN=localhost"
    IS_DUMMY=true
fi

# 5. Start Nginx
echo "Starting Nginx in the background..."
docker compose up -d nginx

# 6. Output status and instructions
echo "========================================================="
if [ "$IS_DUMMY" = true ]; then
    echo " STATUS: RUNNING (WITH TEMPORARY SELF-SIGNED SSL)"
    echo "========================================================="
    echo " A temporary certificate has been installed so Nginx can start."
    echo " To make SSL fully valid under Cloudflare DNS Proxying:"
    echo " 1. Go to your Cloudflare Dashboard -> SSL/TLS -> Origin Server."
    echo " 2. Click 'Create Certificate' for: $DOMAIN"
    echo " 3. Copy the PEM Certificate content and save it to:"
    echo "    nginx/ssl/cert.pem"
    echo " 4. Copy the Private Key content and save it to:"
    echo "    nginx/ssl/key.pem"
    echo " 5. Change SSL/TLS Encryption Mode in Cloudflare to: Full (strict)"
    echo " 6. Reload Nginx configuration to apply the new certificates:"
    echo "    docker compose exec nginx nginx -s reload"
else
    echo " STATUS: SECURED (WITH VALID CLOUDFLARE ORIGIN SSL)"
    echo "========================================================="
    echo " Nginx has successfully started using your custom SSL certificates."
    echo " Your application is now served securely at: https://$DOMAIN"
    echo " Make sure Cloudflare SSL/TLS Encryption Mode is set to 'Full (strict)'."
fi
echo "========================================================="
