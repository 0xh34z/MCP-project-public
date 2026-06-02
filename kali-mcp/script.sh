#!/bin/bash

set -Eeuo pipefail
trap 'echo "Error: command failed at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

INSTALL_PATH="/opt/kali-mcp"
VENV_PATH="${INSTALL_PATH}/venv"
API_SERVICE_NAME="kali-api"
MCP_SERVICE_NAME="kali-mcp"

# ============================================================================
# USER CONFIGURATION (edit these values, then run ./script.sh)
# ============================================================================
API_PORT="5000"
SERVICE_PORT="5001"

# API server bind address (usually localhost for internal-only API)
API_BIND_IP="127.0.0.1"

# MCP server bind address (usually 0.0.0.0 so other machines can reach it)
MCP_BIND_IP="0.0.0.0"

# Public IPv4 advertised in script output
MCP_PUBLIC_IP="192.168.1.101"

# Host used by MCP service to reach the local API
API_UPSTREAM_HOST="127.0.0.1"

# Optional static IPv4 configuration for this machine
# Set to 1 to apply static IPv4 during script run
APPLY_STATIC_IPV4="1"
STATIC_IPV4_IFACE="eth0"
STATIC_IPV4_CIDR="192.168.1.101/24"
STATIC_IPV4_GW="192.168.1.1"
STATIC_IPV4_DNS="1.1.1.1,8.8.8.8"
# Safety: do not rewrite /etc/network/interfaces unless you explicitly opt in.
FORCE_IFUPDOWN_STATIC="1"

# Variable Check
echo -e "${GREEN}--> Checking variables...${NC}"
REQUIRED_VARS=("API_PORT" "SERVICE_PORT" "API_BIND_IP" "MCP_BIND_IP" "MCP_PUBLIC_IP" "API_UPSTREAM_HOST")
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}Error: Variable $var is not set.${NC}"
        exit 1
    fi
done

if [ "${APPLY_STATIC_IPV4}" == "1" ]; then
    STATIC_VARS=("STATIC_IPV4_CIDR" "STATIC_IPV4_GW" "STATIC_IPV4_DNS")
    for var in "${STATIC_VARS[@]}"; do
        if [ -z "${!var}" ]; then
            echo -e "${RED}Error: Variable $var is required when APPLY_STATIC_IPV4=1.${NC}"
            exit 1
        fi
    done
fi


if [ -z "${MCP_PUBLIC_IP}" ]; then
    MCP_PUBLIC_IP="127.0.0.1"
fi

echo -e "${GREEN}=== KALI MCP SERVER SETUP ===${NC}"
echo -e "${GREEN}(HTTP Streaming + SSE fallback)${NC}"

log_warn() {
    echo -e "${RED}$1${NC}"
}

install_if_available() {
    local pkg="$1"
    if apt-cache show "$pkg" >/dev/null 2>&1; then
        apt install -y "$pkg"
        return 0
    fi
    return 1
}

apply_static_ipv4_config() {
    if [ "${APPLY_STATIC_IPV4}" != "1" ]; then
        return 0
    fi

    if [ -z "${STATIC_IPV4_CIDR}" ] || [ -z "${STATIC_IPV4_GW}" ]; then
        log_warn "APPLY_STATIC_IPV4=1 requires STATIC_IPV4_CIDR and STATIC_IPV4_GW."
        exit 1
    fi

    local iface="${STATIC_IPV4_IFACE}"
    if [ -z "${iface}" ]; then
        iface="$(ip route | awk '/^default/ {print $5; exit}')"
    fi

    if [ -z "${iface}" ]; then
        log_warn "Could not detect network interface. Set STATIC_IPV4_IFACE explicitly."
        exit 1
    fi

    local dns_csv
    dns_csv="${STATIC_IPV4_DNS// /}"
    local dns_space
    dns_space="${STATIC_IPV4_DNS//,/ }"

    echo -e "${GREEN}--> Applying static IPv4 network config...${NC}"
    echo -e "${GREEN}   Interface: ${iface}${NC}"
    echo -e "${GREEN}   Address:   ${STATIC_IPV4_CIDR}${NC}"
    echo -e "${GREEN}   Gateway:   ${STATIC_IPV4_GW}${NC}"
    echo -e "${GREEN}   DNS:       ${STATIC_IPV4_DNS}${NC}"

    if command -v nmcli >/dev/null 2>&1 && nmcli -t -f RUNNING general status | grep -qi '^running$'; then
        local conn
        conn="$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: -v dev="${iface}" '$2==dev {print $1; exit}')"
        if [ -z "${conn}" ]; then
            conn="${iface}"
            nmcli connection add type ethernet ifname "${iface}" con-name "${conn}" autoconnect yes >/dev/null 2>&1 || true
        fi

        if ! nmcli connection modify "${conn}" \
            ipv4.method manual \
            ipv4.addresses "${STATIC_IPV4_CIDR}" \
            ipv4.gateway "${STATIC_IPV4_GW}" \
            ipv4.dns "${dns_csv}" \
            connection.autoconnect yes; then
            log_warn "Failed to apply static IPv4 using NetworkManager."
            exit 1
        fi

        if ! nmcli connection up "${conn}"; then
            log_warn "NetworkManager applied config but failed to bring connection up."
            exit 1
        fi

        if ! ip -4 addr show dev "${iface}" | grep -q "inet "; then
            log_warn "No IPv4 address detected on ${iface} after NetworkManager apply."
            exit 1
        fi

        return 0
    fi

    if [ -f /etc/network/interfaces ]; then
        if [ "${FORCE_IFUPDOWN_STATIC}" != "1" ]; then
            log_warn "NetworkManager is not active and FORCE_IFUPDOWN_STATIC=1 is not set."
            log_warn "Skipping static IP apply to avoid breaking connectivity."
            return 0
        fi

        cp /etc/network/interfaces /etc/network/interfaces.bak.$(date +%s)
        cat > /etc/network/interfaces << EOF
source /etc/network/interfaces.d/*

auto lo
iface lo inet loopback

auto ${iface}
iface ${iface} inet static
    address ${STATIC_IPV4_CIDR}
    gateway ${STATIC_IPV4_GW}
    dns-nameservers ${dns_space}
EOF

        if command -v ifreload >/dev/null 2>&1; then
            if ! ifreload -a; then
                log_warn "Static config written, but ifreload failed."
                exit 1
            fi
        elif systemctl list-unit-files | grep -q '^networking.service'; then
            if ! systemctl restart networking; then
                log_warn "Static config written, but networking restart failed."
                exit 1
            fi
        elif command -v ifdown >/dev/null 2>&1 && command -v ifup >/dev/null 2>&1; then
            ifdown "${iface}" >/dev/null 2>&1 || true
            if ! ifup "${iface}"; then
                log_warn "Static config written, but ifup failed."
                exit 1
            fi
        else
            log_warn "Static config written to /etc/network/interfaces, but no reload tool detected. Reboot may be required."
        fi

        if ! ip -4 addr show dev "${iface}" | grep -q "inet "; then
            log_warn "No IPv4 address detected on ${iface} after ifupdown apply."
            exit 1
        fi

        return 0
    fi

    log_warn "No supported network manager found (nmcli or /etc/network/interfaces)."
    log_warn "Skipping static IPv4 apply."
    exit 1
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Error: This script must be run as root${NC}"
    exit 1
fi

echo -e "${GREEN}--> Installing system dependencies...${NC}"
apt update && apt install -y systemd-timesyncd

echo -e "${GREEN}--> Configuring Timezone and NTP...${NC}"
timedatectl set-timezone Europe/Amsterdam
systemctl enable --now systemd-timesyncd

echo -e "${GREEN}--> Creating mcp-user system user...${NC}"
if ! id "mcp-user" &>/dev/null; then
    useradd -r -s /bin/false mcp-user
    echo -e "${GREEN}    Created mcp-user.${NC}"
fi

echo -e "${GREEN}--> Installing required base packages...${NC}"
apt install -y python3 python3-pip qemu-guest-agent

if systemctl list-unit-files | grep -q '^qemu-guest-agent.service'; then
    systemctl enable --now qemu-guest-agent.service || true
fi

PY_SHORT="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! install_if_available "python3-venv"; then
    if ! install_if_available "python${PY_SHORT}-venv"; then
        log_warn "Could not install python venv package (python3-venv or python${PY_SHORT}-venv)."
        log_warn "Install it manually, then rerun this script."
        exit 1
    fi
fi

echo -e "${GREEN}--> Installing core security tools...${NC}"
core_pkgs=(nmap gobuster dirb sqlmap hydra john)
core_missing=()
for pkg in "${core_pkgs[@]}"; do
    if ! install_if_available "$pkg"; then
        core_missing+=("$pkg")
    fi
done

if [ ${#core_missing[@]} -gt 0 ]; then
    log_warn "Some core packages are unavailable in current repos: ${core_missing[*]}"
fi

echo -e "${GREEN}--> Installing optional security tools when available...${NC}"
optional_pkgs=(nikto wpscan enum4linux enum4linux-ng metasploit-framework wordlists)
optional_installed=()
optional_missing=()
for pkg in "${optional_pkgs[@]}"; do
    if install_if_available "$pkg"; then
        optional_installed+=("$pkg")
    else
        optional_missing+=("$pkg")
    fi
done

if [ ${#optional_installed[@]} -gt 0 ]; then
    echo -e "${GREEN}Optional packages installed: ${optional_installed[*]}${NC}"
fi
if [ ${#optional_missing[@]} -gt 0 ]; then
    log_warn "Optional packages not found in repo: ${optional_missing[*]}"
fi

echo -e "${GREEN}--> Verifying required tools are available...${NC}"
required_tools=(nmap gobuster dirb nikto sqlmap hydra john wpscan enum4linux msfconsole)
missing_tools=()
for tool in "${required_tools[@]}"; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        missing_tools+=("$tool")
    fi
done

if [ ${#missing_tools[@]} -gt 0 ]; then
    echo -e "${RED}Warning: Missing tools after install: ${missing_tools[*]}${NC}"
    echo -e "${RED}The API can start, but related MCP tools will fail until these binaries are installed.${NC}"
else
    echo -e "${GREEN}All required CLI tools found.${NC}"
fi

if command -v nmap >/dev/null 2>&1; then
    echo -e "${GREEN}--> Granting Nmap network capabilities for non-root SYN scans...${NC}"
    setcap cap_net_raw,cap_net_admin,cap_net_bind_service+eip $(command -v nmap) || echo -e "${RED}Warning: Failed to setcap on nmap${NC}"
fi

apply_static_ipv4_config

echo -e "${GREEN}--> Creating installation directory...${NC}"
mkdir -p "${INSTALL_PATH}"

echo -e "${GREEN}--> Copying MCP server files...${NC}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SOURCE_DIR}/server.py" "${INSTALL_PATH}/"
cp "${SOURCE_DIR}/mcp_http_server.py" "${INSTALL_PATH}/"
cp "${SOURCE_DIR}/requirements.txt" "${INSTALL_PATH}/"
if [ -f "${SOURCE_DIR}/.env" ]; then
    cp "${SOURCE_DIR}/.env" "${INSTALL_PATH}/.env"
fi

echo -e "${GREEN}--> Setting up Python virtual environment...${NC}"
cd "${INSTALL_PATH}"
if ! python3 -m venv venv; then
    log_warn "Failed to create venv. Ensure python venv package is installed (python3-venv or python${PY_SHORT}-venv)."
    exit 1
fi

if [ ! -x "${VENV_PATH}/bin/python" ]; then
    log_warn "Venv Python binary not found at ${VENV_PATH}/bin/python"
    exit 1
fi

echo -e "${GREEN}--> Installing Python dependencies...${NC}"
if ! "${VENV_PATH}/bin/python" -m pip install --upgrade pip; then
    log_warn "Failed to bootstrap pip in venv."
    exit 1
fi

if ! "${VENV_PATH}/bin/python" -m pip install -r requirements.txt; then
    log_warn "Failed to install Python requirements."
    exit 1
fi

chown -R mcp-user:mcp-user "${INSTALL_PATH}"

echo -e "${GREEN}--> Creating systemd service files...${NC}"
cat > /etc/systemd/system/${API_SERVICE_NAME}.service << EOF
[Unit]
Description=Kali API Server
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_PATH}
ExecStart=${VENV_PATH}/bin/python ${INSTALL_PATH}/server.py --ip ${API_BIND_IP} --port ${API_PORT}
Restart=always
RestartSec=5
User=mcp-user
Environment="API_PORT=${API_PORT}"
EnvironmentFile=-${INSTALL_PATH}/.env

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/${MCP_SERVICE_NAME}.service << EOF
[Unit]
Description=Kali MCP Server
After=network.target ${API_SERVICE_NAME}.service

[Service]
Type=simple
WorkingDirectory=${INSTALL_PATH}
ExecStart=${VENV_PATH}/bin/python ${INSTALL_PATH}/mcp_http_server.py --host ${MCP_BIND_IP} --port ${SERVICE_PORT} --kali-url http://${API_UPSTREAM_HOST}:${API_PORT}
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
