# Proxmox MCP (Container-Friendly)

This package contains two services:

- `server.py`: Flask API wrapper around the Proxmox VE API
- `mcp_http_server.py`: MCP server exposing those API routes as tools

## What is improved

This version is designed to run from a dedicated container (not on the PVE host):

- Added Proxmox API token authentication (`PROXMOX_TOKEN_NAME`, `PROXMOX_TOKEN_VALUE`)
- Added task utility endpoints/tools:
  - `get_next_id`
  - `list_tasks`
  - `get_task_status`
  - `get_task_log`
- Added VM guest agent tools for remote command workflows without host shell access:
  - `vm_agent_ping`
  - `vm_agent_network`
  - `exec_vm_agent_command`
  - `get_vm_agent_exec_status`
- Added direct container file deployment support:
  - `sync_container_file`
  - Useful for pushing app files into LXC containers without building large shell heredocs
- Added host command SSH mode for containerized deployments:
  - `exec_host_command` can now run in `local` mode (default) or `ssh` mode
  - Use `exec_mode=ssh` to execute commands on the real Proxmox node (for example `PVE0`)
- Hardened host-local execution behavior:
  - `ENABLE_COMMAND_EXEC=false` by default
  - `ENABLE_PCT_CONTAINER_EXEC=false` by default
  - Supports `pct` execution modes for container deployments:
    - `PCT_EXEC_MODE=local` (default): run `pct` directly on the API host
    - `PCT_EXEC_MODE=ssh`: run `pct` via SSH on a Proxmox node
    - `PCT_EXEC_MODE=auto`: use local `pct` when present, otherwise SSH fallback

## Configuration

Copy `.env.example` to `.env` and adjust values:

```env
PROXMOX_HOST=10.0.30.10
PROXMOX_HOST_FALLBACKS=192.168.1.2
PROXMOX_PORT=8006
PROXMOX_USER=root@pam

# Use one auth method
PROXMOX_PASSWORD=
PROXMOX_TOKEN_NAME=root@pam!mcp
PROXMOX_TOKEN_VALUE=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

PROXMOX_VERIFY_SSL=false

API_PORT=5000
MCP_PORT=5002
BOOTSTRAP_PVE_SSH_KEY=true

ENABLE_COMMAND_EXEC=false
ENABLE_PCT_CONTAINER_EXEC=false

PCT_EXEC_MODE=local
PCT_SSH_HOST=10.0.30.10
PCT_SSH_PORT=22
PCT_SSH_USER=root
PCT_SSH_KEY_PATH=/root/.ssh/id_ed25519
PCT_SSH_CONNECT_TIMEOUT=10
PCT_SSH_STRICT_HOST_KEY_CHECKING=false
PCT_SSH_KNOWN_HOSTS_FILE=
```

## SSH-based container exec (recommended for API-in-container)

If `server.py` runs in a dedicated container/VM without local `pct`, use SSH mode:

```env
ENABLE_COMMAND_EXEC=true
ENABLE_PCT_CONTAINER_EXEC=true
PCT_EXEC_MODE=ssh
PCT_SSH_HOST=10.0.30.10
PCT_SSH_USER=root
PCT_SSH_KEY_PATH=/root/.ssh/id_ed25519
PCT_SSH_PASSWORD=
PCT_SSH_STRICT_HOST_KEY_CHECKING=false
```

Then ensure the API runtime can SSH to the Proxmox node with key auth:

```bash
ssh -i /root/.ssh/id_ed25519 root@10.0.30.10
```

The public half of that key must already be trusted by the Proxmox host's root account. If SSH returns "Permission denied (publickey,password)", install `id_ed25519.pub` into `/root/.ssh/authorized_keys` on the Proxmox node and rerun the service.

If that SSH command works passwordless, MCP `exec_container_command` will use it to run:

```bash
pct exec <vmid> -- /bin/sh -lc "<command>"
```

New containers default to `pve-data` for storage and `vmbr1` for the bridge, matching the project network layout.

The new `sync_container_file` tool uses the same local-or-SSH transport logic, but writes files with `pct push` and then applies optional `chmod`/`chown` inside the container.

## Run host commands on the real Proxmox node (PVE0)

When MCP runs in a helper container, `exec_host_command` with default `local` mode runs inside that container.

Use SSH mode to execute on the Proxmox node instead:

```json
{
  "command": "hostname && ip a",
  "exec_mode": "ssh",
  "ssh_host": "10.0.30.10"
}
```

Notes:

- `ssh_host` is optional if `PCT_SSH_HOST` (or `PROXMOX_HOST`) already points to the correct node.
- SSH auth/options reuse `PCT_SSH_USER`, `PCT_SSH_KEY_PATH`, `PCT_SSH_PORT`, and related SSH settings from `.env`.
- SSH target selection defaults to the currently active connected Proxmox host. Set `PCT_SSH_HOST` if you need to force a specific SSH endpoint.
- If key auth is not trusted yet, set `PCT_SSH_PASSWORD` and install `sshpass` so pct-over-SSH can authenticate with a password.

The setup script can auto-bootstrap SSH key trust to the Proxmox node. To enable fully automated bootstrap, set `PCT_SSH_PASSWORD` in `.env` and keep `BOOTSTRAP_PVE_SSH_KEY=true`.

## Run

Terminal 1:

```bash
python server.py --host 0.0.0.0 --port 5000
```

Terminal 2:

```bash
python mcp_http_server.py --host 0.0.0.0 --port 5002 --proxmox-url http://127.0.0.1:5000
```

MCP endpoint:

- `http://<container-ip>:5002/mcp`

Backward-compatible endpoint:

- `http://<container-ip>:5002/sse`

Legacy messages endpoint:

- `http://<container-ip>:5002/messages/`

## Operational recovery fallback

If `restart_server` reports success but the Flask API on port `5000` does not come back,
reboot the MCP runtime container instead. In this environment, MCP services are managed by
`systemd`, so a container reboot is a valid and reliable recovery path.

Example (from Proxmox host):

```bash
pct reboot 103
```

## Notes for container deployments

- VM guest-agent command execution requires `qemu-guest-agent` installed and running inside guest VMs.
- LXC `pct exec` is host-local and not available in normal unprivileged API containers.
- For production, prefer API token auth over password auth.
