import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from mcp.server import Server as MCPServer
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.types import Tool, ListToolsResult, CallToolResult, TextContent, ListToolsRequest, CallToolRequest, CallToolRequestParams

from mcp_harbour.gateway import HarbourGateway
from mcp_harbour.models import Server
from mcp_harbour.process_manager import HarbourDaemon, ServerProcess


def make_gateway(config_manager) -> HarbourGateway:
    """Create a gateway with mocked internals pointing at the test config."""
    gateway = HarbourGateway.__new__(HarbourGateway)
    gateway.config_manager = config_manager
    gateway.daemon = HarbourDaemon()
    gateway.session_server = MCPServer("mcp-harbour")
    gateway._register_handlers()
    return gateway


def _set_request_identity(identity_name: str):
    request = SimpleNamespace(
        state=SimpleNamespace(harbour_identity=identity_name)
    )
    return request_ctx.set(
        RequestContext(
            request_id="test",
            meta=None,
            session=MagicMock(),
            lifespan_context=None,
            request=request,
        )
    )


async def get_tools(session_server, identity_name: str = "agent") -> list:
    """Call list_tools on a session server and return the tool list."""
    token = _set_request_identity(identity_name)
    try:
        result = await session_server.request_handlers[ListToolsRequest](MagicMock())
        return result.root.tools
    finally:
        request_ctx.reset(token)


async def call_tool(session_server, name: str, arguments: dict = None, identity_name: str = "agent"):
    """Call a tool on a session server and return the raw handler result."""
    token = _set_request_identity(identity_name)
    try:
        handler = session_server.request_handlers[CallToolRequest]
        request = MagicMock()
        request.params = CallToolRequestParams(name=name, arguments=arguments or {})
        return await handler(request)
    finally:
        request_ctx.reset(token)


def make_mock_process(server_name: str, tool_names: list[str]) -> ServerProcess:
    """Create a mock ServerProcess with fake tools. Shared across integration tests."""
    proc = MagicMock(spec=ServerProcess)
    proc.server_config = Server(name=server_name, command="echo")
    proc.session = MagicMock()

    mock_tools = [
        Tool(
            name=name,
            description=f"Mock {name}",
            inputSchema={"type": "object", "properties": {}},
        )
        for name in tool_names
    ]

    proc.list_tools = AsyncMock(return_value=ListToolsResult(tools=mock_tools))
    proc.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text=f"result from {server_name}")]
        )
    )
    proc.stop = AsyncMock()

    return proc


@pytest.fixture
def tmp_config_dir(tmp_path):
    config_dir = tmp_path / ".mcp-harbour"
    config_dir.mkdir()
    (config_dir / "policies").mkdir()
    return config_dir


@pytest.fixture
def config_manager(tmp_config_dir, monkeypatch):
    import mcp_harbour.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_config_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_config_dir / "config.json")
    monkeypatch.setattr(config_mod, "POLICIES_DIR", tmp_config_dir / "policies")
    monkeypatch.setattr(config_mod, "DEFAULT_HOST", "127.0.0.1")
    monkeypatch.setattr(config_mod, "DEFAULT_PORT", 0)

    from mcp_harbour.config import ConfigManager

    return ConfigManager()


@pytest.fixture
def sample_server(config_manager):
    return config_manager.add_server("test-server", command="echo")


@pytest.fixture
def sample_http_server(config_manager):
    return config_manager.add_server("test-http-server", url="http://localhost:3001/mcp")


