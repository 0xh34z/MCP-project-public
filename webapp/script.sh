#!/bin/bash

set -Eeuo pipefail
trap 'echo "Error: command failed at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

DOMAIN=""
if [ -z "$DOMAIN" ]; then
    DOMAIN=$(hostname -I | cut -d' ' -f1)
fi
MYSQL_ROOT_PASSWORD="uCrhlQyvXpkeShPwOSQxaMQxZ"
WEB_DB_NAME="gui"
WEB_DB_USER="user"
WEB_DB_PASSWORD="uCrhlQyvXpkeShPwOSQxaMQxZ"
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
WEB_ROOT="/var/www/html/public"
KALI_MCP_IP="${KALI_MCP_IP:-192.168.1.101}"
KALI_MCP_PORT="${KALI_MCP_PORT:-5001}"
PROXMOX_MCP_IP="${PROXMOX_MCP_IP:-192.168.1.100}"
PROXMOX_MCP_PORT="${PROXMOX_MCP_PORT:-5002}"
MCP_ENDPOINT_PATH="${MCP_ENDPOINT_PATH:-/mcp}"
MAX_UPLOAD_SIZE="${MAX_UPLOAD_SIZE:-64M}"
MAX_POST_SIZE="${MAX_POST_SIZE:-64M}"
MAX_ALLOWED_PACKET="${MAX_ALLOWED_PACKET:-128M}"
DB_NET_READ_TIMEOUT="${DB_NET_READ_TIMEOUT:-120}"
DB_NET_WRITE_TIMEOUT="${DB_NET_WRITE_TIMEOUT:-120}"
DB_WAIT_TIMEOUT="${DB_WAIT_TIMEOUT:-600}"
PHPMYADMIN_ALIAS="/wZNxxnmnPCMIXQsaAzBspobdw" # needs a / at the start
PHPMYADMIN_CONTROL_USER="phpmyadmin"
PHPMYADMIN_CONTROL_PASS="${MYSQL_ROOT_PASSWORD}"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# Variable Check
echo -e "${GREEN}--> Checking variables...${NC}"
REQUIRED_VARS=("DOMAIN" "MYSQL_ROOT_PASSWORD" "WEB_DB_NAME" "WEB_DB_USER" "WEB_DB_PASSWORD" "WEB_ROOT")
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}Error: Variable $var is not set.${NC}"
        exit 1
    fi
done


# SSL Setup Removed (as requested)
ENABLE_SSL=false


echo -e "${GREEN}=== STARTING NGINX + PHP + MariaDB INSTALLATION ===${NC}"

echo -e "${GREEN}--> Setting system timezone to Europe/Amsterdam...${NC}"
if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone Europe/Amsterdam || true
fi
ln -sf /usr/share/zoneinfo/Europe/Amsterdam /etc/localtime
echo "Europe/Amsterdam" > /etc/timezone

echo -e "${GREEN}--> Updating system...${NC}"
apt update && apt upgrade -y

echo -e "${GREEN}--> Installing MariaDB Server...${NC}"
apt install mariadb-server -y

# MariaDB syntax differs from MySQL 8. Try broad-compatible forms in order.
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';" \
    || mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('${MYSQL_ROOT_PASSWORD}');"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "DELETE FROM mysql.user WHERE User='';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "DROP DATABASE IF EXISTS test;"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;"

echo -e "${GREEN}--> Creating Database and User with full privileges...${NC}"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE DATABASE IF NOT EXISTS \`${WEB_DB_NAME}\`;"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE USER IF NOT EXISTS '${WEB_DB_USER}'@'localhost' IDENTIFIED BY '${WEB_DB_PASSWORD}';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "GRANT ALL PRIVILEGES ON *.* TO '${WEB_DB_USER}'@'localhost' WITH GRANT OPTION;"

echo -e "${GREEN}--> Creating Read-Only Grafana User...${NC}"
GRAFANA_USER="grafana_reader"
GRAFANA_PASS="uCrhlQyvXpkeShPwOSQxaMQxZ" # Consider changing this or making it a variable
GRAFANA_TABLES="${GRAFANA_TABLES:-}"

# Create role for read-only monitoring and the grafana user
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE ROLE IF NOT EXISTS 'role_read_only';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE USER IF NOT EXISTS '${GRAFANA_USER}'@'%' IDENTIFIED BY '${GRAFANA_PASS}';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${GRAFANA_USER}'@'%'" || true

if [ -z "${GRAFANA_TABLES}" ]; then
    # Legacy / fallback: grant SELECT on the entire database (not recommended for production)
    echo -e "${RED}Warning: GRAFANA_TABLES not set — granting SELECT on ${WEB_DB_NAME}.* (legacy).${NC}"
    mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "GRANT SELECT ON \`${WEB_DB_NAME}\`.* TO 'role_read_only';"
else
    # Grant SELECT on each listed table to the role
    IFS=',' read -ra _tables <<< "${GRAFANA_TABLES}"
    for _t in "${_tables[@]}"; do
        _t_trim=$(echo "${_t}" | xargs)
        if [ -n "${_t_trim}" ]; then
            mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "GRANT SELECT ON \`${WEB_DB_NAME}\`.\`${_t_trim}\` TO 'role_read_only';"
        fi
    done
fi

# Apply role to user
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "GRANT 'role_read_only' TO '${GRAFANA_USER}'@'%';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "SET DEFAULT ROLE 'role_read_only' FOR '${GRAFANA_USER}'@'%';"

mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;"
echo -e "${GREEN}--> Configuring MariaDB to allow remote connections (bind-address = 0.0.0.0)...${NC}"
if [ -f /etc/mysql/mariadb.conf.d/50-server.cnf ]; then
    sed -i 's/bind-address[[:space:]]*=[[:space:]]*127.0.0.1/bind-address = 0.0.0.0/' /etc/mysql/mariadb.conf.d/50-server.cnf
    cat > /etc/mysql/mariadb.conf.d/60-timezone.cnf <<EOF
[mysqld]
default_time_zone = SYSTEM
EOF
    cat > /etc/mysql/mariadb.conf.d/61-upload-tuning.cnf <<EOF
[mysqld]
max_allowed_packet = ${MAX_ALLOWED_PACKET}
net_read_timeout = ${DB_NET_READ_TIMEOUT}
net_write_timeout = ${DB_NET_WRITE_TIMEOUT}
wait_timeout = ${DB_WAIT_TIMEOUT}
interactive_timeout = ${DB_WAIT_TIMEOUT}
EOF
    systemctl restart mariadb
fi
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "SET GLOBAL time_zone = 'SYSTEM';" || true

echo -e "${GREEN}--> User ${WEB_DB_USER} can now create and manage databases via phpMyAdmin${NC}"

echo -e "${GREEN}--> Installing Nginx & PHP...${NC}"
apt install nginx php-fpm php-mysql php-mbstring php-zip php-gd php-json php-curl php-xml -y

PHP_VERSION=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;')
PHP_SOCKET="unix:/run/php/php${PHP_VERSION}-fpm.sock"
echo "Detected PHP Version: $PHP_VERSION (Socket: $PHP_SOCKET)"

echo -e "${GREEN}--> Configuring PHP Timezone to Europe/Amsterdam...${NC}"
# Use a more robust sed that handles spaces like "; date.timezone ="
sed -i 's/^;*[[:space:]]*date.timezone[[:space:]]*=.*/date.timezone = "Europe\/Amsterdam"/' /etc/php/${PHP_VERSION}/fpm/php.ini
sed -i 's/^;*[[:space:]]*date.timezone[[:space:]]*=.*/date.timezone = "Europe\/Amsterdam"/' /etc/php/${PHP_VERSION}/cli/php.ini

echo -e "${GREEN}--> Configuring PHP upload and request limits...${NC}"
for ini in /etc/php/${PHP_VERSION}/fpm/php.ini /etc/php/${PHP_VERSION}/cli/php.ini; do
    sed -i "s/^;*[[:space:]]*upload_max_filesize[[:space:]]*=.*/upload_max_filesize = ${MAX_UPLOAD_SIZE}/" "${ini}"
    sed -i "s/^;*[[:space:]]*post_max_size[[:space:]]*=.*/post_max_size = ${MAX_POST_SIZE}/" "${ini}"
    sed -i 's/^;*[[:space:]]*max_input_time[[:space:]]*=.*/max_input_time = 300/' "${ini}"
    sed -i 's/^;*[[:space:]]*max_execution_time[[:space:]]*=.*/max_execution_time = 300/' "${ini}"
done

# Large request bodies over FastCGI can need additional read time.
if [ -f /etc/php/${PHP_VERSION}/fpm/pool.d/www.conf ]; then
    if grep -q '^[;[:space:]]*request_terminate_timeout' /etc/php/${PHP_VERSION}/fpm/pool.d/www.conf; then
        sed -i 's|^[;[:space:]]*request_terminate_timeout[[:space:]]*=.*|request_terminate_timeout = 300s|' /etc/php/${PHP_VERSION}/fpm/pool.d/www.conf
    else
        echo 'request_terminate_timeout = 300s' >> /etc/php/${PHP_VERSION}/fpm/pool.d/www.conf
    fi
fi

# Restart PHP-FPM to apply changes
systemctl restart php${PHP_VERSION}-fpm


echo "phpmyadmin phpmyadmin/dbconfig-install boolean true" | debconf-set-selections
echo "phpmyadmin phpmyadmin/app-password-confirm password $MYSQL_ROOT_PASSWORD" | debconf-set-selections
echo "phpmyadmin phpmyadmin/mysql/admin-pass password $MYSQL_ROOT_PASSWORD" | debconf-set-selections
echo "phpmyadmin phpmyadmin/mysql/app-pass password $MYSQL_ROOT_PASSWORD" | debconf-set-selections
echo "phpmyadmin phpmyadmin/reconfigure-webserver multiselect" | debconf-set-selections
DEBIAN_FRONTEND=noninteractive apt install phpmyadmin -y

echo -e "${GREEN}--> Ensuring phpMyAdmin configuration storage/controluser is valid...${NC}"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE DATABASE IF NOT EXISTS phpmyadmin;"

# Ensure phpMyAdmin config storage tables exist (pma__* tables).
if [ -f /usr/share/phpmyadmin/sql/create_tables.sql ]; then
    mysql -u root -p"${MYSQL_ROOT_PASSWORD}" phpmyadmin < /usr/share/phpmyadmin/sql/create_tables.sql || true
fi

# Ensure controluser credentials match phpMyAdmin runtime config.
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE USER IF NOT EXISTS '${PHPMYADMIN_CONTROL_USER}'@'localhost' IDENTIFIED BY '${PHPMYADMIN_CONTROL_PASS}';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "ALTER USER '${PHPMYADMIN_CONTROL_USER}'@'localhost' IDENTIFIED BY '${PHPMYADMIN_CONTROL_PASS}';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "GRANT SELECT, INSERT, UPDATE, DELETE ON phpmyadmin.* TO '${PHPMYADMIN_CONTROL_USER}'@'localhost';"
mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;"

# Force controluser settings so phpMyAdmin doesn't depend on distro defaults.
mkdir -p /etc/phpmyadmin/conf.d
cat > /etc/phpmyadmin/conf.d/99-controluser.inc.php <<EOF
<?php
declare(strict_types=1);

if (!isset(
    \$cfg['Servers'][1],
    \$cfg['Servers'][1]['host']
)) {
    return;
}

\$cfg['Servers'][1]['pmadb'] = 'phpmyadmin';
\$cfg['Servers'][1]['controluser'] = '${PHPMYADMIN_CONTROL_USER}';
\$cfg['Servers'][1]['controlpass'] = '${PHPMYADMIN_CONTROL_PASS}';
EOF

# Remove standard symlink to keep it hidden
rm -f /var/www/html/phpmyadmin

echo -e "${GREEN}--> Deploying web application files...${NC}"
mkdir -p /var/www/html
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for dir in app config public; do
    if [ -d "${SOURCE_DIR}/$dir" ]; then
        echo -e "${GREEN}   -> Copying ${dir}...${NC}"
        rm -rf "/var/www/html/${dir}"
        cp -a "${SOURCE_DIR}/$dir" /var/www/html/
    fi
done

# Build marker used for cache-busting static assets.
date +%s > /var/www/html/public/.build-id

chown -R www-data:www-data /var/www/html
chmod -R 755 /var/www/html


echo -e "${GREEN}--> Configuring Nginx (Stage 1: HTTP Only)...${NC}"
cat > /etc/nginx/sites-available/default <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    root ${WEB_ROOT};
    index index.php index.html index.htm;

    # Max upload size (for ZIP/file attachments sent as base64 JSON)
    client_max_body_size ${MAX_POST_SIZE};

    location / {
        try_files \$uri \$uri/ =404;
    }

    # SSE endpoint: disable buffering so events flush immediately.
    location = /sse.php {
        include snippets/fastcgi-php.conf;
        fastcgi_pass ${PHP_SOCKET};
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_buffering off;
        fastcgi_request_buffering off;
    }

    location ~ \.php\$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass ${PHP_SOCKET};
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        include fastcgi_params;
    }

    location ~ /\.ht {
        deny all;
    }
}
EOF

ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/
nginx -t && systemctl restart nginx

# Configureer Nginx - Final configuration
echo -e "${GREEN}--> Configuring Nginx (Final)...${NC}"
cat > /etc/nginx/sites-available/default <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    root ${WEB_ROOT};
    index index.php index.html index.htm;

    # Max upload size (for ZIP/file attachments sent as base64 JSON)
    client_max_body_size ${MAX_POST_SIZE};

    location / {
        try_files \$uri \$uri/ =404;
    }

    # SSE endpoint: disable buffering so events flush immediately.
    location = /sse.php {
        include snippets/fastcgi-php.conf;
        fastcgi_pass ${PHP_SOCKET};
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_buffering off;
        fastcgi_request_buffering off;
    }

    location ~ \.php\$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass ${PHP_SOCKET};
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        include fastcgi_params;
    }

    location ~ /\.ht {
        deny all;
    }

    # phpMyAdmin (Op geheim pad)
    location ${PHPMYADMIN_ALIAS} {
        alias /usr/share/phpmyadmin;
        index index.php index.html index.htm;

        location ~ ^${PHPMYADMIN_ALIAS}/(.+\.php)\$ {
            alias /usr/share/phpmyadmin/\$1;
            fastcgi_pass ${PHP_SOCKET};
            fastcgi_index index.php;
            fastcgi_param SCRIPT_FILENAME \$request_filename;
            include fastcgi_params;
        }

        location ~* ^${PHPMYADMIN_ALIAS}/(.+\.(jpg|jpeg|gif|css|png|js|ico|html|xml|txt))\$ {
            alias /usr/share/phpmyadmin/\$1;
        }
    }
}
EOF

nginx -t && systemctl reload nginx
systemctl restart php${PHP_VERSION}-fpm


# End of installation steps


SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${OPENROUTER_API_KEY}" ] && [ -f "${SOURCE_DIR}/worker/.env" ]; then
    OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_KEY=' "${SOURCE_DIR}/worker/.env" | head -n1 | cut -d= -f2-)"
fi

if [ -f "${SOURCE_DIR}/schema.sql" ]; then
    echo -e "${GREEN}--> Importing schema.sql...${NC}"
    SCHEMA_IMPORT_FILE="${SOURCE_DIR}/schema.sql"
    TEMP_SCHEMA_FILE=""
    if grep -q '__OPENROUTER_API_KEY__' "${SOURCE_DIR}/schema.sql"; then
        TEMP_SCHEMA_FILE="$(mktemp)"
        ESCAPED_OPENROUTER_API_KEY="${OPENROUTER_API_KEY//\\/\\\\}"
        ESCAPED_OPENROUTER_API_KEY="${ESCAPED_OPENROUTER_API_KEY//&/\\&}"
        ESCAPED_OPENROUTER_API_KEY="${ESCAPED_OPENROUTER_API_KEY//\//\\/}"
        sed "s/__OPENROUTER_API_KEY__/${ESCAPED_OPENROUTER_API_KEY}/g" "${SOURCE_DIR}/schema.sql" > "${TEMP_SCHEMA_FILE}"
        SCHEMA_IMPORT_FILE="${TEMP_SCHEMA_FILE}"
    fi
    mysql -u root -p"${MYSQL_ROOT_PASSWORD}" "${WEB_DB_NAME}" < "${SCHEMA_IMPORT_FILE}"
    mysql -u root -p"${MYSQL_ROOT_PASSWORD}" "${WEB_DB_NAME}" -e "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS auto_approve_tools TINYINT(1) NULL DEFAULT NULL;"
    mysql -u root -p"${MYSQL_ROOT_PASSWORD}" "${WEB_DB_NAME}" -e "UPDATE mcp_servers SET type='streamable-http', url='http://${PROXMOX_MCP_IP}:${PROXMOX_MCP_PORT}${MCP_ENDPOINT_PATH}' WHERE name='Proxmox MCP';"
    mysql -u root -p"${MYSQL_ROOT_PASSWORD}" "${WEB_DB_NAME}" -e "UPDATE mcp_servers SET type='streamable-http', url='http://${KALI_MCP_IP}:${KALI_MCP_PORT}${MCP_ENDPOINT_PATH}' WHERE name='Kali MCP';"
    
    if [ -f "${SOURCE_DIR}/examples/persona.md" ]; then
        echo -e "${GREEN}--> Auto-importing persona.md...${NC}"
        PERSONA_CONTENT=$(cat "${SOURCE_DIR}/examples/persona.md" | sed "s/'/''/g")
        mysql -u root -p"${MYSQL_ROOT_PASSWORD}" "${WEB_DB_NAME}" -e "UPDATE users SET persona='${PERSONA_CONTENT}' WHERE username='user';"
    fi
    if [ -f "${SOURCE_DIR}/examples/infra.md" ]; then
        echo -e "${GREEN}--> Auto-importing infra.md...${NC}"
        INFRA_CONTENT=$(cat "${SOURCE_DIR}/examples/infra.md" | sed "s/'/''/g")
        mysql -u root -p"${MYSQL_ROOT_PASSWORD}" "${WEB_DB_NAME}" -e "UPDATE users SET blueprints='${INFRA_CONTENT}' WHERE username='user';"
    fi
    
    echo -e "${GREEN}--> Schema imported successfully.${NC}"
    if [ -n "${TEMP_SCHEMA_FILE}" ]; then
        rm -f "${TEMP_SCHEMA_FILE}"
    fi
    echo -e "${GREEN}--> MCP URLs configured:${NC}"
    echo -e "${GREEN}   Proxmox MCP: http://${PROXMOX_MCP_IP}:${PROXMOX_MCP_PORT}${MCP_ENDPOINT_PATH}${NC}"
    echo -e "${GREEN}   Kali MCP:    http://${KALI_MCP_IP}:${KALI_MCP_PORT}${MCP_ENDPOINT_PATH}${NC}"
else
    echo -e "${RED}Warning: schema.sql not found in ${SOURCE_DIR}, skipping DB initialization.${NC}"
fi

echo -e "${GREEN}--> Configuring Python Worker...${NC}"
apt install -y python3-full python3-pip
mkdir -p /opt/gui-worker

# Copy worker files if they exist in script directory
if [ -d "${SOURCE_DIR}/worker" ]; then
    cp -a ${SOURCE_DIR}/worker/. /opt/gui-worker/
fi

# Create .env for the worker
cat > /opt/gui-worker/.env <<EOF
DB_HOST=127.0.0.1
DB_NAME=${WEB_DB_NAME}
DB_USER=${WEB_DB_USER}
DB_PASS=${WEB_DB_PASSWORD}
LLM_MODEL=deepseek/deepseek-v4-flash
OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
OPENROUTER_KEY=${OPENROUTER_API_KEY}
EOF

# Setup Virtual Environment
cd /opt/gui-worker
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
./venv/bin/pip install -r requirements.txt

# Setup Systemd Service
if [ -f "${SOURCE_DIR}/deploy/systemd/gui-worker.service" ]; then
    cp "${SOURCE_DIR}/deploy/systemd/gui-worker.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable gui-worker.service
    systemctl restart gui-worker.service
    echo -e "${GREEN}--> Worker service started.${NC}"
else
    echo -e "${RED}Warning: gui-worker.service not found, skipping service setup.${NC}"
fi

echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}INSTALLATION COMPLETE!${NC}"
if [ "$ENABLE_SSL" = true ]; then
    PROTO="https"
else
    PROTO="http"
fi
echo -e "Website:      ${PROTO}://${DOMAIN}"
echo -e "phpMyAdmin:   ${PROTO}://${DOMAIN}${PHPMYADMIN_ALIAS}"
echo -e "Web Root:     ${WEB_ROOT}"
echo -e "DB Name:      ${WEB_DB_NAME}"
echo -e "DB User:      ${WEB_DB_USER}"
echo -e "DB Pass:      ${WEB_DB_PASSWORD}"
echo -e "Grafana User: ${GRAFANA_USER}"
echo -e "Grafana Pass: ${GRAFANA_PASS}"
echo -e "${GREEN}=============================================${NC}"

