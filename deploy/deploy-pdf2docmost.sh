#!/usr/bin/env bash
#
# deploy-pdf2docmost.sh
# Builds an unprivileged Debian 12 LXC on Proxmox, clones the pdf2docmost repo,
# and runs the Streamlit app as a systemd service.
#
# Run ON the Proxmox host, as root:
#     REPO_URL=https://github.com/KennethDhoe/pdf2docmost.git bash deploy-pdf2docmost.sh
#
# Private repo? Put a token in the URL (fine-grained PAT, Contents:read):
#     REPO_URL=https://<TOKEN>@github.com/KennethDhoe/pdf2docmost.git ...
#
set -euo pipefail

# ------------------------- config (edit me) -------------------------
REPO_URL="${REPO_URL:-https://github.com/KennethDhoe/pdf2docmost.git}"
BRANCH="${BRANCH:-main}"
CTID="${CTID:-9000}"
HOSTNAME="${HOSTNAME:-pdf2docmost}"
CORES="${CORES:-2}"
RAM="${RAM:-1024}"                    # MB  (bump to 2048 for large PDFs)
DISK="${DISK:-4}"                     # GB
BRIDGE="${BRIDGE:-vmbr0}"
STORAGE="${STORAGE:-local-lvm}"       # rootfs storage
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
ROOT_PASSWORD="${ROOT_PASSWORD:-changeme}"
PORT="${PORT:-8501}"
APP_DIR="/opt/pdf2docmost"
# --------------------------------------------------------------------

command -v pct >/dev/null || { echo "ERROR: 'pct' not found. Run this on the Proxmox host."; exit 1; }

if pct status "$CTID" &>/dev/null; then
  echo "ERROR: CTID $CTID already exists. Pick another with CTID=xxxx."
  exit 1
fi

echo ">> Resolving Debian 12 template…"
pveam update >/dev/null 2>&1 || true
TEMPLATE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)
[ -n "$TEMPLATE" ] || { echo "ERROR: no debian-12-standard template found."; exit 1; }

if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
  echo ">> Downloading $TEMPLATE to $TEMPLATE_STORAGE…"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
TEMPLATE_REF="${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}"

echo ">> Creating LXC $CTID ($HOSTNAME)…"
pct create "$CTID" "$TEMPLATE_REF" \
  --hostname "$HOSTNAME" \
  --cores "$CORES" \
  --memory "$RAM" \
  --swap 256 \
  --rootfs "${STORAGE}:${DISK}" \
  --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
  --unprivileged 1 \
  --onboot 1 \
  --ostype debian \
  --password "$ROOT_PASSWORD"

echo ">> Starting container…"
pct start "$CTID"

echo ">> Waiting for network (DHCP)…"
IP=""
for _ in $(seq 1 30); do
  IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)
  [ -n "$IP" ] && break
  sleep 1
done
[ -n "$IP" ] || { echo "ERROR: container got no IP."; exit 1; }
echo ">> Container IP: $IP"

# ---- systemd unit (written on host, pushed in) ----
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/pdf2docmost.service" << EOF
[Unit]
Description=PDF to Docmost Markdown (Streamlit)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HOME=/root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true --browser.gatherUsageStats false
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
pct push "$CTID" "$TMP/pdf2docmost.service" /etc/systemd/system/pdf2docmost.service

echo ">> Installing git + cloning repo + building venv…"
pct exec "$CTID" -- bash -c "
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git python3 python3-venv python3-pip ca-certificates >/dev/null
  rm -rf '$APP_DIR'
  git clone --branch '$BRANCH' --depth 1 '$REPO_URL' '$APP_DIR'
  python3 -m venv '$APP_DIR/.venv'
  '$APP_DIR/.venv/bin/pip' install --upgrade pip -q
  '$APP_DIR/.venv/bin/pip' install -q -r '$APP_DIR/requirements.txt'
  mkdir -p /root/.streamlit
  printf '[general]\nemail=\"\"\n' > /root/.streamlit/credentials.toml
  systemctl daemon-reload
  systemctl enable --now pdf2docmost
"

echo ""
echo "============================================================"
echo " Done."
echo " App:      http://$IP:$PORT"
echo " Console:  pct enter $CTID   (root pw: $ROOT_PASSWORD)"
echo " Logs:     pct exec $CTID -- journalctl -u pdf2docmost -f"
echo " Update:   pct exec $CTID -- bash -c 'cd $APP_DIR && git pull && systemctl restart pdf2docmost'"
echo "============================================================"
