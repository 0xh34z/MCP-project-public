#!/bin/bash

set -Eeuo pipefail
trap 'echo "Error: command failed at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

INSTALL_PATH="/opt/proxmox-mcp"
VENV_PATH="${INSTALL_PATH}/venv"
API_SERVICE_NAME="proxmox-api"
MCP_SERVICE_NAME="proxmox-mcp"
API_PORT="${API_PORT:-5000}"
SERVICE_PORT="${SERVICE_PORT:-5002}"
API_BIND_IP="${API_BIND_IP:-127.0.0.1}"
MCP_BIND_IP="${MCP_BIND_IP:-0.0.0.0}"
MCP_PUBLIC_IP="${MCP_PUBLIC_IP:-$(hostname -I | awk '{print $1}') }"
MCP_PUBLIC_IP="${MCP_PUBLIC_IP// /}"
API_UPSTREAM_HOST="${API_UPSTREAM_HOST:-127.0.0.1}"
BOOTSTRAP_PVE_SSH_KEY="${BOOTSTRAP_PVE_SSH_KEY:-true}"

if [ -z "${MCP_PUBLIC_IP}" ]; then
    MCP_PUBLIC_IP="127.0.0.1"
fi

# Variable Check
echo -e "${GREEN}--> Checking variables...${NC}"
REQUIRED_VARS=("API_PORT" "SERVICE_PORT" "API_BIND_IP" "MCP_BIND_IP" "MCP_PUBLIC_IP" "API_UPSTREAM_HOST")
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}Error: Variable $var is not set.${NC}"
        exit 1
    fi
done


echo -e "${GREEN}=== PROXMOX MCP SERVER SETUP ===${NC}"

echo -e "${GREEN}--> Ensuring /etc/hosts entries are present...${NC}"
declare -A HOST_ENTRIES=(
  ["pve0"]="192.168.1.2"
)
for hostname in "${!HOST_ENTRIES[@]}"; do
  ip="${HOST_ENTRIES[$hostname]}"
  if ! grep -qE "^\s*${ip}\s+.*\b${hostname}\b" /etc/hosts; then
    echo "${ip}  ${hostname}" >> /etc/hosts
    echo -e "${GREEN}    Added: ${ip}  ${hostname}${NC}"
  else
    echo -e "${GREEN}    Already present: ${ip}  ${hostname}${NC}"
  fi
done
echo -e "${GREEN}(HTTP Streaming + SSE fallback)${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Error: This script must be run as root${NC}"
    exit 1
fi

echo -e "${GREEN}--> Installing system dependencies...${NC}"
apt update && apt install -y python3 python3-venv python3-pip openssh-client sshpass systemd-timesyncd

echo -e "${GREEN}--> Configuring Timezone and NTP...${NC}"
timedatectl set-timezone Europe/Amsterdam
systemctl enable --now systemd-timesyncd

echo -e "${GREEN}--> Creating installation directory...${NC}"
mkdir -p "${INSTALL_PATH}"

echo -e "${GREEN}--> Creating mcp-user system user...${NC}"
if ! id "mcp-user" &>/dev/null; then
    useradd -r -s /bin/false mcp-user
    echo -e "${GREEN}    Created mcp-user.${NC}"
fi

echo -e "${GREEN}--> Copying MCP server files...${NC}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SOURCE_DIR}/server.py" "${INSTALL_PATH}/"
cp "${SOURCE_DIR}/mcp_http_server.py" "${INSTALL_PATH}/"
cp "${SOURCE_DIR}/requirements.txt" "${INSTALL_PATH}/"
if [ -f "${SOURCE_DIR}/.env" ]; then
    cp "${SOURCE_DIR}/.env" "${INSTALL_PATH}/.env"
    # Normalize Windows CRLF line endings to avoid shell sourcing errors.
    sed -i 's/\r$//' "${INSTALL_PATH}/.env"
fi

# Copy SSH keys if present
if [ -f "${SOURCE_DIR}/id_ed25519" ]; then
    cp "${SOURCE_DIR}/id_ed25519" "${INSTALL_PATH}/"
    chmod 600 "${INSTALL_PATH}/id_ed25519"
fi
if [ -f "${SOURCE_DIR}/id_ed25519.pub" ]; then
    cp "${SOURCE_DIR}/id_ed25519.pub" "${INSTALL_PATH}/"
fi

chown -R mcp-user:mcp-user "${INSTALL_PATH}"

if [ -f "${INSTALL_PATH}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${INSTALL_PATH}/.env"
    set +a
fi

if [ "${BOOTSTRAP_PVE_SSH_KEY}" = "true" ] && [ -f "${INSTALL_PATH}/id_ed25519.pub" ]; then
    SSH_BOOTSTRAP_HOST="${PCT_SSH_HOST:-${PROXMOX_HOST:-}}"
    SSH_BOOTSTRAP_USER="${PCT_SSH_USER:-root}"
    SSH_BOOTSTRAP_PORT="${PCT_SSH_PORT:-22}"
    SSH_BOOTSTRAP_PASSWORD="${PCT_SSH_PASSWORD:-}"

    if [ -n "${SSH_BOOTSTRAP_HOST}" ]; then
        if [ -z "${SSH_BOOTSTRAP_PASSWORD}" ]; then
            read -rsp "Enter SSH password for ${SSH_BOOTSTRAP_USER}@${SSH_BOOTSTRAP_HOST} (leave empty to skip key bootstrap): " SSH_BOOTSTRAP_PASSWORD
            echo
        fi

        if [ -n "${SSH_BOOTSTRAP_PASSWORD}" ]; then
            echo -e "${GREEN}--> Bootstrapping SSH key access on ${SSH_BOOTSTRAP_USER}@${SSH_BOOTSTRAP_HOST}...${NC}"
            if sshpass -p "${SSH_BOOTSTRAP_PASSWORD}" ssh-copy-id \
                -i "${INSTALL_PATH}/id_ed25519.pub" \
                -p "${SSH_BOOTSTRAP_PORT}" \
                -o StrictHostKeyChecking=no \
                -o UserKnownHostsFile=/dev/null \
                "${SSH_BOOTSTRAP_USER}@${SSH_BOOTSTRAP_HOST}" >/dev/null 2>&1; then
                echo -e "${GREEN}--> SSH key bootstrap successful.${NC}"
            else
                echo -e "${RED}Warning: SSH key bootstrap failed. Check PCT_SSH_HOST/PCT_SSH_USER/PCT_SSH_PASSWORD.${NC}"
            fi
        else
            echo -e "${RED}Warning: Skipping SSH key bootstrap (no password provided).${NC}"
        fi
    else
        echo -e "${RED}Warning: Skipping SSH key bootstrap (no PCT_SSH_HOST/PROXMOX_HOST configured).${NC}"
    fi
fi

echo -e "${GREEN}--> Setting up Python virtual environment...${NC}"
cd "${INSTALL_PATH}"
python3 -m venv venv
source ./venv/bin/activate

echo -e "${GREEN}--> Installing Python dependencies...${NC}"
pip install --upgrade pip
pip install -r requirements.txt

echo -e "${GREEN}--> Creating systemd service files...${NC}"
cat > /etc/systemd/system/${API_SERVICE_NAME}.service << EOF
[Unit]
Description=Proxmox API Server
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_PATH}
ExecStart=${VENV_PATH}/bin/python ${INSTALL_PATH}/server.py --host ${API_BIND_IP} --port ${API_PORT}
Restart=always
RestartSec=5
User=mcp-user
Environment="API_PORT=${API_PORT}"
EnvironmentFile=-${INSTALL_PATH}/.env
Environment="ENABLE_COMMAND_EXEC=true"
Environment="ENABLE_PCT_CONTAINER_EXEC=true"
Environment="MAX_COMMAND_TIMEOUT=1800"

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/${MCP_SERVICE_NAME}.service << EOF
[Unit]
Description=Proxmox MCP Server
After=network.target ${API_SERVICE_NAME}.service

[Service]
Type=simple
WorkingDirectory=${INSTALL_PATH}
ExecStart=${VENV_PATH}/bin/python ${INSTALL_PATH}/mcp_http_server.py --host ${MCP_BIND_IP} --port ${SERVICE_PORT} --proxmox-url http://${API_UPSTREAM_HOST}:${API_PORT}
Restart=always
RestartSec=5
User=mcp-user
EnvironmentFile=-${INSTALL_PATH}/.env

[Install]
WantedBy=multi-user.target
EOF

echo -e "${GREEN}--> Reloading systemd daemon...${NC}"
systemctl daemon-reload

echo -e "${GREEN}--> Enabling services...${NC}"
systemctl enable ${API_SERVICE_NAME}.service
systemctl enable ${MCP_SERVICE_NAME}.service

echo -e "${GREEN}--> Starting API service...${NC}"
systemctl start ${API_SERVICE_NAME}.service

echo -e "${GREEN}--> Starting MCP service...${NC}"
systemctl start ${MCP_SERVICE_NAME}.service

echo -e "${GREEN}--> Checking services status...${NC}"
sleep 2
systemctl status ${API_SERVICE_NAME}.service --no-pager
systemctl status ${MCP_SERVICE_NAME}.service --no-pager

echo -e "${GREEN}=== SETUP COMPLETE ===${NC}"
echo -e "${GREEN}API Service: ${API_SERVICE_NAME}${NC}"
echo -e "${GREEN}MCP Service: ${MCP_SERVICE_NAME}${NC}"
echo -e "${GREEN}Installation Path: ${INSTALL_PATH}${NC}"
echo -e "${GREEN}API Port: ${API_PORT}${NC}"
echo -e "${GREEN}MCP Port: ${SERVICE_PORT}${NC}"
echo -e "${GREEN}API Bind IP: ${API_BIND_IP}${NC}"
echo -e "${GREEN}MCP Bind IP: ${MCP_BIND_IP}${NC}"
echo -e "${GREEN}MCP Public IP: ${MCP_PUBLIC_IP}${NC}"
echo -e "${GREEN}Primary MCP endpoint: http://${MCP_PUBLIC_IP}:${SERVICE_PORT}/mcp${NC}"
echo -e "${GREEN}Backward-compatible endpoint: http://${MCP_PUBLIC_IP}:${SERVICE_PORT}/sse${NC}"
echo -e "${GREEN}Virtual Environment: ${VENV_PATH}${NC}"
echo -e "${GREEN}${NC}"
echo -e "${GREEN}To view API logs: journalctl -u ${API_SERVICE_NAME} -f${NC}"
echo -e "${GREEN}To view MCP logs: journalctl -u ${MCP_SERVICE_NAME} -f${NC}"
echo -e "${GREEN}To restart all: systemctl restart ${API_SERVICE_NAME} ${MCP_SERVICE_NAME}${NC}"
