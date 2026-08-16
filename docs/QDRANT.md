# QDRANT installation

wget https://github.com/qdrant/qdrant/releases/download/v1.19.0/qdrant-x86_64-unknown-linux-musl.tar.gz

tar -xzf qdrant-x86_64-unknown-linux-musl.tar.gz

chmod +x qdrant

mv qdrant /usr/local/bin

qdrant --version 

sudo chown root:root /usr/local/bin/qdrant

sudo useradd -r -s /sbin/nologin qdrant

sudo mkdir -p /var/lib/qdrant /etc/qdrant

sudo chown -R qdrant:qdrant /var/lib/qdrant /etc/qdrant

cat << EOF > /etc/qdrant/config.yaml
storage:
  storage_path: /var/lib/qdrant

service:
  host: 127.0.0.1
  http_port: 6333
  grpc_port: 6334
EOF

cat << EOF > /etc/systemd/system/qdrant.service
[Unit]
Description=Qdrant Vector Database
After=network.target

[Service]
Type=simple
User=qdrant
Group=qdrant
WorkingDirectory=/var/lib/qdrant
ExecStart=/usr/local/bin/qdrant --config-path /etc/qdrant/config.yaml
Restart=on-failure
RestartSec=5s

# Critical for mmap-heavy vector databases
LimitNOFILE=65536

# Hardening
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now qdrant
sudo systemctl status qdrant --no-pager

curl http://127.0.0.1:6333/

{"title":"qdrant - vector search engine","version":"1.19.0","commit":"74f3e85b9473c62560006c043e13737ce6b48412"}
