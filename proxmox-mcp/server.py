#!/usr/bin/env python3

"""
Proxmox VE HTTP API Server

This Flask server exposes comprehensive Proxmox VE management tools via HTTP.
Allows AI agents and tools to interact with Proxmox: VMs, containers, storage, backups, etc.

The server connects directly to Proxmox API and provides RESTful endpoints for:
- Node management
- VM operations (start, stop, clone, create, delete, etc.)
- Container (LXC) management
- Storage operations
- Backup/restore operations
- Snapshot management
- Monitoring and performance data

Usage:
    python3 server.py
    # or with custom settings
    python3 server.py --port 5000 --host 0.0.0.0
"""

import argparse
import base64
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import traceback
import uuid
import zipfile
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from proxmoxer import ProxmoxAPI

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Configuration from environment
PROXMOX_HOST = os.getenv("PROXMOX_HOST", "10.0.30.10")
PROXMOX_HOST_FALLBACKS = [
    item.strip()
    for item in os.getenv("PROXMOX_HOST_FALLBACKS", "").split(",")
    if item.strip()
]
PROXMOX_PORT = int(os.getenv("PROXMOX_PORT", "8006"))
PROXMOX_USER = os.getenv("PROXMOX_USER", "root@pam")
PROXMOX_PASSWORD = os.getenv("PROXMOX_PASSWORD", "")
PROXMOX_TOKEN_NAME = os.getenv("PROXMOX_TOKEN_NAME", "")
PROXMOX_TOKEN_VALUE = os.getenv("PROXMOX_TOKEN_VALUE", "")
PROXMOX_VERIFY_SSL = os.getenv("PROXMOX_VERIFY_SSL", "false").lower() in ("1", "true", "yes", "y")
API_PORT = int(os.getenv("API_PORT", "5000"))
DEBUG_MODE = os.getenv("DEBUG_MODE", "0").lower() in ("1", "true", "yes", "y")
# Keep command execution disabled by default for safer containerized deployments.
ENABLE_COMMAND_EXEC = os.getenv("ENABLE_COMMAND_EXEC", "false").lower() in ("1", "true", "yes", "y")
# LXC exec via pct only works when this API runs on a Proxmox host.
ENABLE_PCT_CONTAINER_EXEC = os.getenv("ENABLE_PCT_CONTAINER_EXEC", "false").lower() in ("1", "true", "yes", "y")
# How to execute pct for container commands: local | ssh | auto.
PCT_EXEC_MODE = os.getenv("PCT_EXEC_MODE", "local").strip().lower()
# SSH settings used when PCT_EXEC_MODE=ssh or auto fallback to SSH.
PCT_SSH_HOST = os.getenv("PCT_SSH_HOST", "").strip()
PCT_SSH_PORT = os.getenv("PCT_SSH_PORT", "22").strip()
PCT_SSH_USER = os.getenv("PCT_SSH_USER", "root").strip()
PCT_SSH_KEY_PATH = os.getenv("PCT_SSH_KEY_PATH", "").strip()
PCT_SSH_PASSWORD = os.getenv("PCT_SSH_PASSWORD", "")
PCT_SSH_CONNECT_TIMEOUT = os.getenv("PCT_SSH_CONNECT_TIMEOUT", "10").strip()
PCT_SSH_STRICT_HOST_KEY_CHECKING = os.getenv("PCT_SSH_STRICT_HOST_KEY_CHECKING", "false").lower() in ("1", "true", "yes", "y")
PCT_SSH_KNOWN_HOSTS_FILE = os.getenv("PCT_SSH_KNOWN_HOSTS_FILE", "").strip()
MAX_COMMAND_TIMEOUT = int(os.getenv("MAX_COMMAND_TIMEOUT", "60"))
MAX_COMMAND_OUTPUT = int(os.getenv("MAX_COMMAND_OUTPUT", "20000"))
DEFAULT_CT_OSTEMPLATE = os.getenv("DEFAULT_CT_OSTEMPLATE", "").strip()
DEFAULT_CT_STORAGE = os.getenv("DEFAULT_CT_STORAGE", "pve-data").strip()
DEFAULT_CT_BRIDGE = os.getenv("DEFAULT_CT_BRIDGE", "vmbr1").strip()
DEFAULT_CT_ROOTFS = os.getenv("DEFAULT_CT_ROOTFS", "8").strip()
DEFAULT_CT_MEMORY = int(os.getenv("DEFAULT_CT_MEMORY", "1024"))
DEFAULT_CT_CORES = int(os.getenv("DEFAULT_CT_CORES", "1"))
DEFAULT_DEPLOY_PORT = int(os.getenv("DEFAULT_DEPLOY_PORT", "8000"))
DEFAULT_DEPLOY_WORKDIR = os.getenv("DEFAULT_DEPLOY_WORKDIR", "/opt/app").strip()
DEFAULT_DEPLOY_TIMEOUT = int(os.getenv("DEFAULT_DEPLOY_TIMEOUT", "1800"))
DEFAULT_CT_NODE = os.getenv("DEFAULT_CT_NODE", "").strip()
STARTUP_RETRY_COUNT = int(os.getenv("STARTUP_RETRY_COUNT", "10"))
STARTUP_RETRY_DELAY = int(os.getenv("STARTUP_RETRY_DELAY", "3"))

app = Flask(__name__)

MCP_API_KEY = os.getenv("MCP_API_KEY", "")

@app.before_request
def check_api_key():
    if MCP_API_KEY:
        auth_header = request.headers.get("Authorization")
        if not auth_header or auth_header != f"Bearer {MCP_API_KEY}":
            return jsonify({"error": "Unauthorized"}), 401

# Global Proxmox API connection
proxmox = None
ACTIVE_PROXMOX_HOST = PROXMOX_HOST


def _candidate_proxmox_hosts() -> List[str]:
    """Return ordered unique Proxmox API hosts to try."""
    candidates: List[str] = []
    seen: set[str] = set()
    for host in [PROXMOX_HOST, *PROXMOX_HOST_FALLBACKS]:
        value = str(host or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        candidates.append(value)
    return candidates


def _default_ssh_host() -> str:
    """Resolve SSH host, preferring explicit SSH host over active Proxmox host."""
    # Prefer the currently connected Proxmox API host. In segmented networks,
    # PROXMOX_HOST may be unreachable while a fallback host is reachable.
    return (ACTIVE_PROXMOX_HOST or PCT_SSH_HOST or PROXMOX_HOST or "").strip()


def _with_ssh_auth(cmd: list[str]) -> list[str]:
    """Attach optional password auth wrapper for SSH commands."""
    if not PCT_SSH_PASSWORD:
        return cmd
    if shutil.which("sshpass") is None:
        raise RuntimeError(
            "PCT_SSH_PASSWORD is set but sshpass is not installed. "
            "Install sshpass or use key-based auth."
        )
    return ["sshpass", "-p", PCT_SSH_PASSWORD, *cmd]


def _clamp_timeout(value: Any) -> int:
    """Clamp command timeout to a safe range."""
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = 30
    return max(1, min(timeout, MAX_COMMAND_TIMEOUT))


def _truncate_output(value: str) -> str:
    """Trim very large command output to keep API responses bounded."""
    if len(value) <= MAX_COMMAND_OUTPUT:
        return value
    return value[:MAX_COMMAND_OUTPUT] + "\n... [truncated]"


def _compact_nmap_output(raw_output: str, max_hosts: int = 25, max_service_lines: int = 8) -> str:
    """Return a compact, exact Nmap digest that preserves confirmed host and service lines."""
    text = str(raw_output or "").strip()
    if not text:
        return "(empty nmap output)"

    host_blocks: List[Dict[str, Any]] = []
    current_host: Optional[str] = None
    current_lines: List[str] = []

    for line in text.splitlines():
        host_match = re.match(r"Nmap scan report for\s+(.+)", line)
        if host_match:
            if current_host is not None:
                host_blocks.append({"host": current_host, "lines": current_lines})
            current_host = host_match.group(1).strip()
            current_lines = [line.rstrip()]
            continue

        if current_host is not None:
            current_lines.append(line.rstrip())

    if current_host is not None:
        host_blocks.append({"host": current_host, "lines": current_lines})

    unique_hosts: List[Dict[str, Any]] = []
    seen = set()
    for block in host_blocks:
        host = str(block.get("host") or "").strip()
        if not host:
            continue
        key = host.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_hosts.append(block)

    done_match = re.search(r"Nmap done:\s+(\d+)\s+IP addresses\s+\((\d+)\s+hosts up\)", text)
    scanned_count = done_match.group(1) if done_match else None
    up_count = done_match.group(2) if done_match else str(len(unique_hosts))

    lines = []
    if scanned_count:
        lines.append(f"Nmap summary: scanned={scanned_count}, up={up_count}")
    else:
        lines.append(f"Nmap summary: detected_up_hosts={len(unique_hosts)}")

    lines.append(
        "Authoritative note: only hosts and services listed below are confirmed. "
        "Do not infer additional hosts, ports, or services beyond the visible output."
    )

    if unique_hosts:
        preview = unique_hosts[:max_hosts]
        lines.append("Up hosts:")
        for block in preview:
            host = str(block.get("host") or "").strip()
            lines.append(f"- {host}")

            service_lines = []
            for item in block.get("lines") or []:
                item = str(item).strip()
                if not item:
                    continue
                if (
                    item.startswith("Host is up")
                    or item.startswith("PORT ")
                    or re.match(r"^\d+/(tcp|udp)\s+", item)
                    or item.startswith("MAC Address:")
                    or item.startswith("Service Info:")
                    or item.startswith("Not shown:")
                ):
                    service_lines.append(item)

            for item in service_lines:
                lines.append(f"  - {item}")

        remaining = len(unique_hosts) - len(preview)
        if remaining > 0:
            lines.append(f"- ... and {remaining} more hosts")

    if any(marker in text.lower() for marker in ("... [truncated", "results above may be incomplete", "timed out after", "partial_results")):
        lines.append("Note: The original Nmap output was truncated or incomplete.")

    return "\n".join(lines).strip()


def _safe_int(value: Any, default: int) -> int:
    """Convert value to int, returning a default when conversion fails."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    """Parse common string/int/bool forms into a strict boolean."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, int):
        return value != 0

    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "y", "on"):
        return True
    if normalized in ("0", "false", "no", "n", "off"):
        return False
    return default


def _filter_resource_data(data: Any, resource_type: str = "vm") -> Any:
    """Strip unnecessary fields from Proxmox data to keep LLM context lean."""
    is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
    if is_verbose or not data:
        return data

    if isinstance(data, list):
        return [_filter_resource_data(item, resource_type) for item in data]

    if not isinstance(data, dict):
        return data

    # Curate fields based on resource type
    # We remove secondary metrics like diskread, netin, cpu usage which fluctuate and bloat context.
    keep_keys = {
        "vm": {"vmid", "name", "status", "node", "type", "net0", "ip", "tags", "template", "pool"},
        "node": {"node", "status", "uptime", "maxcpu", "maxmem", "level"},
        "task": {"upid", "node", "type", "status", "starttime", "endtime", "user"},
        "cluster": {"id", "name", "type", "status", "node", "ip"},
    }.get(resource_type, set())

    if not keep_keys:
        return data

    filtered = {k: v for k, v in data.items() if k.lower() in keep_keys}
    
    # Flatten Proxmox nested fields for easier LLM reading if they exist
    if "status" in filtered and isinstance(filtered["status"], dict):
        filtered["status"] = filtered["status"].get("status", "unknown")

    return filtered


def _run_command(
    cmd: list[str],
    timeout: int,
    cwd: str | None = None,
    input_text: str | None = None,
) -> Dict[str, Any]:
    """Execute a local command and return normalized result data."""
    try:
        command_text = " ".join(cmd)
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            input=input_text,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""

        if "nmap" in command_text.lower():
            stdout = _compact_nmap_output(stdout)

        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": _truncate_output(stdout),
            "stderr": _truncate_output(stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        command_text = " ".join(cmd)
        stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
        if "nmap" in command_text.lower():
            stdout = _compact_nmap_output(stdout)

        return {
            "success": False,
            "returncode": None,
            "stdout": _truncate_output(stdout),
            "stderr": _truncate_output(stderr),
            "timed_out": True,
            "error": f"Command timed out after {timeout}s",
        }


def _is_command_exec_enabled() -> bool:
    """Return whether local shell execution endpoints are enabled."""
    return ENABLE_COMMAND_EXEC


def _resolve_deploy_node(explicit_node: str | None = None) -> str:
    """Resolve a target Proxmox node, preferring an explicit value or configured default."""
    node = str(explicit_node or DEFAULT_CT_NODE or '').strip()
    if node:
        return node

    if proxmox is None:
        raise RuntimeError("Proxmox API is not initialized")

    try:
        nodes = proxmox.nodes.get()
    except Exception as exc:
        raise RuntimeError(f"Unable to discover Proxmox nodes: {exc}") from exc

    if not nodes:
        raise RuntimeError("No Proxmox nodes are available for deployment")

    running_nodes = [item for item in nodes if str(item.get("status", '')).lower() == 'online']
    for candidate in running_nodes or nodes:
        name = str(candidate.get("node") or '').strip()
        if name:
            return name

    raise RuntimeError("Could not resolve a Proxmox node for deployment")


def _extract_node_from_upid(upid: str) -> Optional[str]:
    """Extract node name from a Proxmox UPID string."""
    normalized_upid = _normalize_upid(upid)
    if not normalized_upid:
        return None

    # Format: UPID:<node>:<pid>:<pstart>:<starttime>:<type>:<id>:<user>:
    match = re.match(r"^UPID:([^:]+):", normalized_upid)
    if match:
        return match.group(1)

    # Compact fallback sometimes provided by clients: <node>:<pid>:<pstart>:...
    compact_match = re.match(r"^([^:]+):[0-9A-Fa-f]{8}:[0-9A-Fa-f]{8}:[0-9A-Fa-f]{8}:", normalized_upid)
    if compact_match:
        return compact_match.group(1)
    return None


def _normalize_upid(upid: Any) -> str:
    """Normalize UPID values coming from tools, logs, or serialized wrappers."""
    raw = str(upid or "").strip().strip("'\"")
    if not raw:
        return ""

    match = re.search(r"(UPID:[^\s]+)", raw)
    normalized = match.group(1) if match else raw
    return normalized.rstrip(".,;")


def _candidate_upids(upid: Any, node: str | None = None) -> List[str]:
    """Generate possible valid UPID strings for tolerant task lookups."""
    normalized = _normalize_upid(upid)
    if not normalized:
        return []

    values: List[str] = []

    def add(value: str) -> None:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)

    add(normalized)
    add(normalized.rstrip(":"))
    add(normalized.rstrip(":") + ":")

    if not normalized.startswith("UPID:") and node:
        suffix = normalized.lstrip(":")
        add(f"UPID:{node}:{suffix}")
        add(f"UPID:{node}:{suffix.rstrip(':')}")
        add(f"UPID:{node}:{suffix.rstrip(':')}:")

    return values


def _normalize_token_name(user: str, token_name: str) -> str:
    """Return the token id format expected by proxmoxer.

    Proxmox API headers use `user@realm!tokenid=secret`, but proxmoxer expects
    `user` and `token_name` separately. If the env file contains the full
    `user@realm!tokenid` form, strip the user prefix before passing it on.
    """
    if not token_name:
        return token_name

    prefix = f"{user}!"
    if token_name.startswith(prefix):
        return token_name[len(prefix):]
    return token_name


def _build_pct_exec_command(vmid: int, command: str) -> tuple[list[str], str]:
    """Build command line for pct exec (local or SSH)."""
    if PCT_EXEC_MODE not in ("local", "ssh", "auto"):
        raise RuntimeError("Invalid PCT_EXEC_MODE. Use one of: local, ssh, auto")

    has_local_pct = shutil.which("pct") is not None

    if PCT_EXEC_MODE == "local":
        if not has_local_pct:
            raise RuntimeError(
                "pct binary not found on this host. Run the API on a Proxmox node or set "
                "PCT_EXEC_MODE=ssh with SSH settings."
            )
        return ["pct", "exec", str(vmid), "--", "/bin/sh", "-lc", command], "local"

    use_ssh = PCT_EXEC_MODE == "ssh" or (PCT_EXEC_MODE == "auto" and not has_local_pct)
    if not use_ssh:
        return ["pct", "exec", str(vmid), "--", "/bin/sh", "-lc", command], "local"

    remote_cmd = f"pct exec {shlex.quote(str(vmid))} -- /bin/sh -lc {shlex.quote(command)}"
    return _build_ssh_command(remote_cmd), "ssh"


def _build_ssh_command(remote_command: str) -> list[str]:
    """Build a generic SSH command for executing a remote shell command."""
    if shutil.which("ssh") is None:
        raise RuntimeError("ssh client binary not found. Install openssh-client in the API container/host.")

    ssh_host = _default_ssh_host()
    if not ssh_host:
        raise RuntimeError("PCT_SSH_HOST or PROXMOX_HOST is required when using PCT_EXEC_MODE=ssh")

    ssh_target = f"{PCT_SSH_USER}@{ssh_host}" if PCT_SSH_USER else ssh_host
    cmd = [
        "ssh",
        "-p",
        PCT_SSH_PORT or "22",
        "-o",
        f"ConnectTimeout={PCT_SSH_CONNECT_TIMEOUT or '10'}",
    ]

    if PCT_SSH_KEY_PATH:
        cmd.extend(["-i", PCT_SSH_KEY_PATH])

    if PCT_SSH_STRICT_HOST_KEY_CHECKING:
        if PCT_SSH_KNOWN_HOSTS_FILE:
            cmd.extend(["-o", f"UserKnownHostsFile={PCT_SSH_KNOWN_HOSTS_FILE}"])
    else:
        cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])

    cmd.extend([ssh_target, remote_command])
    return _with_ssh_auth(cmd)


def _build_ssh_command_for_host_exec(remote_command: str, ssh_host_override: str | None = None) -> tuple[list[str], str]:
    """Build SSH command for host-exec and return command plus resolved target host."""
    if shutil.which("ssh") is None:
        raise RuntimeError("ssh client binary not found. Install openssh-client in the API container/host.")

    ssh_host = (ssh_host_override or _default_ssh_host() or "").strip()
    if not ssh_host:
        raise RuntimeError("No SSH host configured. Set PCT_SSH_HOST/PROXMOX_HOST or pass ssh_host.")

    ssh_target = f"{PCT_SSH_USER}@{ssh_host}" if PCT_SSH_USER else ssh_host
    cmd = [
        "ssh",
        "-p",
        PCT_SSH_PORT or "22",
        "-o",
        f"ConnectTimeout={PCT_SSH_CONNECT_TIMEOUT or '10'}",
    ]

    if PCT_SSH_KEY_PATH:
        cmd.extend(["-i", PCT_SSH_KEY_PATH])

    if PCT_SSH_STRICT_HOST_KEY_CHECKING:
        if PCT_SSH_KNOWN_HOSTS_FILE:
            cmd.extend(["-o", f"UserKnownHostsFile={PCT_SSH_KNOWN_HOSTS_FILE}"])
    else:
        cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])

    cmd.extend([ssh_target, remote_command])
    return _with_ssh_auth(cmd), ssh_target


def _detect_pct_exec_mode() -> str:
    """Resolve the effective pct execution mode for file and command operations."""
    if PCT_EXEC_MODE not in ("local", "ssh", "auto"):
        raise RuntimeError("Invalid PCT_EXEC_MODE. Use one of: local, ssh, auto")

    has_local_pct = shutil.which("pct") is not None
    if PCT_EXEC_MODE == "local":
        if not has_local_pct:
            raise RuntimeError(
                "pct binary not found on this host. Run the API on a Proxmox node or set "
                "PCT_EXEC_MODE=ssh with SSH settings."
            )
        return "local"
    if PCT_EXEC_MODE == "ssh":
        return "ssh"
    return "local" if has_local_pct else "ssh"


def _validate_container_sync_path(path: str) -> str:
    """Validate a destination path inside the container to avoid shell surprises."""
    cleaned = (path or "").strip()
    if not cleaned:
        raise RuntimeError("path parameter is required")
    if "\x00" in cleaned:
        raise RuntimeError("path cannot contain null bytes")
    if not cleaned.startswith("/"):
        raise RuntimeError("path must be an absolute path inside the container")
    normalized = os.path.normpath(cleaned)
    if not normalized.startswith("/"):
        raise RuntimeError("path resolved outside container root")
    return normalized


def _sync_file_to_container(
    vmid: int,
    path: str,
    content: str,
    timeout: int,
    mode: str = "0644",
    owner: str | None = None,
    group: str | None = None,
    create_dirs: bool = True,
) -> Dict[str, Any]:
    """Write a text file into a container using pct push plus optional chown/chmod."""
    effective_mode = _detect_pct_exec_mode()
    target_path = _validate_container_sync_path(path)
    parent_dir = os.path.dirname(target_path) or "/"

    if create_dirs and parent_dir not in ("", "/"):
        mkdir_cmd, _ = _build_pct_exec_command(vmid, f"mkdir -p {shlex.quote(parent_dir)}")
        mkdir_result = _run_command(mkdir_cmd, timeout=timeout)
        if not mkdir_result.get("success"):
            mkdir_result["step"] = "mkdir"
            mkdir_result["path"] = target_path
            mkdir_result["exec_mode"] = effective_mode
            return mkdir_result

    if effective_mode == "local":
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name

        try:
            push_result = _run_command(
                ["pct", "push", str(vmid), temp_path, target_path],
                timeout=timeout,
            )
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
    else:
        remote_temp = f"/tmp/mcp-sync-{vmid}-{uuid.uuid4().hex}"
        write_cmd = _build_ssh_command(f"cat > {shlex.quote(remote_temp)}")
        write_result = _run_command(write_cmd, timeout=timeout, input_text=content)
        if not write_result.get("success"):
            write_result["step"] = "stage_remote"
            write_result["path"] = target_path
            write_result["exec_mode"] = effective_mode
            return write_result

        try:
            push_cmd = _build_ssh_command(
                f"pct push {shlex.quote(str(vmid))} {shlex.quote(remote_temp)} {shlex.quote(target_path)}"
            )
            push_result = _run_command(push_cmd, timeout=timeout)
        finally:
            _run_command(_build_ssh_command(f"rm -f {shlex.quote(remote_temp)}"), timeout=min(timeout, 15))

    if not push_result.get("success"):
        push_result["step"] = "push"
        push_result["path"] = target_path
        push_result["exec_mode"] = effective_mode
        return push_result

    post_commands = [f"chmod {shlex.quote(mode)} {shlex.quote(target_path)}"]
    if owner and group:
        post_commands.append(f"chown {shlex.quote(owner)}:{shlex.quote(group)} {shlex.quote(target_path)}")
    elif owner:
        post_commands.append(f"chown {shlex.quote(owner)} {shlex.quote(target_path)}")
    elif group:
        post_commands.append(f"chgrp {shlex.quote(group)} {shlex.quote(target_path)}")

    for command in post_commands:
        pct_cmd, _ = _build_pct_exec_command(vmid, command)
        post_result = _run_command(pct_cmd, timeout=timeout)
        if not post_result.get("success"):
            post_result["step"] = "post_process"
            post_result["path"] = target_path
            post_result["exec_mode"] = effective_mode
            return post_result

    return {
        "success": True,
        "path": target_path,
        "bytes_written": len(content.encode("utf-8")),
        "exec_mode": effective_mode,
        "mode": mode,
        "owner": owner,
        "group": group,
        "message": f"File synced to container {vmid}: {target_path}",
    }


def _write_bytes_to_container(
    vmid: int,
    path: str,
    content_bytes: bytes,
    timeout: int,
    mode: str = "0644",
    owner: str | None = None,
    group: str | None = None,
    create_dirs: bool = True,
) -> Dict[str, Any]:
    """Write arbitrary binary content into a container using pct push."""
    effective_mode = _detect_pct_exec_mode()
    target_path = _validate_container_sync_path(path)
    parent_dir = os.path.dirname(target_path) or "/"

    if create_dirs and parent_dir not in ("", "/"):
        mkdir_cmd, _ = _build_pct_exec_command(vmid, f"mkdir -p {shlex.quote(parent_dir)}")
        mkdir_result = _run_command(mkdir_cmd, timeout=timeout)
        if not mkdir_result.get("success"):
            mkdir_result["step"] = "mkdir"
            mkdir_result["path"] = target_path
            mkdir_result["exec_mode"] = effective_mode
            return mkdir_result

    if effective_mode == "local":
        with tempfile.NamedTemporaryFile("wb", delete=False) as temp_file:
            temp_file.write(content_bytes)
            temp_path = temp_file.name

        try:
            push_result = _run_command(
                ["pct", "push", str(vmid), temp_path, target_path],
                timeout=timeout,
            )
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
    else:
        remote_temp = f"/tmp/mcp-bin-{vmid}-{uuid.uuid4().hex}"
        payload = base64.b64encode(content_bytes).decode("ascii")
        write_cmd = _build_ssh_command(f"base64 -d > {shlex.quote(remote_temp)}")
        write_result = _run_command(write_cmd, timeout=timeout, input_text=payload)
        if not write_result.get("success"):
            write_result["step"] = "stage_remote"
            write_result["path"] = target_path
            write_result["exec_mode"] = effective_mode
            return write_result

        try:
            push_cmd = _build_ssh_command(
                f"pct push {shlex.quote(str(vmid))} {shlex.quote(remote_temp)} {shlex.quote(target_path)}"
            )
            push_result = _run_command(push_cmd, timeout=timeout)
        finally:
            _run_command(_build_ssh_command(f"rm -f {shlex.quote(remote_temp)}"), timeout=min(timeout, 15))

    if not push_result.get("success"):
        push_result["step"] = "push"
        push_result["path"] = target_path
        push_result["exec_mode"] = effective_mode
        return push_result

    post_commands = [f"chmod {shlex.quote(mode)} {shlex.quote(target_path)}"]
    if owner and group:
        post_commands.append(f"chown {shlex.quote(owner)}:{shlex.quote(group)} {shlex.quote(target_path)}")
    elif owner:
        post_commands.append(f"chown {shlex.quote(owner)} {shlex.quote(target_path)}")
    elif group:
        post_commands.append(f"chgrp {shlex.quote(group)} {shlex.quote(target_path)}")

    for command in post_commands:
        pct_cmd, _ = _build_pct_exec_command(vmid, command)
        post_result = _run_command(pct_cmd, timeout=timeout)
        if not post_result.get("success"):
            post_result["step"] = "post_process"
            post_result["path"] = target_path
            post_result["exec_mode"] = effective_mode
            return post_result

    return {
        "success": True,
        "path": target_path,
        "bytes_written": len(content_bytes),
        "exec_mode": effective_mode,
        "mode": mode,
        "owner": owner,
        "group": group,
        "message": f"Binary uploaded to container {vmid}: {target_path}",
    }


def _safe_extract_zip(zip_path: str, extract_dir: str) -> None:
    """Safely extract a zip archive without allowing path traversal."""
    base_dir = os.path.realpath(extract_dir)
    os.makedirs(base_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = os.path.realpath(os.path.join(base_dir, member.filename))
            if member_path != base_dir and not member_path.startswith(base_dir + os.sep):
                raise RuntimeError(f"Archive contains unsafe path: {member.filename}")

            if member.is_dir():
                os.makedirs(member_path, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(member_path), exist_ok=True)
            with archive.open(member, "r") as source, open(member_path, "wb") as target:
                shutil.copyfileobj(source, target)


def _build_tarball(source_dir: str, tar_path: str) -> None:
    """Create a gzipped tarball from a directory's contents."""
    with tarfile.open(tar_path, "w:gz") as archive:
        for entry in sorted(os.listdir(source_dir)):
            archive.add(os.path.join(source_dir, entry), arcname=entry)


def _read_json_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sanitize_service_name(value: str, fallback: str = "app") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (value or "").strip()).strip("-_.")
    return cleaned[:48] if cleaned else fallback


def _wait_for_container_running(node: str, vmid: int, timeout_seconds: int = 180) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_state = None
    while time.monotonic() < deadline:
        try:
            status = proxmox.nodes(node).lxc(vmid).status.current.get()
            last_state = status
            if str(status.get("status", "")).lower() == "running":
                return {"success": True, "status": status}
        except Exception as exc:
            last_state = {"error": str(exc)}
        time.sleep(2)

    return {"success": False, "error": f"Container {vmid} did not reach running state in time", "last_state": last_state}


def _extract_ipv4_addresses(ip_output: str) -> List[str]:
    """Extract non-loopback IPv4 addresses from `ip addr` command output."""
    ipv4_matches = re.findall(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/(?:\d+)", ip_output or "")
    result: List[str] = []
    for ip in ipv4_matches:
        if ip.startswith("127."):
            continue
        if ip.startswith("169.254."):
            continue
        if ip not in result:
            result.append(ip)
    return result


def _get_container_ipv4_details(vmid: int, timeout: int = 20) -> Dict[str, Any]:
    """Read IPv4 details from inside an LXC guest via pct exec."""
    try:
        pct_cmd, exec_mode = _build_pct_exec_command(vmid, "ip -o -4 addr show scope global")
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "exec_mode": None,
            "ipv4_addresses": [],
            "ipv4": None,
        }

    cmd_result = _run_command(pct_cmd, timeout=timeout)
    ips = _extract_ipv4_addresses(cmd_result.get("stdout", ""))
    primary_ip = ips[0] if ips else None
    return {
        "success": bool(cmd_result.get("success")) and primary_ip is not None,
        "exec_mode": exec_mode,
        "command_result": cmd_result,
        "ipv4_addresses": ips,
        "ipv4": primary_ip,
    }


def _resolve_ostemplate(node: str, explicit_template: str | None = None) -> Optional[str]:
    candidate = (explicit_template or DEFAULT_CT_OSTEMPLATE or "").strip()
    if candidate:
        return candidate

    try:
        storages = proxmox.nodes(node).storage.get()
    except Exception:
        storages = []

    templates = []
    for storage in storages:
        storage_name = storage.get("storage")
        if not storage_name:
            continue
        try:
            content = proxmox.nodes(node).storage(storage_name).content.get(content="vztmpl")
            for item in content or []:
                volid = str(item.get("volid") or "").strip()
                if volid:
                    templates.append(volid)
        except Exception:
            continue

    if not templates:
        return None

    preferred_terms = ("debian", "ubuntu", "alpine")
    for term in preferred_terms:
        for template in templates:
            if term in template.lower():
                return template

    return templates[0]


def _build_deploy_plan(staging_dir: str, archive_name: str, options: Dict[str, Any]) -> Dict[str, Any]:
    manifest = _read_json_file(os.path.join(staging_dir, "deploy.json"))
    app_name = str(options.get("name") or manifest.get("name") or os.path.splitext(os.path.basename(archive_name))[0] or "deployed-app")
    port = _safe_int(options.get("port") or manifest.get("port") or DEFAULT_DEPLOY_PORT, DEFAULT_DEPLOY_PORT)
    if port < 1:
        port = DEFAULT_DEPLOY_PORT
    workdir = str(options.get("workdir") or manifest.get("workdir") or DEFAULT_DEPLOY_WORKDIR)
    service_name = _sanitize_service_name(str(options.get("service_name") or manifest.get("service_name") or app_name))

    def exists(rel_path: str) -> bool:
        return os.path.exists(os.path.join(staging_dir, rel_path))

    def read_package_json_script() -> Optional[str]:
        pkg_path = os.path.join(staging_dir, "package.json")
        try:
            with open(pkg_path, "r", encoding="utf-8") as handle:
                pkg = json.load(handle)
            scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
            if isinstance(scripts, dict) and isinstance(scripts.get("start"), str) and scripts["start"].strip():
                return "npm start"
            if exists("server.js"):
                return "node server.js"
            if exists("app.js"):
                return "node app.js"
            return "npm run dev"
        except Exception:
            if exists("server.js"):
                return "node server.js"
            if exists("app.js"):
                return "node app.js"
            return "npm start"

    def read_python_start() -> str:
        if exists("app.py"):
            return f"cd {shlex.quote(workdir)} && . .venv/bin/activate && python3 app.py"
        if exists("main.py"):
            return f"cd {shlex.quote(workdir)} && . .venv/bin/activate && python3 main.py"
        if exists("wsgi.py"):
            return f"cd {shlex.quote(workdir)} && . .venv/bin/activate && gunicorn wsgi:app --bind 0.0.0.0:{port}"
        return f"cd {shlex.quote(workdir)} && python3 -m http.server {port}"

    def read_php_start() -> str:
        if exists("artisan"):
            return f"cd {shlex.quote(workdir)} && php artisan serve --host 0.0.0.0 --port {port}"
        if exists("public/index.php"):
            return f"cd {shlex.quote(workdir)} && php -S 0.0.0.0:{port} -t public"
        return f"cd {shlex.quote(workdir)} && php -S 0.0.0.0:{port} -t ."

    install_commands = []
    start_command = str(options.get("start_command") or manifest.get("start_command") or "").strip()

    if not start_command:
        if exists("package.json"):
            install_commands.extend([
                "apt-get update",
                "apt-get install -y nodejs npm",
                f"cd {shlex.quote(workdir)} && npm install",
            ])
            start_command = read_package_json_script()
        elif exists("requirements.txt") or exists("pyproject.toml"):
            install_commands.extend([
                "apt-get update",
                "apt-get install -y python3 python3-pip python3-venv",
                f"cd {shlex.quote(workdir)} && python3 -m venv .venv",
                f"cd {shlex.quote(workdir)} && . .venv/bin/activate && pip install -r requirements.txt",
            ])
            start_command = read_python_start()
        elif exists("composer.json"):
            install_commands.extend([
                "apt-get update",
                "apt-get install -y php-cli php-mbstring php-xml php-curl composer",
                f"cd {shlex.quote(workdir)} && composer install --no-interaction --no-dev --optimize-autoloader",
            ])
            start_command = read_php_start()
        elif exists("index.html") or exists("index.htm"):
            start_command = f"cd {shlex.quote(workdir)} && python3 -m http.server {port}"
        else:
            start_command = f"cd {shlex.quote(workdir)} && python3 -m http.server {port}"

    manifest_install = manifest.get("install_commands")
    if isinstance(manifest_install, list) and manifest_install:
        install_commands = [str(item).strip() for item in manifest_install if str(item).strip()]

    extra_install = options.get("install_commands")
    if isinstance(extra_install, list) and extra_install:
        install_commands = [str(item).strip() for item in extra_install if str(item).strip()]

    environment = {"PORT": str(port)}
    manifest_env = manifest.get("environment")
    if isinstance(manifest_env, dict):
        for key, value in manifest_env.items():
            environment[str(key)] = str(value)

    return {
        "name": app_name,
        "service_name": service_name,
        "port": port,
        "workdir": workdir,
        "start_command": start_command,
        "install_commands": install_commands,
        "environment": environment,
    }


def init_proxmox():
    """Initialize Proxmox API connection."""
    global proxmox, ACTIVE_PROXMOX_HOST

    candidates = _candidate_proxmox_hosts()
    if not candidates:
        logger.error("No Proxmox hosts configured. Set PROXMOX_HOST (and optionally PROXMOX_HOST_FALLBACKS).")
        return False

    auth_mode = "token" if (PROXMOX_TOKEN_NAME and PROXMOX_TOKEN_VALUE) else "password"
    logger.info(f"Using Proxmox API {auth_mode} authentication")

    last_error: Exception | None = None
    for host in candidates:
        try:
            if PROXMOX_TOKEN_NAME and PROXMOX_TOKEN_VALUE:
                token_name = _normalize_token_name(PROXMOX_USER, PROXMOX_TOKEN_NAME)
                client = ProxmoxAPI(
                    host,
                    user=PROXMOX_USER,
                    token_name=token_name,
                    token_value=PROXMOX_TOKEN_VALUE,
                    port=PROXMOX_PORT,
                    verify_ssl=PROXMOX_VERIFY_SSL,
                )
            else:
                client = ProxmoxAPI(
                    host,
                    user=PROXMOX_USER,
                    password=PROXMOX_PASSWORD,
                    port=PROXMOX_PORT,
                    verify_ssl=PROXMOX_VERIFY_SSL,
                )

            version_info = client.version.get()
            proxmox = client
            ACTIVE_PROXMOX_HOST = host
            if host != PROXMOX_HOST:
                logger.warning(f"Primary Proxmox host unavailable, using fallback host {host}")
            logger.info(
                f"Connected to Proxmox at {host}:{PROXMOX_PORT} "
                f"(version {version_info.get('version', 'unknown')})"
            )
            return True
        except Exception as exc:
            last_error = exc
            logger.warning(f"Failed connecting to Proxmox host {host}:{PROXMOX_PORT}: {exc}")

    logger.error(f"Failed to connect to all Proxmox hosts: {candidates}")
    if last_error is not None:
        logger.error(f"Last Proxmox connection error: {last_error}")
    return False


# ============================================================================
# CLUSTER & NODE MANAGEMENT
# ============================================================================

@app.route("/api/cluster/status", methods=["GET"])
def cluster_status():
    """Get cluster status and resource summary"""
    try:
        result = proxmox.cluster.status.get()
        filtered = _filter_resource_data(result, "cluster")
        return jsonify({"success": True, "data": filtered})
    except Exception as e:
        logger.error(f"Error getting cluster status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/cluster/nextid", methods=["GET"])
def get_next_id():
    """Get the next available VM/CT ID from Proxmox cluster allocator."""
    try:
        result = proxmox.cluster.nextid.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting next VMID: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    """List recent cluster tasks."""
    try:
        # Note: proxmox.cluster.tasks.get() does not accept limit/source
        result = proxmox.cluster.tasks.get()
        
        if request.args.get("source"):
            source = request.args.get("source")
            result = [t for t in result if t.get("source") == source or (t.get("upid") and source in t.get("upid"))]
            
        if request.args.get("limit"):
            limit = int(request.args.get("limit", "50"))
            result = result[:limit]

        filtered = _filter_resource_data(result, "task")
        return jsonify({"success": True, "data": filtered})
    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks/<path:upid>/status", methods=["GET"])
def get_task_status(upid):
    """Get status for a task by UPID."""
    try:
        normalized_upid = _normalize_upid(upid)
        if not normalized_upid:
            return jsonify({"success": False, "error": "UPID is required"}), 400

        node = request.args.get("node") or _extract_node_from_upid(normalized_upid)
        if not node:
            return jsonify({"success": False, "error": "Unable to determine node. Pass ?node=<node>"}), 400

        candidate_upids = _candidate_upids(normalized_upid, node=node)
        errors = []
        for candidate in candidate_upids:
            try:
                result = proxmox.nodes(node).tasks(candidate).status.get()
                return jsonify({"success": True, "data": result, "node": node, "upid": candidate})
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        return jsonify({
            "success": False,
            "error": "Unable to parse worker upid",
            "node": node,
            "provided_upid": normalized_upid,
            "tried_upids": candidate_upids,
            "details": errors,
        }), 400
    except Exception as e:
        logger.error(f"Error getting task status for {upid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tasks/<path:upid>/log", methods=["GET"])
def get_task_log(upid):
    """Get task log for a task by UPID."""
    try:
        normalized_upid = _normalize_upid(upid)
        if not normalized_upid:
            return jsonify({"success": False, "error": "UPID is required"}), 400

        node = request.args.get("node") or _extract_node_from_upid(normalized_upid)
        if not node:
            return jsonify({"success": False, "error": "Unable to determine node. Pass ?node=<node>"}), 400

        params = {}
        if request.args.get("start"):
            params["start"] = int(request.args.get("start", "0"))
        if request.args.get("limit"):
            params["limit"] = int(request.args.get("limit", "200"))

        candidate_upids = _candidate_upids(normalized_upid, node=node)
        errors = []
        for candidate in candidate_upids:
            try:
                result = proxmox.nodes(node).tasks(candidate).log.get(**params)
                return jsonify({"success": True, "data": result, "node": node, "upid": candidate})
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        return jsonify({
            "success": False,
            "error": "Unable to parse worker upid",
            "node": node,
            "provided_upid": normalized_upid,
            "tried_upids": candidate_upids,
            "details": errors,
        }), 400
    except Exception as e:
        logger.error(f"Error getting task log for {upid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/nodes", methods=["GET"])
def get_nodes():
    """List all nodes in the cluster with detailed status"""
    try:
        nodes_list = proxmox.nodes.get()
        result = []
        
        for node in nodes_list:
            node_name = node.get("node")
            if not node_name:
                continue
            node_status = node.get("status", "unknown")
            try:
                status = proxmox.nodes(node_name).status.get()
                result.append({
                    "node": node_name,
                    "status": node_status,
                    "uptime": status.get("uptime", 0),
                    "maxcpu": status.get("cpuinfo", {}).get("cpus", "N/A"),
                    "memory": {
                        "used": status.get("memory", {}).get("used", 0),
                        "total": status.get("memory", {}).get("total", 0)
                    }
                })
            except Exception:
                # Fallback to basic info
                result.append({
                    "node": node_name,
                    "status": node_status,
                    "uptime": 0,
                    "maxcpu": "N/A",
                    "memory": {
                        "used": node.get("maxmem", 0) - node.get("mem", 0),
                        "total": node.get("maxmem", 0)
                    }
                })
        
        return jsonify({"success": True, "data": _filter_resource_data(result, "node")})
    except Exception as e:
        logger.error(f"Error listing nodes: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/nodes/<node>", methods=["GET"])
def get_node_status(node):
    """Get detailed status of a specific node"""
    try:
        result = proxmox.nodes(node).status.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting node status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# VM MANAGEMENT
# ============================================================================

@app.route("/api/vms", methods=["GET"])
def list_vms():
    """List all VMs across all nodes"""
    try:
        node_param = request.args.get("node")
        result = []
        
        if node_param:
            # List VMs on specific node
            vms = proxmox.nodes(node_param).qemu.get()
            for vm in vms:
                vmid = vm.get("vmid")
                if vmid is None:
                    continue
                try:
                    config = proxmox.nodes(node_param).qemu(vmid).config.get()
                    result.append({
                        "vmid": vmid,
                        "name": vm.get("name", f"vm-{vmid}"),
                        "status": vm.get("status", "unknown"),
                        "node": node_param,
                        "cpus": config.get("cores", "N/A"),
                        "memory": {
                            "used": vm.get("mem", 0),
                            "total": vm.get("maxmem", 0)
                        }
                    })
                except Exception:
                    result.append({
                        "vmid": vmid,
                        "name": vm.get("name", f"vm-{vmid}"),
                        "status": vm.get("status", "unknown"),
                        "node": node_param,
                        "cpus": "N/A",
                        "memory": {
                            "used": vm.get("mem", 0),
                            "total": vm.get("maxmem", 0)
                        }
                    })
        else:
            # List all VMs on all nodes
            for node in proxmox.nodes.get():
                node_name = node.get("node")
                if not node_name:
                    continue
                vms = proxmox.nodes(node_name).qemu.get()
                for vm in vms:
                    vmid = vm.get("vmid")
                    if vmid is None:
                        continue
                    try:
                        config = proxmox.nodes(node_name).qemu(vmid).config.get()
                        result.append({
                            "vmid": vmid,
                            "name": vm.get("name", f"vm-{vmid}"),
                            "status": vm.get("status", "unknown"),
                            "node": node_name,
                            "cpus": config.get("cores", "N/A"),
                            "memory": {
                                "used": vm.get("mem", 0),
                                "total": vm.get("maxmem", 0)
                            }
                        })
                    except Exception:
                        result.append({
                            "vmid": vmid,
                            "name": vm.get("name", f"vm-{vmid}"),
                            "status": vm.get("status", "unknown"),
                            "node": node_name,
                            "cpus": "N/A",
                            "memory": {
                                "used": vm.get("mem", 0),
                                "total": vm.get("maxmem", 0)
                            }
                        })
        
        return jsonify({"success": True, "data": _filter_resource_data(result, "vm")})
    except Exception as e:
        logger.error(f"Error listing VMs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>", methods=["GET"])
def get_vm_status(node, vmid):
    """Get detailed status of a specific VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).status.current.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting VM status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/config", methods=["GET"])
def get_vm_config(node, vmid):
    """Get VM configuration"""
    try:
        result = proxmox.nodes(node).qemu(vmid).config.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting VM config: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/start", methods=["POST"])
def start_vm(node, vmid):
    """Start a VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).status.start.post()
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} start initiated"})
    except Exception as e:
        logger.error(f"Error starting VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/stop", methods=["POST"])
def stop_vm(node, vmid):
    """Stop a VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).status.stop.post()
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} stop initiated"})
    except Exception as e:
        logger.error(f"Error stopping VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/shutdown", methods=["POST"])
def shutdown_vm(node, vmid):
    """Gracefully shutdown a VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).status.shutdown.post()
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} shutdown initiated"})
    except Exception as e:
        logger.error(f"Error shutting down VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/reboot", methods=["POST"])
def reboot_vm(node, vmid):
    """Reboot a VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).status.reboot.post()
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} reboot initiated"})
    except Exception as e:
        logger.error(f"Error rebooting VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/reset", methods=["POST"])
def reset_vm(node, vmid):
    """Hard reset a VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).status.reset.post()
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} reset initiated"})
    except Exception as e:
        logger.error(f"Error resetting VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/suspend", methods=["POST"])
def suspend_vm(node, vmid):
    """Suspend a VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).status.suspend.post()
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} suspend initiated"})
    except Exception as e:
        logger.error(f"Error suspending VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/resume", methods=["POST"])
def resume_vm(node, vmid):
    """Resume a suspended VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).status.resume.post()
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} resume initiated"})
    except Exception as e:
        logger.error(f"Error resuming VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/delete", methods=["POST"])
def delete_vm(node, vmid):
    """Delete a VM (destructive)"""
    try:
        result = proxmox.nodes(node).qemu(vmid).delete()
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} deleted"})
    except Exception as e:
        logger.error(f"Error deleting VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/clone", methods=["POST"])
def clone_vm(node, vmid):
    """Clone a VM"""
    try:
        params = request.json or {}
        newid = params.get("newid")
        name = params.get("name", f"clone-{vmid}")
        full = params.get("full", False)
        
        if not newid:
            return jsonify({"success": False, "error": "newid parameter is required"}), 400
        
        clone_params = {
            "newid": newid,
            "name": name,
            "full": full
        }
        result = proxmox.nodes(node).qemu(vmid).clone.post(**clone_params)
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} cloned to {newid}"})
    except Exception as e:
        logger.error(f"Error cloning VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/resize-disk", methods=["POST"])
def resize_vm_disk(node, vmid):
    """Resize a VM disk"""
    try:
        params = request.json or {}
        disk = params.get("disk", "scsi0")
        size = params.get("size")
        
        if not size:
            return jsonify({"success": False, "error": "size parameter is required"}), 400
        
        result = proxmox.nodes(node).qemu(vmid).resize.put(disk=disk, size=size)
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} disk {disk} resized"})
    except Exception as e:
        logger.error(f"Error resizing VM disk: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# CONTAINER (LXC) MANAGEMENT
# ============================================================================

@app.route("/api/containers", methods=["GET"])
def list_containers():
    """List all LXC containers"""
    try:
        node_param = request.args.get("node")
        result = []
        
        if node_param:
            containers = proxmox.nodes(node_param).lxc.get()
            for container in containers:
                result.append({
                    "vmid": container["vmid"],
                    "hostname": container.get("hostname", "N/A"),
                    "status": container["status"],
                    "node": node_param,
                    "memory": {
                        "used": container.get("mem", 0),
                        "total": container.get("maxmem", 0)
                    }
                })
        else:
            for node in proxmox.nodes.get():
                node_name = node["node"]
                containers = proxmox.nodes(node_name).lxc.get()
                for container in containers:
                    result.append({
                        "vmid": container["vmid"],
                        "hostname": container.get("hostname", "N/A"),
                        "status": container["status"],
                        "node": node_name,
                        "memory": {
                            "used": container.get("mem", 0),
                            "total": container.get("maxmem", 0)
                        }
                    })
        
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing containers: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>", methods=["GET"])
def get_container_status(node, vmid):
    """Get detailed status of a container"""
    try:
        result = proxmox.nodes(node).lxc(vmid).status.current.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting container status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/details", methods=["GET"])
def get_container_details(node, vmid):
    """Get container status/config and best-effort guest network details."""
    try:
        status = proxmox.nodes(node).lxc(vmid).status.current.get()
        config = proxmox.nodes(node).lxc(vmid).config.get()

        details: Dict[str, Any] = {
            "status": status,
            "config": config,
        }

        if _is_command_exec_enabled() and ENABLE_PCT_CONTAINER_EXEC:
            timeout = _clamp_timeout(request.args.get("ip_timeout", 20))
            guest_network = _get_container_ipv4_details(vmid, timeout=timeout)
            details["guest_network"] = guest_network
            details["guest_ipv4"] = guest_network.get("ipv4")
            details["guest_ipv4_addresses"] = guest_network.get("ipv4_addresses", [])
        else:
            details["guest_network"] = {
                "success": False,
                "error": "Guest IP discovery requires ENABLE_COMMAND_EXEC=true and ENABLE_PCT_CONTAINER_EXEC=true",
                "ipv4_addresses": [],
                "ipv4": None,
            }

        return jsonify({"success": True, "data": details, "node": node, "vmid": vmid})
    except Exception as e:
        logger.error(f"Error getting container details for {node}/{vmid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/start", methods=["POST"])
def start_container(node, vmid):
    """Start a container"""
    try:
        result = proxmox.nodes(node).lxc(vmid).status.start.post()
        return jsonify({"success": True, "data": result, "message": f"Container {vmid} started"})
    except Exception as e:
        logger.error(f"Error starting container: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/stop", methods=["POST"])
def stop_container(node, vmid):
    """Stop a container"""
    try:
        result = proxmox.nodes(node).lxc(vmid).status.stop.post()
        return jsonify({"success": True, "data": result, "message": f"Container {vmid} stopped"})
    except Exception as e:
        logger.error(f"Error stopping container: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/shutdown", methods=["POST"])
def shutdown_container(node, vmid):
    """Gracefully shutdown a container"""
    try:
        result = proxmox.nodes(node).lxc(vmid).status.shutdown.post()
        return jsonify({"success": True, "data": result, "message": f"Container {vmid} shutdown initiated"})
    except Exception as e:
        logger.error(f"Error shutting down container: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/reboot", methods=["POST"])
def reboot_container(node, vmid):
    """Reboot a container"""
    try:
        result = proxmox.nodes(node).lxc(vmid).status.reboot.post()
        return jsonify({"success": True, "data": result, "message": f"Container {vmid} rebooted"})
    except Exception as e:
        logger.error(f"Error rebooting container: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/delete", methods=["POST"])
def delete_container(node, vmid):
    """Delete a container (destructive)"""
    try:
        result = proxmox.nodes(node).lxc(vmid).delete()
        return jsonify({"success": True, "data": result, "message": f"Container {vmid} deleted"})
    except Exception as e:
        logger.error(f"Error deleting container: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/clone", methods=["POST"])
def clone_container(node, vmid):
    """Clone a container"""
    try:
        params = request.json or {}
        newid = params.get("newid")
        hostname = params.get("hostname", f"clone-{vmid}")
        
        if not newid:
            return jsonify({"success": False, "error": "newid parameter is required"}), 400
        
        clone_params = {
            "newid": newid,
            "hostname": hostname
        }
        result = proxmox.nodes(node).lxc(vmid).clone.post(**clone_params)
        return jsonify({"success": True, "data": result, "message": f"Container {vmid} cloned to {newid}"})
    except Exception as e:
        logger.error(f"Error cloning container: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# STORAGE MANAGEMENT
# ============================================================================

@app.route("/api/storage", methods=["GET"])
def list_storage():
    """List all storage"""
    try:
        node_param = request.args.get("node")
        
        if node_param:
            result = proxmox.nodes(node_param).storage.get()
        else:
            result = proxmox.storage.get()
        
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing storage: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/storage/<node>/<storage>", methods=["GET"])
def get_storage_status(node, storage):
    """Get storage status"""
    try:
        result = proxmox.nodes(node).storage(storage).status.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting storage status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/storage/<node>/<storage>/content", methods=["GET"])
def list_storage_content(node, storage):
    """List storage content (ISOs, templates, backups)"""
    try:
        content_type = request.args.get("content", "")
        params = {}
        if content_type:
            params["content"] = content_type
        
        result = proxmox.nodes(node).storage(storage).content.get(**params)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing storage content: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# SNAPSHOT MANAGEMENT
# ============================================================================

@app.route("/api/vms/<node>/<int:vmid>/snapshots", methods=["GET"])
def list_vm_snapshots(node, vmid):
    """List all snapshots for a VM"""
    try:
        result = proxmox.nodes(node).qemu(vmid).snapshot.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing snapshots: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/snapshots", methods=["POST"])
def create_vm_snapshot(node, vmid):
    """Create a snapshot of a VM"""
    try:
        params = request.json or {}
        snapname = params.get("snapname")
        description = params.get("description", "")
        
        if not snapname:
            return jsonify({"success": False, "error": "snapname parameter is required"}), 400
        
        snap_params = {"snapname": snapname}
        if description:
            snap_params["description"] = description
        
        result = proxmox.nodes(node).qemu(vmid).snapshot.post(**snap_params)
        return jsonify({"success": True, "data": result, "message": f"Snapshot {snapname} created"})
    except Exception as e:
        logger.error(f"Error creating snapshot: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/snapshots/<snapname>", methods=["DELETE"])
def delete_vm_snapshot(node, vmid, snapname):
    """Delete a VM snapshot"""
    try:
        result = proxmox.nodes(node).qemu(vmid).snapshot(snapname).delete()
        return jsonify({"success": True, "data": result, "message": f"Snapshot {snapname} deleted"})
    except Exception as e:
        logger.error(f"Error deleting snapshot: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/snapshots/<snapname>/rollback", methods=["POST"])
def rollback_vm_snapshot(node, vmid, snapname):
    """Rollback VM to a snapshot"""
    try:
        result = proxmox.nodes(node).qemu(vmid).snapshot(snapname).rollback.post()
        return jsonify({"success": True, "data": result, "message": f"Rolled back to snapshot {snapname}"})
    except Exception as e:
        logger.error(f"Error rolling back snapshot: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# BACKUP MANAGEMENT
# ============================================================================

@app.route("/api/backups", methods=["GET"])
def list_backups():
    """List all backups"""
    try:
        node_param = request.args.get("node")
        
        if node_param:
            storages = proxmox.nodes(node_param).storage.get()
            backups = []
            for storage in storages:
                if "backup" in storage.get("content", ""):
                    try:
                        content = proxmox.nodes(node_param).storage(storage["storage"]).content.get(content="backup")
                        backups.extend(content)
                    except:
                        pass
            result = backups
        else:
            all_backups = []
            for node in proxmox.nodes.get():
                storages = proxmox.nodes(node["node"]).storage.get()
                for storage in storages:
                    if "backup" in storage.get("content", ""):
                        try:
                            content = proxmox.nodes(node["node"]).storage(storage["storage"]).content.get(content="backup")
                            all_backups.extend(content)
                        except:
                            pass
            result = all_backups
        
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing backups: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/backup", methods=["POST"])
def backup_vm(node, vmid):
    """Create a backup of a VM"""
    try:
        params = request.json or {}
        storage = params.get("storage", "local")
        mode = params.get("mode", "snapshot")
        compress = params.get("compress", "zstd")
        
        backup_params = {
            "vmid": vmid,
            "storage": storage,
            "mode": mode,
            "compress": compress,
        }
        result = proxmox.nodes(node).vzdump.post(**backup_params)
        return jsonify({"success": True, "data": result, "message": f"Backup of VM {vmid} initiated"})
    except Exception as e:
        logger.error(f"Error backing up VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# MONITORING & PERFORMANCE
# ============================================================================

@app.route("/api/vms/<node>/<int:vmid>/monitoring", methods=["GET"])
def get_vm_monitoring(node, vmid):
    """Get VM performance/monitoring data (RRD)"""
    try:
        timeframe = request.args.get("timeframe", "hour")
        result = proxmox.nodes(node).qemu(vmid).rrddata.get(timeframe=timeframe)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting VM monitoring data: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/nodes/<node>/monitoring", methods=["GET"])
def get_node_monitoring(node):
    """Get node performance/monitoring data (RRD)"""
    try:
        timeframe = request.args.get("timeframe", "hour")
        result = proxmox.nodes(node).rrddata.get(timeframe=timeframe)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting node monitoring data: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# NETWORK & TEMPLATES
# ============================================================================

@app.route("/api/nodes/<node>/networks", methods=["GET"])
def list_networks(node):
    """List network interfaces on a node"""
    try:
        result = proxmox.nodes(node).network.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing networks: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/templates", methods=["GET"])
def list_templates():
    """List available VM templates"""
    try:
        node_param = request.args.get("node")
        
        if node_param:
            storages = proxmox.nodes(node_param).storage.get()
            templates = []
            for storage in storages:
                try:
                    storage_name = storage.get("storage")
                    if not storage_name:
                        continue
                    content = proxmox.nodes(node_param).storage(storage_name).content.get(content="vztmpl")
                    templates.extend(content)
                except:
                    pass
            result = templates
        else:
            all_templates = []
            for node in proxmox.nodes.get():
                node_name = node.get("node")
                if not node_name:
                    continue
                storages = proxmox.nodes(node_name).storage.get()
                for storage in storages:
                    try:
                        storage_name = storage.get("storage")
                        if not storage_name:
                            continue
                        content = proxmox.nodes(node_name).storage(storage_name).content.get(content="vztmpl")
                        all_templates.extend(content)
                    except:
                        pass
            result = all_templates
        
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing templates: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/isos", methods=["GET"])
def list_isos():
    """List available ISO images"""
    try:
        node_param = request.args.get("node")
        
        if node_param:
            storages = proxmox.nodes(node_param).storage.get()
            isos = []
            for storage in storages:
                try:
                    storage_name = storage.get("storage")
                    if not storage_name:
                        continue
                    content = proxmox.nodes(node_param).storage(storage_name).content.get(content="iso")
                    isos.extend(content)
                except:
                    pass
            result = isos
        else:
            all_isos = []
            for node in proxmox.nodes.get():
                node_name = node.get("node")
                if not node_name:
                    continue
                storages = proxmox.nodes(node_name).storage.get()
                for storage in storages:
                    try:
                        storage_name = storage.get("storage")
                        if not storage_name:
                            continue
                        content = proxmox.nodes(node_name).storage(storage_name).content.get(content="iso")
                        all_isos.extend(content)
                    except:
                        pass
            result = all_isos
        
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing ISOs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# VM CREATION & ADVANCED OPERATIONS
# ============================================================================

@app.route("/api/vms/create", methods=["POST"])
def create_vm():
    """Create a new VM with specified parameters"""
    try:
        params = request.json or {}
        node = params.get("node")
        vmid = params.get("vmid")
        name = params.get("name")
        
        if not all([node, vmid, name]):
            return jsonify({"success": False, "error": "node, vmid, and name are required"}), 400
        
        # Build VM creation parameters
        vm_params = {
            "vmid": vmid,
            "name": name,
            "memory": params.get("memory", 2048),
            "cores": params.get("cores", 2),
            "sockets": params.get("sockets", 1),
            "cpu": params.get("cpu", "host"),
            "ostype": params.get("ostype", "l26"),  # linux 2.6+
        }
        
        # Optional parameters
        if "sata0" in params:
            vm_params["sata0"] = params["sata0"]
        if "scsi0" in params:
            vm_params["scsi0"] = params["scsi0"]
        if "net0" in params:
            vm_params["net0"] = params["net0"]
        if "ide2" in params:
            vm_params["ide2"] = params["ide2"]  # For CD/ISO
        if "boot" in params:
            vm_params["boot"] = params["boot"]
        if "description" in params:
            vm_params["description"] = params["description"]
        if "scsihw" in params:
            vm_params["scsihw"] = params["scsihw"]
        if "virtio0" in params:
            vm_params["virtio0"] = params["virtio0"]
        
        result = proxmox.nodes(node).qemu.create(**vm_params)
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} ({name}) creation initiated on {node}"})
    except Exception as e:
        logger.error(f"Error creating VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# CONTAINER CREATION & ADVANCED OPERATIONS
# ============================================================================

@app.route("/api/containers/create", methods=["POST"])
def create_container():
    """Create a new LXC container with specified parameters"""
    try:
        params = request.json or {}
        node = params.get("node")
        vmid = params.get("vmid")
        hostname = params.get("hostname")
        ostype = params.get("ostype")  # e.g., "ubuntu", "debian", "alpine"
        
        if not all([node, vmid, hostname, ostype]):
            return jsonify({"success": False, "error": "node, vmid, hostname, and ostype are required"}), 400
        
        # Build container creation parameters
        container_params = {
            "vmid": vmid,
            "hostname": hostname,
            "ostype": ostype,
            "memory": params.get("memory", 512),
            "cores": params.get("cores", 1),
            "rootfs": params.get("rootfs", "local-lvm:4"),  # 4GB default
            "features": params.get("features", "nesting=1"),
            "tty": params.get("tty", 2),
            "console": params.get("console", 1),
        }
        
        # Optional parameters
        if "ostemplate" in params:
            container_params["ostemplate"] = params["ostemplate"]
        if "storage" in params:
            container_params["storage"] = params["storage"]
        if "swap" in params:
            container_params["swap"] = params["swap"]
        if "net0" in params:
            container_params["net0"] = params["net0"]
        if "description" in params:
            container_params["description"] = params["description"]
        if "searchdomain" in params:
            container_params["searchdomain"] = params["searchdomain"]
        if "nameserver" in params:
            container_params["nameserver"] = params["nameserver"]
        if "password" in params:
            container_params["password"] = params["password"]
        if "start" in params:
            container_params["start"] = 1 if params["start"] else 0
        
        result = proxmox.nodes(node).lxc.create(**container_params)

        # Post-creation: apply keyctl=1 via SSH (requires root@pam privileges, not just API token).
        # Best-effort: log warning on failure but don't fail the whole creation.
        keyctl_warning = None
        if not params.get("skip_keyctl"):
            try:
                ssh_host = _default_ssh_host()
                if ssh_host and PCT_EXEC_MODE in ("ssh", "auto"):
                    ssh_target = f"{PCT_SSH_USER}@{ssh_host}" if PCT_SSH_USER else ssh_host
                    pct_set_cmd = ["ssh", "-p", PCT_SSH_PORT or "22",
                                   "-o", f"ConnectTimeout={PCT_SSH_CONNECT_TIMEOUT or '10'}"]
                    if PCT_SSH_KEY_PATH:
                        pct_set_cmd.extend(["-i", PCT_SSH_KEY_PATH])
                    if PCT_SSH_STRICT_HOST_KEY_CHECKING and PCT_SSH_KNOWN_HOSTS_FILE:
                        pct_set_cmd.extend(["-o", f"UserKnownHostsFile={PCT_SSH_KNOWN_HOSTS_FILE}"])
                    elif not PCT_SSH_STRICT_HOST_KEY_CHECKING:
                        pct_set_cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
                    pct_set_cmd.extend([ssh_target,
                                        f"pct set {vmid} -features nesting=1,keyctl=1"])
                    pct_set_cmd = _with_ssh_auth(pct_set_cmd)
                    subprocess.run(pct_set_cmd, capture_output=True, timeout=30, check=True)
                    logger.info(f"Applied keyctl=1 to container {vmid} via SSH")
            except Exception as keyctl_err:
                keyctl_warning = f"Container created but keyctl=1 could not be applied via SSH: {keyctl_err}"
                logger.warning(keyctl_warning)

        msg = f"Container {vmid} ({hostname}) creation initiated on {node}"
        if keyctl_warning:
            msg += f". Warning: {keyctl_warning}"
        return jsonify({"success": True, "data": result, "message": msg})
    except Exception as e:
        logger.error(f"Error creating container: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# VM MIGRATION & ADVANCED OPERATIONS
# ============================================================================

@app.route("/api/vms/<node>/<int:vmid>/migrate", methods=["POST"])
def migrate_vm(node, vmid):
    """Migrate a VM to another node"""
    try:
        params = request.json or {}
        target = params.get("target")
        online = params.get("online", True)  # Live migration
        
        if not target:
            return jsonify({"success": False, "error": "target node is required"}), 400
        
        migrate_params = {
            "target": target,
            "online": 1 if online else 0,
            "force": params.get("force", 0),  # Force migration even if not shared storage
        }
        
        result = proxmox.nodes(node).qemu(vmid).migrate.post(**migrate_params)
        return jsonify({"success": True, "data": result, "message": f"VM {vmid} migration to {target} initiated"})
    except Exception as e:
        logger.error(f"Error migrating VM: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# HIGH AVAILABILITY (HA) MANAGEMENT
# ============================================================================

@app.route("/api/vms/<node>/<int:vmid>/ha", methods=["GET", "POST", "PUT"])
def manage_vm_ha(node, vmid):
    """Get or set HA configuration for a VM"""
    try:
        if request.method == "GET":
            # Get current HA status
            result = proxmox.cluster.ha.resources.get()
            vm_ha = [r for r in result if r.get("sid") == f"vm:{vmid}"]
            if vm_ha:
                return jsonify({"success": True, "data": vm_ha[0]})
            else:
                return jsonify({"success": True, "data": None, "message": "VM has no HA config"})
        
        elif request.method == "POST" or request.method == "PUT":
            # Enable/configure HA for VM
            params = request.json or {}
            state = params.get("state", "enabled")  # enabled, disabled, stopped
            group = params.get("group")  # optional HA group
            
            ha_params = {
                "sid": f"vm:{vmid}",
                "state": state,
            }
            if group:
                ha_params["group"] = group
            
            # Update HA config
            result = proxmox.cluster.ha.resources(f"vm:{vmid}").put(**ha_params)
            return jsonify({"success": True, "data": result, "message": f"HA configuration updated for VM {vmid}"})
    
    except Exception as e:
        logger.error(f"Error managing VM HA: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/ha/status", methods=["GET"])
def get_ha_status():
    """Get overall HA cluster status"""
    try:
        result = proxmox.cluster.ha.status.current.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error getting HA status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/ha/resources", methods=["GET"])
def list_ha_resources():
    """List all HA-protected resources"""
    try:
        result = proxmox.cluster.ha.resources.get()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error listing HA resources: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# GUEST AGENT & COMMAND EXECUTION
# ============================================================================

@app.route("/api/vms/<node>/<int:vmid>/agent/ping", methods=["POST"])
def vm_agent_ping(node, vmid):
    """Ping the QEMU guest agent inside a VM."""
    try:
        result = proxmox.nodes(node).qemu(vmid).agent("ping").post()
        return jsonify({"success": True, "data": result, "node": node, "vmid": vmid})
    except Exception as e:
        logger.error(f"Error pinging VM guest agent {node}/{vmid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/agent/network", methods=["GET"])
def vm_agent_network(node, vmid):
    """Get network interface details from QEMU guest agent."""
    try:
        result = proxmox.nodes(node).qemu(vmid).agent("network-get-interfaces").get()
        return jsonify({"success": True, "data": result, "node": node, "vmid": vmid})
    except Exception as e:
        logger.error(f"Error reading VM guest network {node}/{vmid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/agent/exec", methods=["POST"])
def exec_vm_agent_command(node, vmid):
    """Execute a command in VM guest via QEMU agent and return the job PID."""
    try:
        params = request.json or {}
        command = params.get("command", "").strip()
        if not command:
            return jsonify({"success": False, "error": "command parameter is required"}), 400

        command_args = params.get("args", [])
        if not isinstance(command_args, list):
            return jsonify({"success": False, "error": "args must be a list of strings"}), 400

        payload = {
            "command": command,
            "capture-output": params.get("capture_output", True),
        }
        if command_args:
            payload["arg"] = command_args
        if params.get("input_data") is not None:
            payload["input-data"] = str(params.get("input_data"))

        result = proxmox.nodes(node).qemu(vmid).agent("exec").post(**payload)
        return jsonify({
            "success": True,
            "data": result,
            "message": "Command submitted via QEMU guest agent",
            "node": node,
            "vmid": vmid,
        })
    except Exception as e:
        logger.error(f"Error executing VM guest command {node}/{vmid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vms/<node>/<int:vmid>/agent/exec-status", methods=["POST"])
def get_vm_agent_exec_status(node, vmid):
    """Get status/output for a previously submitted QEMU guest agent exec command."""
    try:
        params = request.json or {}
        pid = params.get("pid")
        if pid is None:
            return jsonify({"success": False, "error": "pid parameter is required"}), 400

        result = proxmox.nodes(node).qemu(vmid).agent("exec-status").get(pid=int(pid))
        return jsonify({"success": True, "data": result, "node": node, "vmid": vmid, "pid": int(pid)})
    except Exception as e:
        logger.error(f"Error getting VM guest command status {node}/{vmid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/server/sync-file", methods=["POST"])
def sync_server_file():
    """Write a file to the server's filesystem.

    Allows pushing updated source files (e.g. server.py, mcp_http_server.py)
    from a remote workspace directly to this host without needing git.
    Paths are resolved relative to the server's working directory and must
    stay within it — directory traversal attempts are rejected.
    Gated behind ENABLE_COMMAND_EXEC for safety.
    """
    if not _is_command_exec_enabled():
        return jsonify({"success": False, "error": "File sync is disabled (ENABLE_COMMAND_EXEC=false)"}), 403

    try:
        params = request.json or {}
        rel_path = params.get("path", "").strip()
        content = params.get("content")

        if not rel_path:
            return jsonify({"success": False, "error": "path is required"}), 400
        if content is None:
            return jsonify({"success": False, "error": "content is required"}), 400

        # Resolve against the server's working directory and reject traversal.
        base_dir = os.path.realpath(os.getcwd())
        target = os.path.realpath(os.path.join(base_dir, rel_path))
        if not target.startswith(base_dir + os.sep) and target != base_dir:
            return jsonify({"success": False, "error": "Path must be inside the server working directory"}), 400

        # Atomic write via a temporary file.
        tmp_path = target + ".synctmp"
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            os.replace(tmp_path, target)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        logger.info(f"File synced: {target} ({len(content)} chars)")
        return jsonify({"success": True, "message": f"File written: {rel_path} ({len(content)} chars)",
                        "path": target})
    except Exception as e:
        logger.error(f"Error syncing file: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/server/restart", methods=["POST"])
def restart_server():
    """Restart the Flask API process in-place (os.execv).

    Sends the HTTP response first, then replaces the process image so the
    new code (e.g. after a sync-file) is loaded.  Gated behind
    ENABLE_COMMAND_EXEC for safety.
    """
    if not _is_command_exec_enabled():
        return jsonify({"success": False, "error": "Server restart is disabled (ENABLE_COMMAND_EXEC=false)"}), 403

    def _do_restart():
        import time
        time.sleep(0.5)  # Give Flask time to send the response
        logger.info("Restarting server process via subprocess + exit…")
        # Spawn a fresh process (inherits cwd, env, stdout/stderr) then exit this one.
        subprocess.Popen(
            [sys.executable] + sys.argv,
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )
        os._exit(0)

    import threading
    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"success": True, "message": "Server is restarting…"})


@app.route("/api/host/exec", methods=["POST"])
def exec_host_command():
    """Execute a shell command locally or over SSH for containerized deployments."""
    if not _is_command_exec_enabled():
        return jsonify({"success": False, "error": "Host command execution is disabled"}), 403

    try:
        params = request.json or {}
        command = params.get("command", "").strip()
        timeout = _clamp_timeout(params.get("timeout", 30))
        exec_mode = str(params.get("exec_mode", "local")).strip().lower()
        ssh_host = params.get("ssh_host")

        if not command:
            return jsonify({"success": False, "error": "command parameter is required"}), 400

        # Strikt whitelisting van beheerscommando's
        ALLOWED_BASE_CMDS = {"ls", "df", "free", "uptime", "pvs", "vgs", "lvs", "cat", "echo", "pwd", "whoami"}
        base_cmd = command.split()[0]
        if base_cmd not in ALLOWED_BASE_CMDS:
            return jsonify({"success": False, "error": f"Command not in whitelist: {base_cmd}"}), 403

        if exec_mode not in ("local", "ssh"):
            return jsonify({"success": False, "error": "exec_mode must be 'local' or 'ssh'"}), 400

        if ssh_host is not None and not isinstance(ssh_host, str):
            return jsonify({"success": False, "error": "ssh_host must be a string when provided"}), 400

        cwd = params.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            return jsonify({"success": False, "error": "cwd must be a string path"}), 400

        if exec_mode == "ssh":
            remote_script = command
            if cwd:
                remote_script = f"cd {shlex.quote(cwd)} && {command}"
            ssh_cmd, ssh_target = _build_ssh_command_for_host_exec(
                f"/bin/sh -lc {shlex.quote(remote_script)}",
                ssh_host_override=ssh_host,
            )
            result = _run_command(ssh_cmd, timeout=timeout)
            result["exec_mode"] = "ssh"
            result["ssh_target"] = ssh_target
        else:
            result = _run_command(["/bin/sh", "-lc", command], timeout=timeout, cwd=cwd)
            result["exec_mode"] = "local"

        status_code = 408 if result.get("timed_out") else 200
        return jsonify(result), status_code
    except Exception as e:
        logger.error(f"Error executing host command: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/exec", methods=["POST"])
def exec_container_command(node, vmid):
    """Execute a shell command inside an LXC container via local pct (host-only mode)."""
    if not _is_command_exec_enabled():
        return jsonify({"success": False, "error": "Container command execution is disabled"}), 403
    if not ENABLE_PCT_CONTAINER_EXEC:
        return jsonify({
            "success": False,
            "error": "pct-based LXC exec is disabled. Enable ENABLE_PCT_CONTAINER_EXEC=true only on a PVE host.",
        }), 403

    try:
        params = request.json or {}
        command = params.get("command", "").strip()
        timeout = _clamp_timeout(params.get("timeout", 30))

        if not command:
            return jsonify({"success": False, "error": "command parameter is required"}), 400

        # Validate target container through Proxmox API first.
        proxmox.nodes(node).lxc(vmid).status.current.get()

        pct_cmd, exec_mode = _build_pct_exec_command(vmid, command)

        result = _run_command(
            pct_cmd,
            timeout=timeout,
        )
        result["node"] = node
        result["vmid"] = vmid
        result["exec_mode"] = exec_mode
        status_code = 408 if result.get("timed_out") else 200
        return jsonify(result), status_code
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error executing container command on {node}/{vmid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/verify-web", methods=["POST"])
def verify_container_web(node, vmid):
    """Run strict web deployment checks inside a container.

    Checks service activity and local HTTP response from inside the guest.
    """
    if not _is_command_exec_enabled():
        return jsonify({"success": False, "error": "Container command execution is disabled"}), 403
    if not ENABLE_PCT_CONTAINER_EXEC:
        return jsonify({
            "success": False,
            "error": "pct-based LXC exec is disabled. Enable ENABLE_PCT_CONTAINER_EXEC=true only on a PVE host.",
        }), 403

    try:
        params = request.json or {}
        service_name = str(params.get("service", "apache2")).strip() or "apache2"
        port = _safe_int(params.get("port", 80), 80)
        path = str(params.get("path", "/")).strip() or "/"
        timeout = _clamp_timeout(params.get("timeout", 30))
        install_probe = _safe_bool(params.get("install_probe", True), True)

        if not path.startswith("/"):
            path = "/" + path

        proxmox.nodes(node).lxc(vmid).status.current.get()

        service_cmd, exec_mode = _build_pct_exec_command(vmid, f"systemctl is-active {shlex.quote(service_name)}")
        service_result = _run_command(service_cmd, timeout=timeout)
        service_state = (service_result.get("stdout", "") or "").strip().splitlines()
        service_state_value = service_state[-1].strip() if service_state else "unknown"
        service_ok = bool(service_result.get("success")) and service_state_value == "active"

        tool_check_cmd, _ = _build_pct_exec_command(
            vmid,
            "if command -v curl >/dev/null 2>&1; then echo curl; "
            "elif command -v wget >/dev/null 2>&1; then echo wget; "
            "else echo none; fi",
        )
        tool_check_result = _run_command(tool_check_cmd, timeout=timeout)
        http_probe_tool = (tool_check_result.get("stdout", "") or "").strip().splitlines()
        probe_tool = http_probe_tool[-1].strip() if http_probe_tool else "none"

        install_result = None
        if probe_tool == "none" and install_probe:
            install_cmd, _ = _build_pct_exec_command(vmid, "apt-get update && apt-get install -y curl")
            install_result = _run_command(install_cmd, timeout=timeout)
            if install_result.get("success"):
                probe_tool = "curl"

        probe_url = f"http://127.0.0.1:{port}{path}"
        if probe_tool == "curl":
            probe_cmd_text = f"curl -fsS --max-time 8 {shlex.quote(probe_url)}"
        elif probe_tool == "wget":
            probe_cmd_text = f"wget -qO- {shlex.quote(probe_url)}"
        else:
            probe_cmd_text = "echo 'No HTTP probe tool available (curl/wget missing)' >&2; exit 127"

        probe_cmd, _ = _build_pct_exec_command(vmid, probe_cmd_text)
        probe_result = _run_command(probe_cmd, timeout=timeout)
        http_ok = bool(probe_result.get("success"))

        network = _get_container_ipv4_details(vmid, timeout=min(timeout, 20))
        overall_success = service_ok and http_ok and bool(network.get("ipv4"))

        response = {
            "success": overall_success,
            "result": "success" if overall_success else "partial",
            "node": node,
            "vmid": vmid,
            "exec_mode": exec_mode,
            "service": {
                "name": service_name,
                "active": service_ok,
                "state": service_state_value,
                "result": service_result,
            },
            "http": {
                "url": probe_url,
                "tool": probe_tool,
                "install_probe": install_probe,
                "tool_check": tool_check_result,
                "install_result": install_result,
                "result": probe_result,
                "ok": http_ok,
            },
            "guest_network": network,
            "guest_ipv4": network.get("ipv4"),
            "guest_ipv4_addresses": network.get("ipv4_addresses", []),
        }
        return jsonify(response), 200
    except Exception as e:
        logger.error(f"Error verifying web deployment in container {node}/{vmid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/containers/<node>/<int:vmid>/sync-file", methods=["POST"])
def sync_container_file(node, vmid):
    """Write a text file inside an LXC container using pct push + pct exec."""
    if not _is_command_exec_enabled():
        return jsonify({"success": False, "error": "Container file sync is disabled"}), 403
    if not ENABLE_PCT_CONTAINER_EXEC:
        return jsonify({
            "success": False,
            "error": "pct-based LXC file sync is disabled. Enable ENABLE_PCT_CONTAINER_EXEC=true only on a PVE host.",
        }), 403

    try:
        params = request.json or {}
        path = params.get("path", "")
        content = params.get("content")
        timeout = _clamp_timeout(params.get("timeout", 30))
        mode = str(params.get("mode", "0644"))
        owner = params.get("owner")
        group = params.get("group")
        create_dirs = bool(params.get("create_dirs", True))

        if content is None:
            return jsonify({"success": False, "error": "content parameter is required"}), 400

        # Validate target container through Proxmox API first.
        proxmox.nodes(node).lxc(vmid).status.current.get()

        result = _sync_file_to_container(
            vmid=vmid,
            path=str(path),
            content=str(content),
            timeout=timeout,
            mode=mode,
            owner=owner if isinstance(owner, str) and owner.strip() else None,
            group=group if isinstance(group, str) and group.strip() else None,
            create_dirs=create_dirs,
        )
        result["node"] = node
        result["vmid"] = vmid
        status_code = 200 if result.get("success") else 500
        return jsonify(result), status_code
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error syncing file to container {node}/{vmid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/deploy/container-from-zip", methods=["POST"])
def deploy_container_from_zip():
    """Create a new container and deploy a zip archive into it.

    Accepts either multipart form upload (field name `archive`) or JSON with
    `zip_base64`. The archive is unpacked safely on the host, transferred into
    the container, and a basic service plan is generated from the contents or
    an optional `deploy.json` manifest.
    """
    if not _is_command_exec_enabled():
        return jsonify({"success": False, "error": "Deployment is disabled (ENABLE_COMMAND_EXEC=false)"}), 403
    if not ENABLE_PCT_CONTAINER_EXEC:
        return jsonify({
            "success": False,
            "error": "pct-based deployment requires ENABLE_PCT_CONTAINER_EXEC=true on a PVE host.",
        }), 403

    temp_paths = []
    try:
        payload = request.get_json(silent=True) or {}
        form = request.form or {}
        files = request.files or {}

        node = _resolve_deploy_node(form.get("node") or payload.get("node"))
        hostname = str(form.get("hostname") or payload.get("hostname") or "").strip()
        vmid = form.get("vmid") or payload.get("vmid")
        ostemplate = str(form.get("ostemplate") or payload.get("ostemplate") or "").strip()
        ostype = str(form.get("ostype") or payload.get("ostype") or "").strip() or None
        storage = str(form.get("storage") or payload.get("storage") or DEFAULT_CT_STORAGE).strip()
        rootfs = str(form.get("rootfs") or payload.get("rootfs") or DEFAULT_CT_ROOTFS).strip()
        memory = _safe_int(form.get("memory") or payload.get("memory") or DEFAULT_CT_MEMORY, DEFAULT_CT_MEMORY)
        cores = _safe_int(form.get("cores") or payload.get("cores") or DEFAULT_CT_CORES, DEFAULT_CT_CORES)
        swap = _safe_int(form.get("swap") or payload.get("swap") or 0, 0)
        net0 = str(form.get("net0") or payload.get("net0") or f"name=eth0,bridge={DEFAULT_CT_BRIDGE},ip=dhcp").strip()
        description = str(form.get("description") or payload.get("description") or "").strip()
        password = form.get("password") or payload.get("password")
        start_after_create = _safe_bool(form.get("start") if "start" in form else payload.get("start", True), True)
        create_container = _safe_bool(form.get("create_container") if "create_container" in form else payload.get("create_container", True), True)
        deploy_timeout = _safe_int(form.get("deploy_timeout") or payload.get("deploy_timeout") or DEFAULT_DEPLOY_TIMEOUT, DEFAULT_DEPLOY_TIMEOUT)
        if deploy_timeout < 60:
            deploy_timeout = 60
        app_name = str(form.get("name") or payload.get("name") or hostname or "deployed-app").strip()

        if not node:
            return jsonify({"success": False, "error": "node is required"}), 400

        archive_bytes = None
        archive_name = str(form.get("archive_name") or payload.get("archive_name") or "app.zip").strip() or "app.zip"
        if "archive" in files:
            upload = files["archive"]
            archive_name = upload.filename or archive_name
            archive_bytes = upload.read()
        elif payload.get("zip_base64"):
            try:
                archive_bytes = base64.b64decode(str(payload.get("zip_base64")), validate=True)
            except Exception as exc:
                return jsonify({"success": False, "error": f"Invalid zip_base64 payload: {exc}"}), 400
        else:
            return jsonify({"success": False, "error": "Provide an uploaded file field named archive or a zip_base64 value"}), 400

        if not archive_bytes:
            return jsonify({"success": False, "error": "Archive is empty"}), 400

        if not hostname:
            hostname = _sanitize_service_name(os.path.splitext(os.path.basename(archive_name))[0] or app_name, fallback="app")

        staging_dir = tempfile.mkdtemp(prefix="proxmox-deploy-")
        temp_paths.append(staging_dir)
        zip_path = os.path.join(staging_dir, os.path.basename(archive_name) or "app.zip")
        with open(zip_path, "wb") as handle:
            handle.write(archive_bytes)

        extract_dir = os.path.join(staging_dir, "extract")
        os.makedirs(extract_dir, exist_ok=True)
        _safe_extract_zip(zip_path, extract_dir)

        deploy_plan = _build_deploy_plan(extract_dir, archive_name, {
            "name": app_name,
            "port": form.get("port") or payload.get("port"),
            "workdir": form.get("workdir") or payload.get("workdir"),
            "service_name": form.get("service_name") or payload.get("service_name"),
            "start_command": form.get("start_command") or payload.get("start_command"),
            "install_commands": payload.get("install_commands") if isinstance(payload.get("install_commands"), list) else None,
        })

        if not hostname:
            hostname = _sanitize_service_name(deploy_plan["name"], fallback="app")

        if not vmid:
            vmid = int(proxmox.cluster.nextid.get())
        else:
            vmid = int(vmid)

        resolved_template = _resolve_ostemplate(node, ostemplate)
        if not resolved_template:
            return jsonify({
                "success": False,
                "error": "No container template found. Provide ostemplate or set DEFAULT_CT_OSTEMPLATE.",
            }), 400

        if ostype is None:
            lowered = resolved_template.lower()
            if "ubuntu" in lowered:
                ostype = "ubuntu"
            elif "alpine" in lowered:
                ostype = "alpine"
            else:
                ostype = "debian"

        container_result = None
        if create_container:
            rootfs_spec = rootfs if ":" in rootfs else f"{storage}:{rootfs}"
            container_params = {
                "vmid": vmid,
                "hostname": hostname,
                "ostype": ostype,
                "memory": memory,
                "cores": cores,
                "rootfs": rootfs_spec,
                "features": "nesting=1",
                "net0": net0,
                "start": 1 if start_after_create else 0,
                "ostemplate": resolved_template,
            }
            if swap > 0:
                container_params["swap"] = swap
            if description:
                container_params["description"] = description
            if password:
                container_params["password"] = str(password)

            container_result = proxmox.nodes(node).lxc.create(**container_params)
            logger.info(f"Created container {vmid} for zip deployment on {node}")

        running_check = _wait_for_container_running(node, vmid, timeout_seconds=min(deploy_timeout, 300))
        if not running_check.get("success"):
            return jsonify({"success": False, "error": running_check.get("error", "Container failed to start"), "details": running_check}), 500

        tar_path = os.path.join(staging_dir, "payload.tar.gz")
        _build_tarball(extract_dir, tar_path)
        temp_paths.append(tar_path)

        with open(tar_path, "rb") as handle:
            tar_bytes = handle.read()

        remote_tar = "/tmp/deploy-payload.tar.gz"
        upload_result = _write_bytes_to_container(
            vmid=vmid,
            path=remote_tar,
            content_bytes=tar_bytes,
            timeout=deploy_timeout,
            mode="0644",
            create_dirs=True,
        )
        if not upload_result.get("success"):
            return jsonify({"success": False, "error": "Failed to transfer payload into container", "details": upload_result}), 500

        deploy_root = deploy_plan["workdir"]
        container_cmds = [
            f"mkdir -p {shlex.quote(deploy_root)}",
            f"tar -xzf {shlex.quote(remote_tar)} -C {shlex.quote(deploy_root)}",
            f"rm -f {shlex.quote(remote_tar)}",
        ]

        if deploy_plan["install_commands"]:
            container_cmds = deploy_plan["install_commands"] + container_cmds

        for command in container_cmds:
            cmd_result = _run_command(_build_pct_exec_command(vmid, command)[0], timeout=deploy_timeout)
            if not cmd_result.get("success"):
                return jsonify({
                    "success": False,
                    "error": f"Deployment step failed: {command}",
                    "details": cmd_result,
                }), 500

        service_name = _sanitize_service_name(deploy_plan["service_name"], fallback=hostname)
        service_unit = (
            "[Unit]\n"
            f"Description=Deployed application {deploy_plan['name']}\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"WorkingDirectory={deploy_root}\n"
        )
        for key, value in deploy_plan["environment"].items():
            service_unit += f"Environment={key}={value}\n"
        service_unit += (
            f"ExecStart=/bin/sh -lc {shlex.quote(deploy_plan['start_command'])}\n"
            "Restart=always\n"
            "RestartSec=5\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

        service_path = f"/etc/systemd/system/{service_name}.service"
        service_write = _sync_file_to_container(
            vmid=vmid,
            path=service_path,
            content=service_unit,
            timeout=deploy_timeout,
            mode="0644",
            create_dirs=True,
        )
        if not service_write.get("success"):
            return jsonify({"success": False, "error": "Failed to write systemd service", "details": service_write}), 500

        final_cmds = [
            "systemctl daemon-reload",
            f"systemctl enable --now {shlex.quote(service_name)}.service",
        ]
        for command in final_cmds:
            cmd_result = _run_command(_build_pct_exec_command(vmid, command)[0], timeout=deploy_timeout)
            if not cmd_result.get("success"):
                return jsonify({
                    "success": False,
                    "error": f"Failed to start service with command: {command}",
                    "details": cmd_result,
                    "container": {"node": node, "vmid": vmid, "hostname": hostname},
                    "deployment": deploy_plan,
                }), 500

        return jsonify({
            "success": True,
            "message": f"Container {vmid} deployed from zip archive",
            "container": {
                "node": node,
                "vmid": vmid,
                "hostname": hostname,
                "created": bool(container_result),
                "template": resolved_template,
            },
            "deployment": deploy_plan,
            "service_path": service_path,
            "notes": [
                "The archive was unpacked safely on the host before being pushed into the container.",
                "The container is started with a systemd service based on detected project files or deploy.json.",
            ],
        })
    except Exception as e:
        logger.error(f"Error deploying container from zip: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        for path in reversed(temp_paths):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                elif os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint"""
    try:
        version_info = proxmox.version.get()
        return jsonify({"success": True, "status": "healthy", "proxmox": ACTIVE_PROXMOX_HOST})
    except Exception as e:
        return jsonify({"success": False, "status": "unhealthy", "error": str(e)}), 503


@app.route("/", methods=["GET"])
def index():
    """Root endpoint - API info"""
    return jsonify({
        "name": "Proxmox VE API Server",
        "version": "1.1",
        "description": "HTTP API wrapper for Proxmox VE",
        "proxmox_host": ACTIVE_PROXMOX_HOST,
        "proxmox_primary_host": PROXMOX_HOST,
        "proxmox_fallback_hosts": PROXMOX_HOST_FALLBACKS,
        "api_port": API_PORT,
        "auth_mode": "token" if (PROXMOX_TOKEN_NAME and PROXMOX_TOKEN_VALUE) else "password",
        "command_exec_enabled": ENABLE_COMMAND_EXEC,
        "pct_container_exec_enabled": ENABLE_PCT_CONTAINER_EXEC,
        "endpoints": {
            "cluster": "/api/cluster/status",
            "cluster_nextid": "/api/cluster/nextid",
            "tasks": "/api/tasks",
            "task_status": "/api/tasks/<upid>/status",
            "task_log": "/api/tasks/<upid>/log",
            "nodes": "/api/nodes",
            "vms": "/api/vms",
            "containers": "/api/containers",
            "storage": "/api/storage",
            "backups": "/api/backups",
            "vm_agent_ping": "/api/vms/<node>/<vmid>/agent/ping",
            "vm_agent_network": "/api/vms/<node>/<vmid>/agent/network",
            "vm_agent_exec": "/api/vms/<node>/<vmid>/agent/exec",
            "vm_agent_exec_status": "/api/vms/<node>/<vmid>/agent/exec-status",
            "host_exec": "/api/host/exec",
            "container_exec": "/api/containers/<node>/<vmid>/exec",
            "container_details": "/api/containers/<node>/<vmid>/details",
            "container_verify_web": "/api/containers/<node>/<vmid>/verify-web",
            "container_sync_file": "/api/containers/<node>/<vmid>/sync-file",
            "health": "/api/health"
        }
    })


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proxmox API Server")
    parser.add_argument("--port", type=int, default=API_PORT, help=f"API port (default: {API_PORT})")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", default=DEBUG_MODE, help="Debug mode")
    
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("Proxmox VE API Server")
    logger.info("=" * 60)

    for attempt in range(1, STARTUP_RETRY_COUNT + 1):
        if init_proxmox():
            break
        if attempt < STARTUP_RETRY_COUNT:
            logger.warning(
                f"Proxmox connection failed during startup, retrying in {STARTUP_RETRY_DELAY}s "
                f"({attempt}/{STARTUP_RETRY_COUNT})"
            )
            time.sleep(STARTUP_RETRY_DELAY)
    else:
        logger.error("Failed to initialize Proxmox connection after startup retries")
        sys.exit(1)
    
    logger.info(f"Starting API Server on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
