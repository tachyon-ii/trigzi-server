# Trigzi Server Setup & Deployment Guide

Architecture: **nginx** handles SSL termination and static assets, forwarding `/api/` to **Hypercorn** which manages the async **Quart** workers inside a Python virtual environment on RHEL/Rocky Linux.

---

## Prerequisites

- RHEL / CentOS / Rocky Linux server (9.x recommended; 8.x requires the systemd note in §6)
- DNS A-records for `trigzi.com` and `www.trigzi.com` pointing to server IP
- Root or sudo privileges

---

## 1. System Dependencies

```bash
dnf install python3 python3-pip nginx certbot python3-certbot-nginx mariadb-server -y
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

All secrets and config live in `/etc/trigzi/env`. Each line uses bash `export` syntax so the same file can be sourced from a developer shell **and** consumed by systemd's `EnvironmentFile=` directive.

```bash
mkdir -p /etc/trigzi
cat > /etc/trigzi/env << 'EOF'
export DB_HOST=localhost
export DB_PORT=3306
export DB_NAME=trigzi
export DB_USER=trigzi
export DB_PASS=<password>
export GEMINI_API_KEY=<key>
export CLAUDE_API_KEY=<key>
export OPENAI_API_KEY=<key>
EOF
chmod 600 /etc/trigzi/env
```

> **API key env-var names are exact.** The provider modules read `GEMINI_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY`. Do not substitute `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` — they will not be picked up.

Add a `trigzi` shell function to `/root/.bashrc` so every root session gets a fully-loaded environment in one command:

```bash
trigzi() {
    source /etc/trigzi/env
    source /var/www/trigzi/venv/bin/activate
    cd /var/www/trigzi
    echo "OK: trigzi environment loaded"
}
```

The `export` keywords inside `/etc/trigzi/env` make `set -a` redundant — sourcing alone is enough. (If you ever convert the env file to plain `KEY=value` lines for any reason, switch the function to `set -a; source /etc/trigzi/env; set +a` to compensate.)

---

## 5. Database

```bash
# Create database, schema (products + enrichments + sessions), and trigzi user
./setup/createdb trigzi <password>

# Import Open Food Facts data
./scripts/run_import.sh scripts/import_off_to_db.py \
    --input /data2000/openfoodfacts-products.jsonl --write

# Import enriched Woolworths/Coles data
./scripts/run_import.sh scripts/import_enriched.py \
    --input /data2000/enriched_products_normalised.jsonl --write
```

The schema is documented in `README.md` — three tables: `products`, `enrichments`, `sessions`.

---

## 6. Hypercorn / Systemd

Create the base unit at `/etc/systemd/system/trigzi_api.service`:

```ini
[Unit]
Description=Hypercorn instance to serve TRIGZI API
After=network.target

[Service]
User=root
Group=nginx
WorkingDirectory=/var/www/trigzi
Environment="PATH=/var/www/trigzi/venv/bin"

ExecStart=/var/www/trigzi/venv/bin/python3 /var/www/trigzi/venv/bin/hypercorn --workers 3 --bind 127.0.0.1:5000 app:app

[Install]
WantedBy=multi-user.target
```

Create the drop-in directory and override file so systemd loads `/etc/trigzi/env`:

```bash
mkdir -p /etc/systemd/system/trigzi_api.service.d/
```

`/etc/systemd/system/trigzi_api.service.d/override.conf`:
```ini
[Service]
# systemd v246+ tolerates `export` prefix on each line of the env file
EnvironmentFile=-/etc/trigzi/env
```

> **systemd version note:** `EnvironmentFile=` accepting `export` keywords requires systemd ≥ v246. Rocky/RHEL 9 ships systemd 252 — fine. RHEL/CentOS 8 ships systemd 239 — you must either keep an `export`-free copy of the env file for systemd, or upgrade.

Reload and start:
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

    # SSE-bearing routes — disable buffering. Add new SSE routes here.
    location ~ ^/api/v1/(product|chat|analyse)/ {
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

> The original config disabled buffering only on `/api/v1/product/`. Several other routes also stream SSE (`/api/v1/chat/stream`, `/chat/onboarding`, `/chat/sigmund`). The regex location block above covers all of them. If you prefer separate `location` blocks for clarity, replicate the buffering-off settings on each.

```bash
nginx -t && systemctl reload nginx
```

---

## 8. SSL

```bash
certbot --nginx -d trigzi.com -d www.trigzi.com
```

---

## 9. Operations

```bash
# Deploy (bounce hypercorn + nginx)
./deploy.sh

# Tail application logs
./logs.sh

# Application logs (systemd journal)
journalctl -u trigzi_api -n 50 -f

# nginx logs
tail -f /var/log/nginx/trigzi.access.log
tail -f /var/log/nginx/trigzi.error.log

# Quart application log
tail -f /var/www/trigzi/logs/api.log

# Run synthetic client probe (uses tests/api_manifest.json)
./scripts/probe_client.py

# Live API contract tests (uses tests/schemas/endpoints/*.json)
python tests/test_api_contracts.py

# Check LLM provider health
./scripts/probe_live.py all
./scripts/probe_live.py gemini claude

# Product acquisition queue
sort logs/unmatched.log | uniq -c | sort -rn | head -50
```
