#!/usr/bin/env python3

"""
MCP HTTP Streaming Server for Kali Linux Tools

This server wraps the Flask API (server.py) and exposes Kali tools via MCP HTTP streaming transport.
Supports both modern HTTP streaming (Streamable HTTP) and legacy SSE fallback for compatibility.

Usage:
    python3 mcp_http_server.py --port 5001
    # or
    python3 mcp_http_server.py --port 5001 --kali-url http://192.168.1.223:5000
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

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
DEFAULT_KALI_SERVER = "http://localhost:5000"
DEFAULT_MCP_PORT = 5001
DEFAULT_REQUEST_TIMEOUT = 900  # 15 minutes
DEFAULT_MCP_HOST = "0.0.0.0"

class KaliMCPServer:
    """MCP Server wrapper for Kali Linux Tools API"""

    def __init__(
        self,
        kali_server_url: str = DEFAULT_KALI_SERVER,
        request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
        mcp_port: int = DEFAULT_MCP_PORT,
        mcp_host: str = DEFAULT_MCP_HOST,
    ):
        self.kali_server_url = kali_server_url.rstrip("/")
        self.request_timeout = request_timeout
        self.mcp_port = mcp_port
        self.mcp_host = mcp_host
        self.session = requests.Session()
        
        # Add API Key from environment if defined
        mcp_api_key = os.getenv("MCP_API_KEY", "")
        if mcp_api_key:
            self.session.headers.update({"Authorization": f"Bearer {mcp_api_key}"})

        # Create MCP server
        self.server = Server("kali-tools-sse")
        
        logger.info(f"Initializing Kali Tools MCP HTTP Server")
        logger.info(f"Kali API Server: {self.kali_server_url}")
        logger.info(f"MCP HTTP Host: {self.mcp_host}")
        logger.info(f"MCP HTTP Port: {self.mcp_port}")

        # Register tool handlers using the proper Server API
        self._register_tools()

    def _register_tools(self):
        """Register all available Kali tools as MCP tools"""
        
        # Register tool request handler
        @self.server.call_tool()
        async def handle_tool_call(name: str, arguments: Dict[str, Any]) -> list:
            """Handle tool call requests with non-blocking execution."""
            try:
                # Use asyncio.to_thread to prevent blocking the event loop
                result = await asyncio.to_thread(self._call_kali_tool, name, arguments)
                return [TextContent(type="text", text=result)]
            except Exception as e:
                error = json.dumps({"error": str(e), "success": False})
                return [TextContent(type="text", text=error)]
        
        # Register list tools handler
        @self.server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            """List available tools as typed MCP Tool objects."""
            return [
                Tool(name="nmap", description="Port scanning with nmap", inputSchema=self._create_nmap_schema()),
                Tool(name="gobuster", description="Directory/DNS enumeration", inputSchema=self._create_gobuster_schema()),
                Tool(name="dirb", description="Web directory scanner", inputSchema=self._create_dirb_schema()),
                Tool(name="nikto", description="Web server vulnerability scanner", inputSchema=self._create_nikto_schema()),
                Tool(name="sqlmap", description="SQL injection testing", inputSchema=self._create_sqlmap_schema()),
                Tool(name="hydra", description="Credential brute-forcing", inputSchema=self._create_hydra_schema()),
                Tool(name="john", description="Password hash cracking", inputSchema=self._create_john_schema()),
                Tool(name="wpscan", description="WordPress security scanning", inputSchema=self._create_wpscan_schema()),
                Tool(name="enum4linux", description="SMB/CIFS enumeration", inputSchema=self._create_enum4linux_schema()),
                Tool(name="metasploit", description="Execute Metasploit modules", inputSchema=self._create_metasploit_schema()),
                Tool(name="execute_command", description="Execute arbitrary shell commands", inputSchema=self._create_execute_command_schema()),
            ]
        
        logger.info("All Kali tools registered!")

    def _create_nmap_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Target host/IP address"},
                "scan_type": {"type": "string", "description": "Scan type", "default": "-sCV"},
                "ports": {"type": "string", "description": "Port range"},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": "-T4 -Pn"},
            },
            "required": ["target"],
        }

    def _create_gobuster_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Target URL or domain"},
                "mode": {"type": "string", "description": "Mode (dir, dns, fuzz, vhost)", "default": "dir"},
                "wordlist": {"type": "string", "description": "Wordlist path", "default": "/usr/share/wordlists/dirb/common.txt"},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": ""},
            },
            "required": ["url"],
        }

    def _create_dirb_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Target URL"},
                "wordlist": {"type": "string", "description": "Wordlist path", "default": "/usr/share/wordlists/dirb/common.txt"},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": ""},
            },
            "required": ["url"],
        }

    def _create_nikto_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Target host/IP"},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": ""},
            },
            "required": ["target"],
        }

    def _create_sqlmap_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Target URL"},
                "data": {"type": "string", "description": "POST data", "default": ""},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": ""},
            },
            "required": ["url"],
        }

    def _create_hydra_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Target host/IP"},
                "service": {"type": "string", "description": "Service (ssh, http-get, ftp, etc.)"},
                "username": {"type": "string", "description": "Single username", "default": ""},
                "username_file": {"type": "string", "description": "Usernames file", "default": ""},
                "password": {"type": "string", "description": "Single password", "default": ""},
                "password_file": {"type": "string", "description": "Passwords file", "default": ""},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": ""},
            },
            "required": ["target", "service"],
        }

    def _create_john_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "hash_file": {"type": "string", "description": "Hash file path"},
                "wordlist": {"type": "string", "description": "Wordlist path", "default": "/usr/share/wordlists/rockyou.txt"},
                "format_type": {"type": "string", "description": "Hash format", "default": ""},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": ""},
            },
            "required": ["hash_file"],
        }

    def _create_wpscan_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "WordPress site URL"},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": ""},
            },
            "required": ["url"],
        }

    def _create_enum4linux_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Target host/IP"},
                "additional_args": {"type": "string", "description": "Additional arguments", "default": "-a"},
            },
            "required": ["target"],
        }

    def _create_metasploit_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "module": {"type": "string", "description": "Module path"},
                "options": {"type": "object", "description": "Module options", "default": {}},
            },
            "required": ["module"],
        }

    def _create_execute_command_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
            },
            "required": ["command"],
        }

    def _call_kali_tool(self, tool_name: str, params: Dict[str, Any]) -> str:
        """
        Call a tool via the Kali API server.

        Args:
            tool_name: Name of the tool
            params: Parameters to send to the tool

        Returns:
            Tool output as JSON string
        """
        try:
            if tool_name in ("command", "execute_command"):
                url = f"{self.kali_server_url}/api/command"
            else:
                url = f"{self.kali_server_url}/api/tools/{tool_name}"

            logger.debug(f"Calling {tool_name}: {url}")
            logger.debug(f"Parameters: {params}")

            response = self.session.post(
                url,
                json=params,
                timeout=self.request_timeout,
            )
            response.raise_for_status()

            result = response.json()
            logger.debug(f"{tool_name} response: {result}")

            # Format the output for MCP
            if isinstance(result, dict):
                output = {
                    "tool": tool_name,
                    "success": result.get("success", result.get("return_code") == 0),
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                    "return_code": result.get("return_code"),
                }
                return json.dumps(output, indent=2)
            else:
                return json.dumps({"tool": tool_name, "result": result}, indent=2)

        except requests.exceptions.Timeout:
            error_msg = f"Request to Kali server timed out (>{self.request_timeout}s)"
            logger.error(error_msg)
            return json.dumps({
                "tool": tool_name,
                "success": False,
                "error": error_msg,
            })
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to Kali server at {self.kali_server_url}: {str(e)}"
            logger.error(error_msg)
            return json.dumps({
                "tool": tool_name,
                "success": False,
                "error": error_msg,
            })
        except requests.exceptions.RequestException as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(error_msg)
            return json.dumps({
                "tool": tool_name,
                "success": False,
                "error": error_msg,
            })
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return json.dumps({
                "tool": tool_name,
                "success": False,
                "error": error_msg,
            })

    async def async_run(self):
        """Run the MCP HTTP server using Starlette + uvicorn.

                Supports two MCP transports:
                    GET/POST /mcp      → MCP Streamable HTTP (Open WebUI, mcp >= 1.1)
                    GET      /sse      → legacy SSE fallback (older clients)
                    POST     /messages/→ legacy SSE post messages
        """
        import uuid
        from starlette.middleware.cors import CORSMiddleware
        import uvicorn

        sse_transport = SseServerTransport("/messages/")

        try:
            from mcp.server.streamable_http import StreamableHTTPServerTransport
            _streamable_available = True
            logger.info("Streamable HTTP transport available (Open WebUI compatible)")
        except ImportError:
            _streamable_available = False
            logger.warning(
                "mcp.server.streamable_http not found — only legacy SSE transport active. "
                "Upgrade mcp package (pip install -U mcp) for Open WebUI compatibility."
            )

        # Session store: session_id -> StreamableHTTPServerTransport
        _sessions: dict[str, dict[str, Any]] = {}

        def _scope_header(scope, name: str) -> Optional[str]:
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
            existing = _sessions.get(session_id)
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
            _sessions[session_id] = {"transport": transport, "task": task, "ready": ready}
            await ready.wait()
            return transport

        def _cleanup_streamable_sessions():
            stale_ids = []
            for session_id, session in _sessions.items():
                transport = session["transport"]
                task = session["task"]
                if transport.is_terminated or task.done():
                    stale_ids.append(session_id)
            for session_id in stale_ids:
                _sessions.pop(session_id, None)

        async def handle_sse_route(scope, receive, send):
            """Raw ASGI handler for legacy SSE compatibility transport."""
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
            if not _streamable_available:
                await _send_plain_response(
                    send,
                    501,
                    "Streamable HTTP transport is unavailable. Upgrade mcp package.",
                )
                return

            request_session_id = _scope_header(scope, "mcp-session-id")
            if request_session_id:
                session = _sessions.get(request_session_id)
                if not session:
                    await _send_plain_response(send, 404, "Invalid or expired session ID")
                    return
                transport = session["transport"]
            else:
                # New Streamable HTTP sessions start without Mcp-Session-Id and
                # receive one on initialize response.
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
                    # Keep legacy GUI compatibility: plain GET without
                    # session header uses old SSE transport.
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

        logger.info(f"Starting MCP HTTP Server on http://{self.mcp_host}:{self.mcp_port}/mcp")
        logger.info(f"Backward-compatible endpoint available at http://{self.mcp_host}:{self.mcp_port}/sse")
        logger.info(f"MCP messages endpoint available at http://{self.mcp_host}:{self.mcp_port}/messages/")
        logger.info(f"Open WebUI: add as 'MCP Streamable HTTP' → http://your-ip:{self.mcp_port}/mcp")
        logger.info(f"LibreChat:  add as 'SSE'                  → http://your-ip:{self.mcp_port}/sse")
        logger.info("Server running - waiting for connections...")

        config = uvicorn.Config(app=app, host=self.mcp_host, port=self.mcp_port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    def run(self):
        """Run the MCP HTTP server"""
        try:
            asyncio.run(self.async_run())
        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Error running server: {str(e)}", exc_info=True)
            sys.exit(1)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="MCP HTTP Server for Kali Linux Tools",
        epilog="""
Examples:
  python3 mcp_http_server.py --port 5001
  python3 mcp_http_server.py --port 5001 --kali-url http://192.168.1.223:5000
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_MCP_HOST,
        help=f"Host to bind MCP HTTP server to (default: {DEFAULT_MCP_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_MCP_PORT,
        help=f"Port to run MCP HTTP server on (default: {DEFAULT_MCP_PORT})",
    )
    parser.add_argument(
        "--kali-url",
        default=DEFAULT_KALI_SERVER,
        help=f"URL of the Kali API server (default: {DEFAULT_KALI_SERVER})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_REQUEST_TIMEOUT})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    # Create and run server
    server = KaliMCPServer(
        kali_server_url=args.kali_url,
        request_timeout=args.timeout,
        mcp_port=args.port,
        mcp_host=args.host,
    )
    server.run()


if __name__ == "__main__":
    main()
