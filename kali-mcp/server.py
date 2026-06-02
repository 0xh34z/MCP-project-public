#!/usr/bin/env python3

# This script connect the MCP AI agent to Kali Linux terminal and API Server.

# some of the code here was inspired from https://github.com/whit3rabbit0/project_astro , be sure to check them out

import argparse
import json
import logging
import os
import subprocess
import sys
import traceback
import threading
from typing import Dict, Any
from flask import Flask, request, jsonify

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration
API_PORT = int(os.environ.get("API_PORT", 5000))
DEBUG_MODE = os.environ.get("DEBUG_MODE", "0").lower() in ("1", "true", "yes", "y")
COMMAND_TIMEOUT = 600  # 10 minutes default timeout
MAX_OUTPUT_CHARS = int(os.environ.get("MAX_OUTPUT_CHARS", 30000))

import re
import html
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # dotenv is optional; if not present the process will still read env vars from the environment
    pass


def _extract_ip_from_header(header: str):
    ip_match = re.search(r'\((\d{1,3}(?:\.\d{1,3}){3})\)', header)
    if ip_match:
        return ip_match.group(1)

    ip_only = re.match(r'^(\d{1,3}(?:\.\d{1,3}){3})$', header)
    if ip_only:
        return ip_only.group(1)

    return None


def _parse_nmap_output(raw_output: str, max_hosts: int = 200) -> Dict[str, Any]:
    text = str(raw_output or '')
    lines = [line.rstrip() for line in text.splitlines()]

    host_blocks = []
    current_host = None
    current_lines = []

    for line in lines:
        host_match = re.match(r'Nmap scan report for\s+(.+)', line)
        if host_match:
            if current_host is not None:
                host_blocks.append({'header': current_host, 'lines': current_lines})
            current_host = host_match.group(1).strip()
            current_lines = [line.rstrip()]
            continue

        if current_host is not None:
            current_lines.append(line.rstrip())

    if current_host is not None:
        host_blocks.append({'header': current_host, 'lines': current_lines})

    hosts = []
    seen = set()
    for block in host_blocks[:max_hosts]:
        header = str(block.get('header') or '').strip()
        if not header:
            continue
        key = header.lower()
        if key in seen:
            continue
        seen.add(key)

        entry = {
            'host': header,
            'ip': _extract_ip_from_header(header),
            'ports': [],
            'mac': None,
            'service_info': None,
            'raw_lines': block.get('lines', []),
        }

        for raw_line in block.get('lines', []):
            line = str(raw_line).strip()
            if line.startswith('MAC Address:'):
                parts = line.split('MAC Address:')[-1].strip()
                entry['mac'] = parts.split()[0] if parts else None
            if line.startswith('Service Info:'):
                entry['service_info'] = line.split('Service Info:')[-1].strip()

            port_match = re.match(r'^(\d+)/(tcp|udp)\s+(open|closed|filtered|open\|filtered)\s+(.+)$', line)
            if port_match:
                rest = port_match.group(4).strip()
                service = rest.split()[0] if rest else rest
                entry['ports'].append({
                    'port': int(port_match.group(1)),
                    'proto': port_match.group(2),
                    'state': port_match.group(3),
                    'service': service,
                    'version': None,
                })

        hosts.append(entry)

    done_match = re.search(r'Nmap done:\s+(\d+)\s+IP addresses\s+\((\d+)\s+hosts up\)', text)
    scanned = int(done_match.group(1)) if done_match else None
    up = int(done_match.group(2)) if done_match else (len(hosts) if hosts else None)

    return {
        'hosts': hosts,
        'scanned': scanned,
        'up': up,
        'truncated': any(marker in text.lower() for marker in ('... [truncated', 'results above may be incomplete', 'timed out after', 'partial_results')),
    }

def _compact_nmap_output(raw_output: str, max_hosts: int = 40) -> str:
    """Return a compact, exact Nmap digest with explicit anti-inference notes."""
    text = str(raw_output or '').strip()
    if not text:
        return '(empty nmap output)'

    host_blocks = []
    current_host = None
    current_lines = []

    for line in text.splitlines():
        host_match = re.match(r'Nmap scan report for\s+(.+)', line)
        if host_match:
            if current_host is not None:
                host_blocks.append({'host': current_host, 'lines': current_lines})
            current_host = host_match.group(1).strip()
            current_lines = [line.rstrip()]
            continue

        if current_host is not None:
            current_lines.append(line.rstrip())

    if current_host is not None:
        host_blocks.append({'host': current_host, 'lines': current_lines})

    unique_hosts = []
    seen = set()
    for block in host_blocks:
        host = str(block.get('host') or '').strip()
        if not host:
            continue
        k = host.lower()
        if k not in seen:
            seen.add(k)
            unique_hosts.append(block)

    # Get summary line
    done_match = re.search(r'Nmap done:\s+(\d+)\s+IP addresses\s+\((\d+)\s+hosts up\)', text)
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
            host = str(block.get('host') or '').strip()
            lines.append(f"- {host}")
            block_lines = [str(item).strip() for item in block.get('lines') or [] if str(item).strip()]
            for item in block_lines[1:]:
                if (
                    item.startswith('Host is up')
                    or item.startswith('PORT ')
                    or re.match(r'^\d+/(tcp|udp)\s+', item)
                    or item.startswith('MAC Address:')
                    or item.startswith('Service Info:')
                    or item.startswith('Not shown:')
                ):
                    lines.append(f"  - {item}")
        if len(unique_hosts) > max_hosts:
            lines.append(f"... and {len(unique_hosts) - max_hosts} more hosts.")

    if any(marker in text.lower() for marker in ('... [truncated', 'results above may be incomplete', 'timed out after', 'partial_results')):
        lines.append('Note: The original Nmap output was truncated or incomplete.')

    return '\n'.join(lines).strip()

def _sanitize_output(command: str, stdout: str, stderr: str) -> tuple[str, str]:
    """Reduce output size based on command type and global limits."""
    # If verbose is requested, do nothing (handled in route)
    cmd_lower = command.lower()
    
    # Special handling for Nmap
    if 'nmap' in cmd_lower:
        return _compact_nmap_output(stdout), stderr

    # Generic truncation for massive outputs (gobuster, nikto etc)
    shortened_out = stdout
    if len(stdout) > MAX_OUTPUT_CHARS:
        head = stdout[:MAX_OUTPUT_CHARS // 2]
        tail = stdout[-(MAX_OUTPUT_CHARS // 4):]
        shortened_out = (
            f"{head}\n\n... [truncated {len(stdout) - len(head) - len(tail)} chars] ...\n\n{tail}\n\n"
            "Note: The tool output was truncated. Do not infer omitted hosts, ports, files, or values."
        )
    
    return shortened_out, stderr

app = Flask(__name__)

MCP_API_KEY = os.getenv("MCP_API_KEY", "")

@app.before_request
def check_api_key():
    if MCP_API_KEY:
        auth_header = request.headers.get("Authorization")
        if not auth_header or auth_header != f"Bearer {MCP_API_KEY}":
            return jsonify({"error": "Unauthorized"}), 401

class CommandExecutor:
    """Class to handle command execution with better timeout management"""
    
    def __init__(self, command: str, timeout: int = COMMAND_TIMEOUT):
        self.command = command
        self.timeout = timeout
        self.process = None
        self.stdout_data = ""
        self.stderr_data = ""
        self.stdout_thread = None
        self.stderr_thread = None
        self.return_code = None
        self.timed_out = False
    
    def _read_stdout(self):
        """Thread function to continuously read stdout"""
        for line in iter(self.process.stdout.readline, ''):
            self.stdout_data += line
    
    def _read_stderr(self):
        """Thread function to continuously read stderr"""
        for line in iter(self.process.stderr.readline, ''):
            self.stderr_data += line
    
    def execute(self) -> Dict[str, Any]:
        """Execute the command and handle timeout gracefully"""
        logger.info(f"Executing command: {self.command}")
        
        try:
            self.process = subprocess.Popen(
                self.command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1  # Line buffered
            )
            
            # Start threads to read output continuously
            self.stdout_thread = threading.Thread(target=self._read_stdout)
            self.stderr_thread = threading.Thread(target=self._read_stderr)
            self.stdout_thread.daemon = True
            self.stderr_thread.daemon = True
            self.stdout_thread.start()
            self.stderr_thread.start()
            
            # Wait for the process to complete or timeout
            try:
                self.return_code = self.process.wait(timeout=self.timeout)
                # Process completed, join the threads
                self.stdout_thread.join()
                self.stderr_thread.join()
            except subprocess.TimeoutExpired:
                # Process timed out but we might have partial results
                self.timed_out = True
                logger.warning(f"Command timed out after {self.timeout} seconds. Terminating process.")
                
                # Try to terminate gracefully first
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)  # Give it 5 seconds to terminate
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't terminate
                    logger.warning("Process not responding to termination. Killing.")
                    self.process.kill()
                
                # Update final output
                self.stdout_data += f"\n\n[ERROR] Command timed out after {self.timeout}s. Results above may be incomplete."
                self.return_code = -1
            
            # Always consider it a success if we have output, even with timeout
            success = True if self.timed_out and (self.stdout_data or self.stderr_data) else (self.return_code == 0)
            
            return {
                "stdout": self.stdout_data,
                "stderr": self.stderr_data,
                "return_code": self.return_code,
                "success": success,
                "timed_out": self.timed_out,
                "partial_results": self.timed_out and (self.stdout_data or self.stderr_data)
            }
        
        except Exception as e:
            logger.error(f"Error executing command: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                "stdout": self.stdout_data,
                "stderr": f"Error executing command: {str(e)}\n{self.stderr_data}",
                "return_code": -1,
                "success": False,
                "timed_out": False,
                "partial_results": bool(self.stdout_data or self.stderr_data)
            }


def execute_command(command: str, sanitize: bool = True) -> Dict[str, Any]:
    """
    Execute a shell command and return the result
    """
    executor = CommandExecutor(command)
    res = executor.execute()
    res['raw_stdout'] = res.get('stdout', '')
    res['raw_stderr'] = res.get('stderr', '')
    
    if sanitize:
        res['stdout'], res['stderr'] = _sanitize_output(command, res['stdout'], res['stderr'])
        
    return res


@app.route("/api/command", methods=["POST"])
def generic_command():
    """Execute any command provided in the request."""
    try:
        params = request.json
        command = params.get("command", "")
        
        if not command:
            logger.warning("Command endpoint called without command parameter")
            return jsonify({
                "error": "Command parameter is required"
            }), 400

        # Strikt whitelisting van beheerscommando's
        ALLOWED_BASE_CMDS = {"ls", "df", "free", "uptime", "nmap", "gobuster", "dirb", "nikto", "sqlmap", "metasploit", "hydra", "john", "wpscan", "enum4linux", "whoami", "pwd"}
        base_cmd = command.split()[0]
        if base_cmd not in ALLOWED_BASE_CMDS:
            return jsonify({"error": f"Command not in whitelist: {base_cmd}"}), 403

        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in command endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500


@app.route("/api/tools/nmap", methods=["POST"])
def nmap():
    """Execute nmap scan with the provided parameters."""
    try:
        params = request.json
        target = params.get("target", "")
        scan_type = params.get("scan_type", "-sCV")
        ports = params.get("ports", "")
        additional_args = params.get("additional_args", "-T4 -Pn")
        
        if not target:
            logger.warning("Nmap called without target parameter")
            return jsonify({
                "error": "Target parameter is required"
            }), 400        
        
        command = f"nmap --privileged {scan_type}"
        
        if ports:
            command += f" -p {ports}"
        
        if additional_args:
            # Basic validation for additional args - more sophisticated validation would be better
            command += f" {additional_args}"
        
        command += f" {target}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        raw_stdout = str(result.get('raw_stdout') or '')
        if raw_stdout:
            result['nmap'] = _parse_nmap_output(raw_stdout)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in nmap endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/gobuster", methods=["POST"])
def gobuster():
    """Execute gobuster with the provided parameters."""
    try:
        params = request.json
        url = params.get("url", "")
        mode = params.get("mode", "dir")
        wordlist = params.get("wordlist", "/usr/share/wordlists/dirb/common.txt")
        additional_args = params.get("additional_args", "")
        
        if not url:
            logger.warning("Gobuster called without URL parameter")
            return jsonify({
                "error": "URL parameter is required"
            }), 400
        
        # Validate mode
        if mode not in ["dir", "dns", "fuzz", "vhost"]:
            logger.warning(f"Invalid gobuster mode: {mode}")
            return jsonify({
                "error": f"Invalid mode: {mode}. Must be one of: dir, dns, fuzz, vhost"
            }), 400
        
        command = f"gobuster {mode} -u {url} -w {wordlist}"
        
        if additional_args:
            command += f" {additional_args}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in gobuster endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/dirb", methods=["POST"])
def dirb():
    """Execute dirb with the provided parameters."""
    try:
        params = request.json
        url = params.get("url", "")
        wordlist = params.get("wordlist", "/usr/share/wordlists/dirb/common.txt")
        additional_args = params.get("additional_args", "")
        
        if not url:
            logger.warning("Dirb called without URL parameter")
            return jsonify({
                "error": "URL parameter is required"
            }), 400
        
        command = f"dirb {url} {wordlist}"
        
        if additional_args:
            command += f" {additional_args}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in dirb endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/nikto", methods=["POST"])
def nikto():
    """Execute nikto with the provided parameters."""
    try:
        params = request.json
        target = params.get("target", "")
        additional_args = params.get("additional_args", "")
        
        if not target:
            logger.warning("Nikto called without target parameter")
            return jsonify({
                "error": "Target parameter is required"
            }), 400
        
        command = f"nikto -h {target}"
        
        if additional_args:
            command += f" {additional_args}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in nikto endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/sqlmap", methods=["POST"])
def sqlmap():
    """Execute sqlmap with the provided parameters."""
    try:
        params = request.json
        url = params.get("url", "")
        data = params.get("data", "")
        additional_args = params.get("additional_args", "")
        
        if not url:
            logger.warning("SQLMap called without URL parameter")
            return jsonify({
                "error": "URL parameter is required"
            }), 400
        
        command = f"sqlmap -u {url} --batch"
        
        if data:
            command += f" --data=\"{data}\""
        
        if additional_args:
            command += f" {additional_args}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in sqlmap endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/metasploit", methods=["POST"])
def metasploit():
    """Execute metasploit module with the provided parameters."""
    try:
        params = request.json
        module = params.get("module", "")
        options = params.get("options", {})
        
        if not module:
            logger.warning("Metasploit called without module parameter")
            return jsonify({
                "error": "Module parameter is required"
            }), 400
        
        # Format options for Metasploit
        options_str = ""
        for key, value in options.items():
            options_str += f" {key}={value}"
        
        # Create an MSF resource script
        resource_content = f"use {module}\n"
        for key, value in options.items():
            resource_content += f"set {key} {value}\n"
        resource_content += "exploit\n"
        
        # Save resource script to a temporary file
        resource_file = "/tmp/mks_msf_resource.rc"
        with open(resource_file, "w") as f:
            f.write(resource_content)
        
        command = f"msfconsole -q -r {resource_file}"
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        
        # Clean up the temporary file
        try:
            os.remove(resource_file)
        except Exception as e:
            logger.warning(f"Error removing temporary resource file: {str(e)}")
            
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in metasploit endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/hydra", methods=["POST"])
def hydra():
    """Execute hydra with the provided parameters."""
    try:
        params = request.json
        target = params.get("target", "")
        service = params.get("service", "")
        username = params.get("username", "")
        username_file = params.get("username_file", "")
        password = params.get("password", "")
        password_file = params.get("password_file", "")
        additional_args = params.get("additional_args", "")
        
        if not target or not service:
            logger.warning("Hydra called without target or service parameter")
            return jsonify({
                "error": "Target and service parameters are required"
            }), 400
        
        if not (username or username_file) or not (password or password_file):
            logger.warning("Hydra called without username/password parameters")
            return jsonify({
                "error": "Username/username_file and password/password_file are required"
            }), 400
        
        command = f"hydra -t 4"
        
        if username:
            command += f" -l {username}"
        elif username_file:
            command += f" -L {username_file}"
        
        if password:
            command += f" -p {password}"
        elif password_file:
            command += f" -P {password_file}"
        
        command += f" {target} {service}"

        if additional_args:
            command += f" {additional_args}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in hydra endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/john", methods=["POST"])
def john():
    """Execute john with the provided parameters."""
    try:
        params = request.json
        hash_file = params.get("hash_file", "")
        wordlist = params.get("wordlist", "/usr/share/wordlists/rockyou.txt")
        format_type = params.get("format", "")
        additional_args = params.get("additional_args", "")
        
        if not hash_file:
            logger.warning("John called without hash_file parameter")
            return jsonify({
                "error": "Hash file parameter is required"
            }), 400
        
        command = f"john"
        
        if format_type:
            command += f" --format={format_type}"
        
        if wordlist:
            command += f" --wordlist={wordlist}"
        
        if additional_args:
            command += f" {additional_args}"
        
        command += f" {hash_file}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in john endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/wpscan", methods=["POST"])
def wpscan():
    """Execute wpscan with the provided parameters."""
    try:
        params = request.json
        url = params.get("url", "")
        additional_args = params.get("additional_args", "")
        
        if not url:
            logger.warning("WPScan called without URL parameter")
            return jsonify({
                "error": "URL parameter is required"
            }), 400
        
        command = f"wpscan --url {url}"
        
        if additional_args:
            command += f" {additional_args}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in wpscan endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

@app.route("/api/tools/enum4linux", methods=["POST"])
def enum4linux():
    """Execute enum4linux with the provided parameters."""
    try:
        params = request.json
        target = params.get("target", "")
        additional_args = params.get("additional_args", "-a")
        
        if not target:
            logger.warning("Enum4linux called without target parameter")
            return jsonify({
                "error": "Target parameter is required"
            }), 400
        
        command = f"enum4linux {additional_args} {target}"
        
        is_verbose = str(request.args.get("verbose", "false")).lower() in ("1", "true", "yes", "y")
        result = execute_command(command, sanitize=not is_verbose)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in enum4linux endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500


# Health check endpoint
@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    # Check if essential tools are installed
    essential_tools = ["nmap", "gobuster", "dirb", "nikto"]
    tools_status = {}
    
    for tool in essential_tools:
        try:
            result = execute_command(f"which {tool}")
            tools_status[tool] = result["success"]
        except:
            tools_status[tool] = False
    
    all_essential_tools_available = all(tools_status.values())
    
    return jsonify({
        "status": "healthy",
        "message": "Kali Linux Tools API Server is running",
        "tools_status": tools_status,
        "all_essential_tools_available": all_essential_tools_available
    })

@app.route("/mcp/capabilities", methods=["GET"])
def get_capabilities():
    # Return tool capabilities similar to our existing MCP server
    pass

@app.route("/mcp/tools/kali_tools/<tool_name>", methods=["POST"])
def execute_tool(tool_name):
    # Direct tool execution without going through the API server
    pass

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Run the Kali Linux API Server")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--port", type=int, default=API_PORT, help=f"Port for the API server (default: {API_PORT})")
    parser.add_argument("--ip", type=str, default="127.0.0.1", help="IP address to bind the server to (default: 127.0.0.1 for localhost only)")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    
    # Set configuration from command line arguments
    if args.debug:
        DEBUG_MODE = True
        os.environ["DEBUG_MODE"] = "1"
        logger.setLevel(logging.DEBUG)
    
    if args.port != API_PORT:
        API_PORT = args.port
    
    logger.info(f"Starting Kali Linux Tools API Server on {args.ip}:{API_PORT}")
    app.run(host=args.ip, port=API_PORT, debug=DEBUG_MODE)
