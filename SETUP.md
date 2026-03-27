# Trigzi Server Setup & Deployment Guide

Architecture: **nginx** handles SSL termination and static assets, forwarding `/api/` to **Gunicorn** which manages **Flask** workers inside a Python virtual environment on RHEL/CentOS.

---

## Prerequisites

- RHEL / CentOS / Rocky Linux server
- DNS A-records for `trigzi.com` and `www.trigzi.com` pointing to server IP
- Root or sudo privileges

---

## 1. System Dependencies

```bash
dnf install python3 python3-pip nginx certbot python3-certbot-nginx mysql-server -y
```

---

## 2. Directory Structure

```bash
mkdir -p /var/www/trigzi
chown -R root:nginx /var/www/trigzi
chmod -R 775 /var/www/trigzi
cd /var/www/trigzi
```

---

## 3. Python Virtual Environment

Python venvs hardcode absolute paths — never move or rename the `venv` directory. If the project moves, delete and rebuild.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 4. Environment Configuration

All secrets and config live in `/etc/trigzi/env`. This file is loaded by systemd and by root's shell.

```bash
mkdir -p /etc/trigzi
cat > /etc/trigzi/env << 'EOF'
export DB_HOST=localhost
export DB_NAME=trigzi
export DB_USER=trigzi
export DB_PASS=<password>
export GEMINI_API_KEY=<key>
export CLAUDE_API_KEY=<key>
export OPENAI_API_KEY=<key>
EOF
chmod 600 /etc/trigzi/env
```

Add to `/root/.bashrc` so every `sudo su root` session is fully configured:

```bash
trigzi() {
    source /etc/trigzi/env
    source /var/www/trigzi/venv/bin/activate
    cd /var/www/trigzi
    echo "OK: trigzi environment loaded"
}
```

---

## 5. Database

```bash
# Create database and products table
./setup/createdb trigzi <password>

# Import Open Food Facts data
./scripts/run_import.sh scripts/import_off_to_db.py \
    --input /data2000/openfoodfacts-products.jsonl --write

# Import enriched Woolworths/Coles data
./scripts/run_import.sh scripts/import_enriched.py \
    --input /data2000/enriched_products_normalised.jsonl --write
```

---

## 6. Gunicorn / Systemd

Create `/etc/systemd/system/trigzi_api.service`:

```ini
[Unit]
Description=Gunicorn instance to serve TRIGZI API
After=network.target

[Service]
User=root
Group=nginx
WorkingDirectory=/var/www/trigzi
Environment="PATH=/var/www/trigzi/venv/bin"
EnvironmentFile=/etc/trigzi/env

ExecStart=/var/www/trigzi/venv/bin/gunicorn \
    --workers 3 \
    --bind 127.0.0.1:5000 \
    -m 007 \
    app:app

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable trigzi_api
systemctl start trigzi_api
```

---

## 7. Nginx

Configuration at `/etc/nginx/conf.d/trigzi.com.conf`:

```nginx
server {
    listen                  443 ssl;
    listen                  [::]:443 ssl;
    server_name             trigzi.com www.trigzi.com;

    root                    /var/www/trigzi/html;

    ssl_certificate         /etc/letsencrypt/live/trigzi.com/fullchain.pem;
    ssl_certificate_key     /etc/letsencrypt/live/trigzi.com/privkey.pem;
    include                 /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam             /etc/letsencrypt/ssl-dhparams.pem;

    access_log              /var/log/nginx/trigzi.access.log combined buffer=512k flush=1m;
    error_log               /var/log/nginx/trigzi.error.log warn;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Standard API endpoints
    location /api/ {
        proxy_pass              http://127.0.0.1:5000;
        proxy_set_header        Host $host;
        proxy_set_header        X-Real-IP $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header        X-Forwarded-Proto $scheme;
        proxy_connect_timeout   10s;
        proxy_send_timeout      60s;
        proxy_read_timeout      60s;
    }

    # Product lookup — SSE streaming, disable buffering
    location /api/v1/product/ {
        proxy_pass              http://127.0.0.1:5000;
        proxy_set_header        Host $host;
        proxy_set_header        X-Real-IP $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header        X-Forwarded-Proto $scheme;
        proxy_connect_timeout   10s;
        proxy_send_timeout      120s;
        proxy_read_timeout      120s;
        proxy_buffering         off;
        proxy_cache             off;
        chunked_transfer_encoding on;
    }
}

server {
    listen      80;
    listen      [::]:80;
    server_name trigzi.com www.trigzi.com;
    if ($host = www.trigzi.com) { return 301 https://$host$request_uri; }
    if ($host = trigzi.com)     { return 301 https://$host$request_uri; }
    return 404;
}
```

```bash
nginx -t && systemctl reload nginx
```

## 8. SSL

```bash
certbot --nginx -d trigzi.com -d www.trigzi.com
```

---

## 9. Operations

```bash
# Deploy (bounce gunicorn + nginx)
./deploy.sh

# View logs
./logs.sh

# Application logs
journalctl -u trigzi_api -n 50 -f

# nginx logs
tail -f /var/log/nginx/trigzi.access.log
tail -f /var/log/nginx/trigzi.error.log

# Run test suite
./run_tests.sh

# Check LLM provider health
./scripts/probe_live.py all

# Product acquisition queue
sort logs/unmatched.log | uniq -c | sort -rn | head -50
```
