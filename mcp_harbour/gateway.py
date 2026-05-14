import logging
import socket
from contextlib import asynccontextmanager
from fnmatch import fnmatch
from http import HTTPStatus
from typing import Dict, List, Optional

import bcrypt
import keyring
import mcp.types as types
from mcp.server import Server
from mcp.server.streamable_http import MCP_SESSION_ID_HEADER
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import ConfigManager
from .errors import authorization_denied, server_unavailable
from .models import AgentPolicy
from .permissions import PermissionEngine
from .process_manager import HarbourDaemon

logger = logging.getLogger("mcp_harbour")


class HarbourAuthenticatedStreamableHTTPApp:
    def __init__(self, gateway: "HarbourGateway", manager: StreamableHTTPSessionManager):
        self.gateway = gateway
        self.manager = manager
        self._session_identities: Dict[str, str] = {}

    async def __call__(self, scope, receive, send):
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        identity_name = self.gateway._authenticate_authorization_header(
            headers.get("authorization")
        )
        if not identity_name:
            await self._send_auth_error(scope, receive, send)
            return

        request_session_id = headers.get(MCP_SESSION_ID_HEADER)
        if request_session_id:
            bound_identity = self._session_identities.get(request_session_id)
            if bound_identity and bound_identity != identity_name:
                await self._send_auth_error(scope, receive, send)
                return

        response_session_id = None

        async def send_with_session_binding(message):
            nonlocal response_session_id
            if message["type"] == "http.response.start":
                response_headers = {
                    key.decode("latin1").lower(): value.decode("latin1")
                    for key, value in message.get("headers", [])
                }
                response_session_id = response_headers.get(MCP_SESSION_ID_HEADER)
            await send(message)

        scope.setdefault("state", {})["harbour_identity"] = identity_name
        await self.manager.handle_request(scope, receive, send_with_session_binding)

        if response_session_id:
            self._session_identities[response_session_id] = identity_name
        if scope.get("method") == "DELETE" and request_session_id:
            self._session_identities.pop(request_session_id, None)

    async def _send_auth_error(self, scope, receive, send) -> None:
        response = JSONResponse(
            {"error": "Unauthorized"},
            status_code=HTTPStatus.UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


class HarbourGateway:
    def __init__(self):
        self.config_manager = ConfigManager()
        self.daemon = HarbourDaemon()
        self.session_server = Server("mcp-harbour")
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.session_server.list_tools()
        async def list_tools() -> List[Tool]:
            return await self._list_allowed_tools(self._current_identity_name())

        @self.session_server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
            return await self._call_tool_for_identity(
                self._current_identity_name(), name, arguments
            )

    def _current_identity_name(self) -> str:
        try:
            request = self.session_server.request_context.request
            identity_name = request.state.harbour_identity if request else None
        except (AttributeError, LookupError):
            identity_name = None
        if not identity_name:
            raise authorization_denied("Missing authenticated identity.")
        return identity_name

    def _resolve_identity_from_token(self, token: str) -> Optional[str]:
        for name in self.config_manager.config.identities:
            try:
                hashed_key = keyring.get_password("mcp-harbour", name)
                if hashed_key and bcrypt.checkpw(token.encode(), hashed_key.encode()):
                    return name
            except Exception as e:
                logger.error(f"Keyring error checking identity '{name}': {e}")
        return None

    def _extract_bearer_token(self, authorization: Optional[str]) -> Optional[str]:
        if not authorization:
            return None
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()

    def _authenticate_authorization_header(self, authorization: Optional[str]) -> Optional[str]:
        token = self._extract_bearer_token(authorization)
        if not token:
            return None
        self.config_manager.reload()
        return self._resolve_identity_from_token(token)

    def _load_identity_policy(self, identity_name: str) -> AgentPolicy:
        policy = self.config_manager.load_policy(identity_name)
        if not policy:
            return AgentPolicy(identity_name=identity_name, permissions={})
        return policy

    def _iter_accessible_processes(self, policy: AgentPolicy):
        for server_name in policy.permissions:
            process = self.daemon.get_shared_process(server_name)
            if process and process.session:
                yield server_name, process

    async def _list_allowed_tools(self, identity_name: str) -> List[Tool]:
        policy = self._load_identity_policy(identity_name)
        all_tools = []

        for server_name, process in self._iter_accessible_processes(policy):
            try:
                ship_tools = await process.list_tools()
                for tool in ship_tools.tools:
                    for perm in policy.permissions.get(server_name, []):
                        if fnmatch(tool.name, perm.name):
                            all_tools.append(tool)
                            break
            except Exception as e:
                logger.error(f"Error listing tools from {server_name}: {e}")

        return all_tools

    async def _resolve_tool_server(self, identity_name: str, tool_name: str) -> Optional[str]:
        policy = self._load_identity_policy(identity_name)

        for server_name, process in self._iter_accessible_processes(policy):
            try:
                ship_tools = await process.list_tools()
                for tool in ship_tools.tools:
                    if tool.name == tool_name:
                        return server_name
            except Exception as e:
                logger.error(f"Error listing tools from {server_name}: {e}")

        return None

    async def _call_tool_for_identity(
        self, identity_name: str, name: str, arguments: dict
    ) -> types.CallToolResult:
        policy = self._load_identity_policy(identity_name)
        engine = PermissionEngine(policy)
        server_name = await self._resolve_tool_server(identity_name, name)

        if not server_name:
            raise authorization_denied(f"Tool '{name}' not found on any docked server.")

        process = self.daemon.get_shared_process(server_name)
        if not process or not process.session:
            raise server_unavailable(server_name)

        engine.check_permission(server_name, name, arguments)

        logger.info(f"Routing tool '{name}' to server '{server_name}'")
        try:
            result = await process.call_tool(name, arguments)
            return result
        except Exception as e:
            if hasattr(e, "error"):
                raise
            logger.error(f"Error calling tool '{name}' on '{server_name}': {e}")
            raise server_unavailable(server_name)

    async def start_shared_processes(self):
        for server in self.config_manager.list_servers():
            try:
                await self.daemon.start_shared_server(server)
            except Exception as e:
                logger.error(f"Failed to start docked server '{server.name}': {e}")

    def _security_settings(self, host: str, port: int) -> TransportSecuritySettings:
        allowed_hosts = [
            host,
            f"{host}:{port}",
            "127.0.0.1",
            f"127.0.0.1:{port}",
            "localhost",
            f"localhost:{port}",
        ]
        return TransportSecuritySettings(allowed_hosts=allowed_hosts)

    def create_asgi_app(self, host: str, port: int) -> Starlette:
        manager = StreamableHTTPSessionManager(
            app=self.session_server,
            json_response=False,
            stateless=False,
            security_settings=self._security_settings(host, port),
        )
        http_app = HarbourAuthenticatedStreamableHTTPApp(self, manager)

        @asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                yield

        return Starlette(
            routes=[Route("/mcp", endpoint=http_app, methods=["GET", "POST", "DELETE"])],
            lifespan=lifespan,
        )

    async def serve(self, host: str, port: int):
        """Run the gateway over Streamable HTTP."""
        await self.start_shared_processes()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError as e:
                if e.errno in (98, 48, 10048):
                    logger.error(f"Port {port} is already in use. Is another harbour instance running?")
                    logger.error("Check with: harbour status")
                    logger.error("Or use a different port: harbour serve --port <port>")
                    raise SystemExit(1)
                raise

        app = self.create_asgi_app(host, port)

        import uvicorn

        logger.info(f"Listening on http://{host}:{port}/mcp")
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
