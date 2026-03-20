# Trigzi Server Architecture & Deployment Guide

This document outlines the provisioning and deployment pipeline for the Trigzi backend. The architecture utilizes a standard reverse-proxy pattern: **NGINX** handles SSL termination and static asset delivery, forwarding dynamic API requests to **Gunicorn**, which manages the **Flask** application workers running inside an isolated Python virtual environment.

## Prerequisites
* A Debian/Ubuntu-based Linux server.
* DNS A-records for `trigzi.com` and `www.trigzi.com` pointing to the server's public IP.
* Root or `sudo` privileges.

---

## 1. System Dependencies
Install the required system-level packages. 

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-venv python3-pip nginx certbot python3-certbot-nginx -y
```

## 2. Directory Structure & Permissions
We deploy to `/var/www/` as it is the Unix standard for web-facing applications.

```bash
# Create the root application directory
sudo mkdir -p /var/www/trigzi
sudo chown -R $USER:www-data /var/www/trigzi
sudo chmod -R 775 /var/www/trigzi
cd /var/www/trigzi
```
*Architecture Note: We assign group ownership to `www-data` so NGINX can natively read the static frontend files in the `/html` directory without requiring elevated privileges.*

## 3. Python Virtual Environment
Python virtual environments hardcode absolute paths into their binaries (like `pip` and `gunicorn`). **Never move or rename a `venv` directory.** If the project moves, nuke the environment and rebuild it.

```bash
# Build the pristine environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies (Flask, gunicorn, requests, curl_cffi)
pip install -r requirements.txt
```

## 4. Gunicorn & Systemd Application Server
We use `systemd` to keep the Python application alive, restart it on crashes, and manage its boot sequence.

Create the service file:
```bash
sudo vi /etc/systemd/system/trigzi_api.service
```

Paste the following configuration:
```ini
[Unit]
Description=Gunicorn instance to serve TRIGZI API
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/var/www/trigzi
Environment="PATH=/var/www/trigzi/venv/bin"

# Boot 3 workers and bind to localhost port 5000. 
# We use localhost so the API is inaccessible from the public internet bypassing NGINX.
ExecStart=/var/www/trigzi/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:5000 -m 007 app:app

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable trigzi_api
sudo systemctl start trigzi_api
```

## 5. NGINX Reverse Proxy
NGINX acts as the gatekeeper. It serves `/html` directly from disk (fast) and proxies `/api/` to Gunicorn (dynamic).

Create the configuration file:
```bash
sudo vi /etc/nginx/conf.d/trigzi.com.conf
```

Paste the routing logic:
```nginx
server {
    listen 80;
    listen [::]:80;
    server_name trigzi.com [www.trigzi.com](https://www.trigzi.com);

    # Frontend html files
    root /var/www/trigzi/html;

    # Logging
    access_log /var/log/nginx/trigzi.access.log combined buffer=512k flush=1m;
    error_log /var/log/nginx/trigzi.error.log warn;

    # Frontend SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Backend API Proxy
    location /api/ {
        proxy_pass [http://127.0.0.1:5000](http://127.0.0.1:5000);
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Verify syntax and reload:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 6. SSL Configuration (Certbot)
We use EFF's Certbot to automatically provision Let's Encrypt SSL certificates and modify our NGINX config to enforce HTTPS redirects.

```bash
sudo certbot --nginx -d trigzi.com -d [www.trigzi.com](https://www.trigzi.com)
```
*Note: Certbot will automatically rewrite the `/etc/nginx/conf.d/trigzi.com.conf` file to include the SSL termination parameters and port 443 listeners.*

---

## 7. Operational Cheatsheet

**Check the Application Logs (The first stop for 500 errors):**
```bash
sudo journalctl -u trigzi_api.service -n 50 -f
```

**Check NGINX Logs (The first stop for 502/404 errors):**
```bash
sudo tail -f /var/log/nginx/trigzi.error.log
sudo tail -f /var/log/nginx/trigzi.access.log
```

**Standard Deployment Bounce:**
After pulling new code, always bounce the application server to load the new Python files into memory.
```bash
sudo systemctl restart trigzi_api
```
```
