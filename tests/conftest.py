import pytest
from unittest.mock import AsyncMock, MagicMock
from mcp.types import Tool, ListToolsResult, CallToolResult, TextContent, ListToolsRequest, CallToolRequest, CallToolRequestParams

from mcp_harbour.models import (
    Server,
    ServerType,
    Identity,
    AgentPolicy,
    ToolPermission,
    ArgumentPolicy,
)
from mcp_harbour.process_manager import ServerProcess, HarbourDaemon
from mcp_harbour.gateway import HarbourGateway


def make_gateway(config_manager) -> HarbourGateway:
    """Create a gateway with mocked internals pointing at the test config."""
    gateway = HarbourGateway.__new__(HarbourGateway)
    gateway.config_manager = config_manager
    gateway.daemon = HarbourDaemon()
    return gateway


async def get_tools(session_server) -> list:
    """Call list_tools on a session server and return the tool list."""
    result = await session_server.request_handlers[ListToolsRequest](MagicMock())
    return result.root.tools


async def call_tool(session_server, name: str, arguments: dict = None):
    """Call a tool on a session server and return the raw handler result."""
    handler = session_server.request_handlers[CallToolRequest]
    request = MagicMock()
    request.params = CallToolRequestParams(name=name, arguments=arguments or {})
    return await handler(request)


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
def sample_server():
    return Server(
        name="filesystem",
        command="npx -y @modelcontextprotocol/server-filesystem",
        server_type=ServerType.stdio,
    )


@pytest.fixture
def sample_http_server():
    return Server(
        name="web-search",
        url="http://localhost:3001/mcp",
        server_type=ServerType.http,
    )


@pytest.fixture
def sample_identity():
    return Identity(name="test-agent", key_prefix="harbour_sk_test")


@pytest.fixture
def restrictive_policy():
    return AgentPolicy(
        identity_name="test-agent",
        permissions={
            "filesystem": [
                ToolPermission(
                    name="read_file",
                    policies=[
                        ArgumentPolicy(
                            arg_name="path",
                            match_type="glob",
                            pattern="/home/user/public/**",
                        )
                    ],
                )
            ]
        },
    )
