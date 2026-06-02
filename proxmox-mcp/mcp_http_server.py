#!/usr/bin/env python3

"""
MCP HTTP Streaming Server for Proxmox VE

This server wraps the Flask API (server.py) and exposes Proxmox tools via MCP HTTP streaming transport.
Supports both modern HTTP streaming (Streamable HTTP) and legacy SSE fallback for compatibility.
OpenWebUI and other clients should use the /mcp endpoint (/sse remains backward-compatible).

Supports all Proxmox operations:
- Cluster management
- Node operations
- VM management (create, clone, start, stop, backup, etc.)
- Container (LXC) management
- Storage operations
- Snapshot management
- Backup/restore operations
- Monitoring and performance data

Usage:
    python3 mcp_http_server.py --port 5002
    # or
    python3 mcp_http_server.py --port 5002 --proxmox-url http://10.0.30.10:5000
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from urllib.parse import quote
from typing import Any, Dict

import requests
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger(__name__)

# Configuration
DEFAULT_PROXMOX_API = "http://localhost:5000"
DEFAULT_MCP_PORT = 5002
DEFAULT_REQUEST_TIMEOUT = 900  # 15 minutes

class ProxmoxMCPServer:
    """MCP Server wrapper for Proxmox HTTP API"""

    def __init__(
        self,
        proxmox_api_url: str = DEFAULT_PROXMOX_API,
        request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
        mcp_port: int = DEFAULT_MCP_PORT,
    ):
        self.proxmox_api_url = proxmox_api_url.rstrip("/")
        self.request_timeout = request_timeout
        self.mcp_port = mcp_port
        self.session = requests.Session()
        
        # Add API Key from environment if defined
        mcp_api_key = os.getenv("MCP_API_KEY", "")
        if mcp_api_key:
            self.session.headers.update({"Authorization": f"Bearer {mcp_api_key}"})

        # Create MCP server
        self.server = Server("proxmox-tools-sse")
        
        logger.info(f"Initializing Proxmox Tools MCP HTTP Server")
        logger.info(f"Proxmox API Server: {self.proxmox_api_url}")
        logger.info(f"MCP HTTP Port: {self.mcp_port}")

        # Register tool handlers using the proper Server API
        self._register_tools()

    def _register_tools(self):
        """Register all available Proxmox tools as MCP tools"""
        
        # Register tool request handler
        @self.server.call_tool()
        async def handle_tool_call(name: str, arguments: Dict[str, Any]) -> list:
            """Handle tool call requests with non-blocking execution."""
            try:
                # Use to_thread to prevent blocking the event loop
                result = await asyncio.to_thread(self._call_proxmox_tool, name, arguments)
                return [TextContent(type="text", text=result)]
            except Exception as e:
                error_msg = str(e)
                if hasattr(e, "response") and getattr(e, "response") is not None:
                    try:
                        resp_json = e.response.json()
                        if isinstance(resp_json, dict) and "error" in resp_json:
                            error_msg = f"{e} - API Error: {resp_json['error']}"
                    except Exception:
                        if hasattr(e.response, "text") and e.response.text:
                            error_msg = f"{e} - {e.response.text[:200]}"
                error = json.dumps({"error": error_msg, "success": False})
                return [TextContent(type="text", text=error)]
        
        # Register list tools handler
        @self.server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            """List available Proxmox tools as typed MCP Tool objects."""
            return [
                # Cluster & Node Management
                Tool(name="get_cluster_status", description="Get cluster status and resource summary", inputSchema={"type": "object", "properties": {}}),
                Tool(name="get_next_id", description="Get the next available VM/CT ID from the cluster", inputSchema={"type": "object", "properties": {}}),
                Tool(name="list_tasks", description="List recent cluster tasks", inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Optional: maximum tasks to return"},
                        "source": {"type": "string", "description": "Optional: task source filter"}
                    }
                }),
                Tool(name="get_task_status", description="Get status for a task by UPID", inputSchema={
                    "type": "object",
                    "properties": {
                        "upid": {"type": "string", "description": "Task UPID"},
                        "node": {"type": "string", "description": "Optional: node if UPID parsing is unavailable"}
                    },
                    "required": ["upid"]
                }),
                Tool(name="get_task_log", description="Get log for a task by UPID", inputSchema={
                    "type": "object",
                    "properties": {
                        "upid": {"type": "string", "description": "Task UPID"},
                        "node": {"type": "string", "description": "Optional: node if UPID parsing is unavailable"},
                        "start": {"type": "integer", "description": "Optional: log start offset"},
                        "limit": {"type": "integer", "description": "Optional: maximum log entries"}
                    },
                    "required": ["upid"]
                }),
                Tool(name="get_nodes", description="List all nodes in the cluster", inputSchema={"type": "object", "properties": {}}),
                Tool(name="get_node_status", description="Get detailed status of a specific node", inputSchema={
                    "type": "object",
                    "properties": {"node": {"type": "string", "description": "Node name (e.g. 'pve')"}},
                    "required": ["node"]
                }),
                
                # VM Management
                Tool(name="list_vms", description="List all VMs across all nodes", inputSchema={
                    "type": "object",
                    "properties": {"node": {"type": "string", "description": "Optional: specific node name"}}
                }),
                Tool(name="get_vm_status", description="Get detailed status of a specific VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="get_vm_config", description="Get VM configuration", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="start_vm", description="Start a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="stop_vm", description="Stop a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="shutdown_vm", description="Gracefully shutdown a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="reboot_vm", description="Reboot a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="reset_vm", description="Hard reset a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="suspend_vm", description="Suspend a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="resume_vm", description="Resume a suspended VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="delete_vm", description="Delete a VM (destructive)", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="create_vm", description="Create a new VM with specified parameters", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Target node name"},
                        "vmid": {"type": "integer", "description": "New VM ID (must be unique)"},
                        "name": {"type": "string", "description": "VM name"},
                        "memory": {"type": "integer", "description": "Memory in MB (default: 2048)"},
                        "cores": {"type": "integer", "description": "CPU cores (default: 2)"},
                        "sockets": {"type": "integer", "description": "CPU sockets (default: 1)"},
                        "cpu": {"type": "string", "description": "CPU type (default: host)"},
                        "scsi0": {"type": "string", "description": "SCSI disk (e.g., local-lvm:50)"},
                        "net0": {"type": "string", "description": "Network config (e.g., virtio,bridge=vmbr0)"},
                        "ide2": {"type": "string", "description": "IDE device for ISO (e.g., local:iso/ubuntu.iso,media=cdrom)"},
                        "boot": {"type": "string", "description": "Boot order (e.g., order=scsi0,net0)"},
                        "description": {"type": "string", "description": "VM description"}
                    },
                    "required": ["node", "vmid", "name"]
                }),
                Tool(name="migrate_vm", description="Migrate a VM to another node (live or offline)", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Current node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "target": {"type": "string", "description": "Target node name"},
                        "online": {"type": "boolean", "description": "Live migration (default: true)"},
                        "force": {"type": "boolean", "description": "Force migration"}
                    },
                    "required": ["node", "vmid", "target"]
                }),
                Tool(name="clone_vm", description="Clone a VM to create a new one", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Source VM ID"},
                        "newid": {"type": "integer", "description": "New VM ID"},
                        "name": {"type": "string", "description": "Optional: name for the new VM"},
                        "full": {"type": "boolean", "description": "Create a full clone"}
                    },
                    "required": ["node", "vmid", "newid"]
                }),
                Tool(name="resize_vm_disk", description="Resize a VM disk", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "disk": {"type": "string", "description": "Disk identifier (e.g., scsi0, rootfs)"},
                        "size": {"type": "string", "description": "New size (e.g., +10G to add 10GB)"}
                    },
                    "required": ["node", "vmid", "disk", "size"]
                }),
                Tool(name="vm_agent_ping", description="Ping QEMU guest agent in a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="vm_agent_network", description="Get guest network interfaces via QEMU guest agent", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="exec_vm_agent_command", description="Execute a command in a VM via QEMU guest agent", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "command": {"type": "string", "description": "Command path to execute in guest"},
                        "args": {"type": "array", "description": "Optional command arguments", "items": {"type": "string"}},
                        "capture_output": {"type": "boolean", "description": "Capture stdout/stderr"},
                        "input_data": {"type": "string", "description": "Optional stdin payload"}
                    },
                    "required": ["node", "vmid", "command"]
                }),
                Tool(name="get_vm_agent_exec_status", description="Get status/output for a prior guest agent command", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "pid": {"type": "integer", "description": "Guest agent exec PID"}
                    },
                    "required": ["node", "vmid", "pid"]
                }),
                
                # Container Management
                Tool(name="list_containers", description="List all LXC containers", inputSchema={
                    "type": "object",
                    "properties": {"node": {"type": "string", "description": "Optional: specific node name"}}
                }),
                Tool(name="get_container_status", description="Get detailed status of a container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="start_container", description="Start a container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="stop_container", description="Stop a container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="shutdown_container", description="Gracefully shutdown a container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="reboot_container", description="Reboot a container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="delete_container", description="Delete a container (destructive)", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="create_container", description="Create a new LXC container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Target node name"},
                        "vmid": {"type": "integer", "description": "New container ID (must be unique)"},
                        "hostname": {"type": "string", "description": "Container hostname"},
                        "ostype": {"type": "string", "description": "OS type (ubuntu, debian, alpine, etc.)"},
                        "memory": {"type": "integer", "description": "Memory in MB (default: 512)"},
                        "cores": {"type": "integer", "description": "CPU cores (default: 1)"},
                        "rootfs": {"type": "string", "description": "Root filesystem (e.g., local-lvm:4 for 4GB)"},
                        "ostemplate": {"type": "string", "description": "OS template to use"},
                        "net0": {"type": "string", "description": "Network config (e.g., name=eth0,bridge=vmbr0,ip=dhcp)"},
                        "swap": {"type": "integer", "description": "Swap in MB"},
                        "password": {"type": "string", "description": "Root password"},
                        "description": {"type": "string", "description": "Container description"},
                        "start": {"type": "boolean", "description": "Start container after creation"}
                    },
                    "required": ["node", "vmid", "hostname", "ostype"]
                }),
                Tool(name="clone_container", description="Clone a container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Source container ID"},
                        "newid": {"type": "integer", "description": "New container ID"},
                        "hostname": {"type": "string", "description": "Optional: hostname for the new container"}
                    },
                    "required": ["node", "vmid", "newid"]
                }),
                Tool(name="exec_container_command", description="Execute a shell command inside a container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"},
                        "command": {"type": "string", "description": "Shell command to run inside the container"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30, max set by server)"}
                    },
                    "required": ["node", "vmid", "command"]
                }),
                Tool(name="get_container_details", description="Get container status/config and best-effort guest IPv4 details", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"},
                        "ip_timeout": {"type": "integer", "description": "Optional timeout (seconds) for in-guest IP discovery"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="verify_container_web", description="Strictly verify service + HTTP reachability from inside a container", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"},
                        "service": {"type": "string", "description": "Systemd service name (default: apache2)"},
                        "port": {"type": "integer", "description": "HTTP port to probe (default: 80)"},
                        "path": {"type": "string", "description": "HTTP path to probe (default: /)"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30, max set by server)"},
                        "install_probe": {"type": "boolean", "description": "Install curl when probe tools are missing (default: true)"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="sync_container_file", description="Write a file directly into a container via pct push, with optional mode/owner/group handling", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "Container ID"},
                        "path": {"type": "string", "description": "Absolute destination path inside the container"},
                        "content": {"type": "string", "description": "Full file content to write"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30, max set by server)"},
                        "mode": {"type": "string", "description": "POSIX file mode to apply, e.g. 0644"},
                        "owner": {"type": "string", "description": "Optional owner to apply, e.g. www-data"},
                        "group": {"type": "string", "description": "Optional group to apply, e.g. www-data"},
                        "create_dirs": {"type": "boolean", "description": "Create parent directories if needed (default: true)"}
                    },
                    "required": ["node", "vmid", "path", "content"]
                }),
                Tool(name="deploy_container_from_zip", description="Upload a zip archive, optionally create a new container (or use an existing one), unpack the archive, and start the app as a service", inputSchema={
                    "type": "object",
                    "properties": {
                        "create_container": {"type": "boolean", "description": "Set to false to deploy the zip into an existing container instead of creating a new one. Default is true."},
                        "node": {"type": "string", "description": "Target node name; optional if the server can infer a default"},
                        "vmid": {"type": "integer", "description": "Optional container ID; if omitted the next available ID is used"},
                        "hostname": {"type": "string", "description": "Container hostname"},
                        "ostemplate": {"type": "string", "description": "Container template (optional; auto-detected if omitted)"},
                        "ostype": {"type": "string", "description": "OS type override"},
                        "storage": {"type": "string", "description": "Storage for the container rootfs"},
                        "rootfs": {"type": "string", "description": "Root filesystem size or full rootfs spec"},
                        "memory": {"type": "integer", "description": "Memory in MB"},
                        "cores": {"type": "integer", "description": "CPU cores"},
                        "swap": {"type": "integer", "description": "Swap in MB"},
                        "net0": {"type": "string", "description": "Network config"},
                        "description": {"type": "string", "description": "Container description"},
                        "password": {"type": "string", "description": "Root password"},
                        "start": {"type": "boolean", "description": "Start container after creation"},
                        "name": {"type": "string", "description": "Application name"},
                        "port": {"type": "integer", "description": "Service port"},
                        "workdir": {"type": "string", "description": "Application work directory inside the container"},
                        "service_name": {"type": "string", "description": "systemd service name override"},
                        "start_command": {"type": "string", "description": "Explicit command to run as the service"},
                        "install_commands": {"type": "array", "items": {"type": "string"}, "description": "Optional shell commands to run before starting the service"},
                        "archive_name": {"type": "string", "description": "Archive filename used for display only"},
                        "zip_base64": {"type": "string", "description": "Base64-encoded zip archive contents"}
                    },
                    "required": ["zip_base64"]
                }),
                Tool(name="exec_host_command", description="Execute a shell command on the Proxmox host running this API", inputSchema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run on the host"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30, max set by server)"},
                        "cwd": {"type": "string", "description": "Optional working directory"},
                        "exec_mode": {"type": "string", "description": "Execution mode: local (default) or ssh"},
                        "ssh_host": {"type": "string", "description": "Optional SSH host override (used with exec_mode=ssh)"}
                    },
                    "required": ["command"]
                }),
                
                # Storage Management
                Tool(name="list_storage", description="List all storage", inputSchema={
                    "type": "object",
                    "properties": {"node": {"type": "string", "description": "Optional: specific node name"}}
                }),
                Tool(name="get_storage_status", description="Get storage status and usage", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "storage": {"type": "string", "description": "Storage name"}
                    },
                    "required": ["node", "storage"]
                }),
                Tool(name="list_storage_content", description="List storage content (ISOs, templates, backups)", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "storage": {"type": "string", "description": "Storage name"},
                        "content": {"type": "string", "description": "Content type (iso, vztmpl, backup, images)"}
                    },
                    "required": ["node", "storage"]
                }),
                
                # Snapshot Management
                Tool(name="list_vm_snapshots", description="List all snapshots for a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="create_vm_snapshot", description="Create a snapshot of a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "snapname": {"type": "string", "description": "Snapshot name"},
                        "description": {"type": "string", "description": "Optional: snapshot description"}
                    },
                    "required": ["node", "vmid", "snapname"]
                }),
                Tool(name="delete_vm_snapshot", description="Delete a VM snapshot", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "snapname": {"type": "string", "description": "Snapshot name"}
                    },
                    "required": ["node", "vmid", "snapname"]
                }),
                Tool(name="rollback_vm_snapshot", description="Rollback VM to a snapshot", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "snapname": {"type": "string", "description": "Snapshot name"}
                    },
                    "required": ["node", "vmid", "snapname"]
                }),
                
                # Backup Management
                Tool(name="list_backups", description="List all backups", inputSchema={
                    "type": "object",
                    "properties": {"node": {"type": "string", "description": "Optional: specific node name"}}
                }),
                Tool(name="backup_vm", description="Create a backup of a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "storage": {"type": "string", "description": "Storage for backup"},
                        "mode": {"type": "string", "description": "Backup mode (snapshot, suspend, stop)"},
                        "compress": {"type": "string", "description": "Compression (0, 1, gzip, lzo, zstd)"}
                    },
                    "required": ["node", "vmid"]
                }),
                
                # Monitoring
                Tool(name="get_vm_monitoring", description="Get VM performance data (RRD)", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "timeframe": {"type": "string", "description": "Timeframe (hour, day, week, month, year)"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="get_node_monitoring", description="Get node performance data (RRD)", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "timeframe": {"type": "string", "description": "Timeframe (hour, day, week, month, year)"}
                    },
                    "required": ["node"]
                }),
                
                # Network & Templates
                Tool(name="list_networks", description="List network interfaces on a node", inputSchema={
                    "type": "object",
                    "properties": {"node": {"type": "string", "description": "Node name"}},
                    "required": ["node"]
                }),
                Tool(name="list_templates", description="List available VM templates", inputSchema={
                    "type": "object",
                    "properties": {"node": {"type": "string", "description": "Optional: specific node name"}}
                }),
                Tool(name="list_isos", description="List available ISO images", inputSchema={
                    "type": "object",
                    "properties": {"node": {"type": "string", "description": "Optional: specific node name"}}
                }),
                
                # High Availability (HA) Management
                Tool(name="get_vm_ha_status", description="Get HA status for a specific VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"}
                    },
                    "required": ["node", "vmid"]
                }),
                Tool(name="set_vm_ha", description="Enable or configure HA for a VM", inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name"},
                        "vmid": {"type": "integer", "description": "VM ID"},
                        "state": {"type": "string", "description": "HA state (enabled, disabled, stopped)"},
                        "group": {"type": "string", "description": "Optional: HA group name"}
                    },
                    "required": ["node", "vmid", "state"]
                }),
                Tool(name="get_ha_status", description="Get overall HA cluster status", inputSchema={
                    "type": "object",
                    "properties": {}
                }),
                Tool(name="list_ha_resources", description="List all HA-protected resources", inputSchema={
                    "type": "object",
                    "properties": {}
                }),

                # Server self-management
                Tool(name="sync_server_file", description="Push a file from the local workspace to the MCP server host. Use this to deploy updated server.py or mcp_http_server.py without needing git.", inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path within the server working directory (e.g. 'server.py')"},
                        "content": {"type": "string", "description": "Full file content to write"}
                    },
                    "required": ["path", "content"]
                }),
                Tool(name="restart_server", description="Restart the MCP Flask API server process to apply synced code changes", inputSchema={
                    "type": "object",
                    "properties": {}
                }),
            ]
        
        logger.info("All Proxmox tools registered!")

    def _call_proxmox_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call the appropriate Proxmox API endpoint"""
        try:
            # Cluster operations
            if tool_name == "get_cluster_status":
                url = f"{self.proxmox_api_url}/api/cluster/status"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "get_next_id":
                url = f"{self.proxmox_api_url}/api/cluster/nextid"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "list_tasks":
                url = f"{self.proxmox_api_url}/api/tasks"
                params = {}
                if "limit" in arguments:
                    params["limit"] = arguments["limit"]
                if "source" in arguments:
                    params["source"] = arguments["source"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "get_task_status":
                upid = arguments.get("upid")
                if not upid:
                    return json.dumps({"success": False, "error": "upid is required"})
                upid_path = quote(str(upid), safe="")
                url = f"{self.proxmox_api_url}/api/tasks/{upid_path}/status"
                params = {}
                if "node" in arguments:
                    params["node"] = arguments["node"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "get_task_log":
                upid = arguments.get("upid")
                if not upid:
                    return json.dumps({"success": False, "error": "upid is required"})
                upid_path = quote(str(upid), safe="")
                url = f"{self.proxmox_api_url}/api/tasks/{upid_path}/log"
                params = {}
                if "node" in arguments:
                    params["node"] = arguments["node"]
                if "start" in arguments:
                    params["start"] = arguments["start"]
                if "limit" in arguments:
                    params["limit"] = arguments["limit"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # Node operations
            elif tool_name == "get_nodes":
                url = f"{self.proxmox_api_url}/api/nodes"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "get_node_status":
                node = arguments.get("node")
                url = f"{self.proxmox_api_url}/api/nodes/{node}"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # VM operations
            elif tool_name == "list_vms":
                url = f"{self.proxmox_api_url}/api/vms"
                params = {}
                if "node" in arguments:
                    params["node"] = arguments["node"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "get_vm_status":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "get_vm_config":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/config"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "start_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/start"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "stop_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/stop"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "shutdown_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/shutdown"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "reboot_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/reboot"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "reset_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/reset"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "suspend_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/suspend"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "resume_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/resume"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "delete_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/delete"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "create_vm":
                url = f"{self.proxmox_api_url}/api/vms/create"
                data = {
                    "node": arguments.get("node"),
                    "vmid": arguments.get("vmid"),
                    "name": arguments.get("name"),
                    "memory": arguments.get("memory"),
                    "cores": arguments.get("cores"),
                    "sockets": arguments.get("sockets"),
                    "cpu": arguments.get("cpu"),
                    "sata0": arguments.get("sata0"),
                    "scsi0": arguments.get("scsi0"),
                    "net0": arguments.get("net0"),
                    "ide2": arguments.get("ide2"),
                    "boot": arguments.get("boot"),
                    "description": arguments.get("description"),
                }
                # Remove None values
                data = {k: v for k, v in data.items() if v is not None}
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "migrate_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/migrate"
                data = {
                    "target": arguments.get("target"),
                    "online": arguments.get("online", True),
                    "force": arguments.get("force", False)
                }
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "clone_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/clone"
                data = {
                    "newid": arguments.get("newid"),
                    "name": arguments.get("name"),
                    "full": arguments.get("full", False)
                }
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "resize_vm_disk":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/resize-disk"
                data = {
                    "disk": arguments.get("disk", "scsi0"),
                    "size": arguments.get("size")
                }
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "vm_agent_ping":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/agent/ping"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "vm_agent_network":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/agent/network"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "exec_vm_agent_command":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/agent/exec"
                data = {
                    "command": arguments.get("command"),
                    "args": arguments.get("args", []),
                    "capture_output": arguments.get("capture_output", True),
                    "input_data": arguments.get("input_data"),
                }
                data = {k: v for k, v in data.items() if v is not None}
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "get_vm_agent_exec_status":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/agent/exec-status"
                data = {"pid": arguments.get("pid")}
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # Container operations
            elif tool_name == "list_containers":
                url = f"{self.proxmox_api_url}/api/containers"
                params = {}
                if "node" in arguments:
                    params["node"] = arguments["node"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "get_container_status":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "start_container":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/start"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "stop_container":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/stop"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "shutdown_container":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/shutdown"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "reboot_container":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/reboot"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "delete_container":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/delete"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "create_container":
                url = f"{self.proxmox_api_url}/api/containers/create"
                data = {
                    "node": arguments.get("node"),
                    "vmid": arguments.get("vmid"),
                    "hostname": arguments.get("hostname"),
                    "ostype": arguments.get("ostype"),
                    "memory": arguments.get("memory"),
                    "cores": arguments.get("cores"),
                    "rootfs": arguments.get("rootfs"),
                    "ostemplate": arguments.get("ostemplate"),
                    "storage": arguments.get("storage"),
                    "net0": arguments.get("net0"),
                    "swap": arguments.get("swap"),
                    "password": arguments.get("password"),
                    "description": arguments.get("description"),
                    "start": arguments.get("start"),
                }
                # Remove None values
                data = {k: v for k, v in data.items() if v is not None}
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "clone_container":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/clone"
                data = {
                    "newid": arguments.get("newid"),
                    "hostname": arguments.get("hostname")
                }
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "exec_container_command":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/exec"
                data = {
                    "command": arguments.get("command"),
                    "timeout": arguments.get("timeout", 30),
                }
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "get_container_details":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/details"
                params = {}
                if "ip_timeout" in arguments:
                    params["ip_timeout"] = arguments.get("ip_timeout")
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "verify_container_web":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/verify-web"
                data = {
                    "service": arguments.get("service"),
                    "port": arguments.get("port"),
                    "path": arguments.get("path"),
                    "timeout": arguments.get("timeout", 30),
                    "install_probe": arguments.get("install_probe"),
                }
                data = {k: v for k, v in data.items() if v is not None}
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "sync_container_file":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/containers/{node}/{vmid}/sync-file"
                data = {
                    "path": arguments.get("path"),
                    "content": arguments.get("content"),
                    "timeout": arguments.get("timeout", 30),
                    "mode": arguments.get("mode", "0644"),
                    "owner": arguments.get("owner"),
                    "group": arguments.get("group"),
                    "create_dirs": arguments.get("create_dirs", True),
                }
                data = {k: v for k, v in data.items() if v is not None}
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "deploy_container_from_zip":
                url = f"{self.proxmox_api_url}/api/deploy/container-from-zip"
                data = {
                    "create_container": arguments.get("create_container"),
                    "node": arguments.get("node"),
                    "vmid": arguments.get("vmid"),
                    "hostname": arguments.get("hostname"),
                    "ostemplate": arguments.get("ostemplate"),
                    "ostype": arguments.get("ostype"),
                    "storage": arguments.get("storage"),
                    "rootfs": arguments.get("rootfs"),
                    "memory": arguments.get("memory"),
                    "cores": arguments.get("cores"),
                    "swap": arguments.get("swap"),
                    "net0": arguments.get("net0"),
                    "description": arguments.get("description"),
                    "password": arguments.get("password"),
                    "start": arguments.get("start"),
                    "name": arguments.get("name"),
                    "port": arguments.get("port"),
                    "workdir": arguments.get("workdir"),
                    "service_name": arguments.get("service_name"),
                    "start_command": arguments.get("start_command"),
                    "install_commands": arguments.get("install_commands"),
                    "archive_name": arguments.get("archive_name"),
                    "zip_base64": arguments.get("zip_base64"),
                }
                data = {k: v for k, v in data.items() if v is not None}
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "exec_host_command":
                url = f"{self.proxmox_api_url}/api/host/exec"
                data = {
                    "command": arguments.get("command"),
                    "timeout": arguments.get("timeout", 30),
                    "cwd": arguments.get("cwd"),
                    "exec_mode": arguments.get("exec_mode"),
                    "ssh_host": arguments.get("ssh_host"),
                }
                data = {k: v for k, v in data.items() if v is not None}
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # Storage operations
            elif tool_name == "list_storage":
                url = f"{self.proxmox_api_url}/api/storage"
                params = {}
                if "node" in arguments:
                    params["node"] = arguments["node"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "get_storage_status":
                node, storage = arguments.get("node"), arguments.get("storage")
                url = f"{self.proxmox_api_url}/api/storage/{node}/{storage}"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "list_storage_content":
                node, storage = arguments.get("node"), arguments.get("storage")
                url = f"{self.proxmox_api_url}/api/storage/{node}/{storage}/content"
                params = {}
                if "content" in arguments:
                    params["content"] = arguments["content"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # Snapshot operations
            elif tool_name == "list_vm_snapshots":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/snapshots"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "create_vm_snapshot":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/snapshots"
                data = {
                    "snapname": arguments.get("snapname"),
                    "description": arguments.get("description", "")
                }
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "delete_vm_snapshot":
                node, vmid, snapname = arguments.get("node"), arguments.get("vmid"), arguments.get("snapname")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/snapshots/{snapname}"
                response = requests.delete(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "rollback_vm_snapshot":
                node, vmid, snapname = arguments.get("node"), arguments.get("vmid"), arguments.get("snapname")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/snapshots/{snapname}/rollback"
                response = self.session.post(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # Backup operations
            elif tool_name == "list_backups":
                url = f"{self.proxmox_api_url}/api/backups"
                params = {}
                if "node" in arguments:
                    params["node"] = arguments["node"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "backup_vm":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/backup"
                data = {
                    "storage": arguments.get("storage", "local"),
                    "mode": arguments.get("mode", "snapshot"),
                    "compress": arguments.get("compress", "zstd")
                }
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # Monitoring
            elif tool_name == "get_vm_monitoring":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/monitoring"
                params = {}
                if "timeframe" in arguments:
                    params["timeframe"] = arguments["timeframe"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "get_node_monitoring":
                node = arguments.get("node")
                url = f"{self.proxmox_api_url}/api/nodes/{node}/monitoring"
                params = {}
                if "timeframe" in arguments:
                    params["timeframe"] = arguments["timeframe"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # Network & Templates
            elif tool_name == "list_networks":
                node = arguments.get("node")
                url = f"{self.proxmox_api_url}/api/nodes/{node}/networks"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "list_templates":
                url = f"{self.proxmox_api_url}/api/templates"
                params = {}
                if "node" in arguments:
                    params["node"] = arguments["node"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "list_isos":
                url = f"{self.proxmox_api_url}/api/isos"
                params = {}
                if "node" in arguments:
                    params["node"] = arguments["node"]
                response = self.session.get(url, params=params, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            # HA Management
            elif tool_name == "get_vm_ha_status":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/ha"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "set_vm_ha":
                node, vmid = arguments.get("node"), arguments.get("vmid")
                url = f"{self.proxmox_api_url}/api/vms/{node}/{vmid}/ha"
                data = {
                    "state": arguments.get("state"),
                    "group": arguments.get("group"),
                }
                # Remove None values
                data = {k: v for k, v in data.items() if v is not None}
                response = requests.put(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "get_ha_status":
                url = f"{self.proxmox_api_url}/api/ha/status"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())
            
            elif tool_name == "list_ha_resources":
                url = f"{self.proxmox_api_url}/api/ha/resources"
                response = self.session.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "sync_server_file":
                url = f"{self.proxmox_api_url}/api/server/sync-file"
                data = {
                    "path": arguments.get("path"),
                    "content": arguments.get("content"),
                }
                response = self.session.post(url, json=data, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            elif tool_name == "restart_server":
                url = f"{self.proxmox_api_url}/api/server/restart"
                response = self.session.post(url, json={}, timeout=self.request_timeout)
                response.raise_for_status()
                return json.dumps(response.json())

            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}", "success": False})
        
        except requests.exceptions.RequestException as e:
            error_msg = f"HTTP error: {str(e)}"
            if hasattr(e, "response") and e.response is not None:
                try:
                    resp_json = e.response.json()
                    if isinstance(resp_json, dict) and "error" in resp_json:
                        error_msg = f"API Error: {resp_json['error']}"
                except Exception:
                    if hasattr(e.response, "text") and e.response.text:
                        error_msg = f"HTTP error: {str(e)} - {e.response.text[:200]}"
            
            logger.error(f"Error calling {tool_name}: {error_msg}")
            return json.dumps({"error": error_msg, "success": False})
        except Exception as e:
            logger.error(f"Error calling {tool_name}: {e}")
            return json.dumps({"error": str(e), "success": False})

    async def run_sse(self, host: str = "0.0.0.0"):
        """Run the MCP server with Streamable HTTP + legacy SSE transport."""
        import uuid
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware

        sse_transport = SseServerTransport("/messages/")

        try:
            from mcp.server.streamable_http import StreamableHTTPServerTransport
            streamable_available = True
            logger.info("Streamable HTTP transport enabled")
        except ImportError:
            streamable_available = False
            logger.warning(
                "mcp.server.streamable_http not found; falling back to legacy SSE only"
            )

        sessions: dict[str, Any] = {}

        def _scope_header(scope, name: str) -> str | None:
            header_name = name.lower().encode("latin-1")
            for key, value in scope.get("headers", []):
                if key.lower() == header_name:
                    return value.decode("latin-1")
            return None

        async def _send_plain_response(send, status: int, body: str):
            await send({
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            })
            await send({
                "type": "http.response.body",
                "body": body.encode("utf-8"),
                "more_body": False,
            })

        async def _ensure_streamable_session(session_id: str):
            existing = sessions.get(session_id)
            if existing:
                await existing["ready"].wait()
                return existing["transport"]

            transport = StreamableHTTPServerTransport(mcp_session_id=session_id)
            ready = asyncio.Event()

            async def _run_session():
                try:
                    async with transport.connect() as streams:
                        ready.set()
                        await self.server.run(
                            streams[0],
                            streams[1],
                            self.server.create_initialization_options(),
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(f"Streamable session {session_id} crashed: {exc}", exc_info=True)
                finally:
                    ready.set()

            task = asyncio.create_task(_run_session())
            sessions[session_id] = {"transport": transport, "task": task, "ready": ready}
            await ready.wait()
            return transport

        def _cleanup_streamable_sessions():
            stale_ids = []
            for session_id, session in sessions.items():
                transport = session["transport"]
                task = session["task"]
                if transport.is_terminated or task.done():
                    stale_ids.append(session_id)
            for session_id in stale_ids:
                sessions.pop(session_id, None)

        async def handle_sse_route(scope, receive, send):
            # Use standard SSE transport for better compatibility with all MCP clients
            try:
                logger.debug(f"Handling GET stream route request from {scope.get('client')}")
                async with sse_transport.connect_sse(scope, receive, send) as streams:
                    logger.info("SSE Connection established, running MCP server...")
                    await self.server.run(
                        streams[0],
                        streams[1],
                        self.server.create_initialization_options(),
                    )
            except Exception as e:
                logger.error(f"Error in handle_sse_route: {e}", exc_info=True)
                raise

        async def handle_streamable_route(scope, receive, send):
            if not streamable_available:
                await _send_plain_response(
                    send,
                    501,
                    "Streamable HTTP transport is unavailable. Upgrade mcp package.",
                )
                return

            request_session_id = _scope_header(scope, "mcp-session-id")
            if request_session_id:
                session = sessions.get(request_session_id)
                if not session:
                    await _send_plain_response(send, 404, "Invalid or expired session ID")
                    return
                transport = session["transport"]
            else:
                new_session_id = str(uuid.uuid4())
                transport = await _ensure_streamable_session(new_session_id)

            await transport.handle_request(scope, receive, send)
            _cleanup_streamable_sessions()

        async def asgi_app(scope, receive, send):
            """Minimal ASGI router that avoids Starlette endpoint wrapping."""
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return

            if scope["type"] != "http":
                return

            path = scope.get("path", "")
            method = scope.get("method", "GET").upper()
            mcp_session_id = _scope_header(scope, "mcp-session-id")
            if path in ("/sse", "/mcp"):
                try:
                    if method == "GET" and not mcp_session_id:
                        await handle_sse_route(scope, receive, send)
                    elif method in ("POST", "DELETE") or mcp_session_id:
                        await handle_streamable_route(scope, receive, send)
                    else:
                        await _send_plain_response(send, 405, "Method Not Allowed")
                except asyncio.CancelledError:
                    logger.debug("MCP session cancelled")
                except Exception as exc:
                    logger.error(f"MCP {path} route error: {exc}", exc_info=True)
            elif path.startswith("/messages"):
                try:
                    logger.debug(f"Handling POST {path}")
                    await sse_transport.handle_post_message(scope, receive, send)
                except Exception as exc:
                    logger.error(f"MCP /messages error: {exc}", exc_info=True)
            else:
                await send({"type": "http.response.start", "status": 404, "headers": []})
                await send({"type": "http.response.body", "body": b"Not Found", "more_body": False})

        app = CORSMiddleware(
            asgi_app,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )

        logger.info(f"Starting MCP HTTP Server on http://{host}:{self.mcp_port}/mcp")
        logger.info(f"Backward-compatible endpoint available at http://{host}:{self.mcp_port}/sse")
        logger.info(f"MCP messages endpoint available at http://{host}:{self.mcp_port}/messages/")
        logger.info("Server running - waiting for connections...")

        config = uvicorn.Config(app=app, host=host, port=self.mcp_port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Proxmox MCP HTTP Server")
    parser.add_argument("--port", type=int, default=DEFAULT_MCP_PORT, help=f"MCP port (default: {DEFAULT_MCP_PORT})")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--proxmox-url", default=DEFAULT_PROXMOX_API, help=f"Proxmox API URL (default: {DEFAULT_PROXMOX_API})")
    
    args = parser.parse_args()
    
    mcp_server = ProxmoxMCPServer(
        proxmox_api_url=args.proxmox_url,
        mcp_port=args.port
    )
    
    logger.info(f"Starting MCP HTTP Server on {args.host}:{args.port}")
    logger.info(f"OpenWebUI: http://{args.host}:{args.port}/mcp")
    
    try:
        await mcp_server.run_sse(args.host)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
