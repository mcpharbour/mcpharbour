"""
End-to-end tests that start a real daemon, connect via TCP,
and exercise the full MCP protocol through the harbour.

Uses @modelcontextprotocol/server-everything as the downstream MCP server.
Requires `npx` to be available.
"""

import json
import socket
import asyncio
import pytest
import pytest_asyncio

from mcp_harbour.gateway import HarbourGateway
from mcp_harbour.config import ConfigManager


# ─── Helpers ────────────────────────────────────────────────────────


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MCPClient:
    """Minimal MCP client that speaks JSON-RPC over TCP with harbour handshake."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self._req_id = 0

    async def connect(self, token: str) -> dict:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.writer.write(json.dumps({"auth": token}).encode() + b"\n")
        await self.writer.drain()
        ack_line = await asyncio.wait_for(self.reader.readline(), timeout=5)
        return json.loads(ack_line.decode())

    async def request(self, method: str, params: dict = None, timeout: float = 10) -> dict:
        self._req_id += 1
        msg = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.writer.write(json.dumps(msg).encode() + b"\n")
        await self.writer.drain()
        line = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
        return json.loads(line.decode())

    async def notify(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.writer.write(json.dumps(msg).encode() + b"\n")
        await self.writer.drain()

    async def initialize(self) -> dict:
        resp = await self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "0.1.0"},
        })
        await self.notify("notifications/initialized")
        return resp

    async def list_tools(self) -> list:
        resp = await self.request("tools/list", {})
        return resp.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict = None) -> dict:
        return await self.request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

    async def close(self):
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def e2e_dir(tmp_path):
    config_dir = tmp_path / "harbour-config"
    config_dir.mkdir()
    (config_dir / "policies").mkdir()
    return {"config_dir": config_dir}


@pytest.fixture
def e2e_config(e2e_dir, monkeypatch):
    import mcp_harbour.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", e2e_dir["config_dir"])
    monkeypatch.setattr(config_mod, "CONFIG_FILE", e2e_dir["config_dir"] / "config.json")
    monkeypatch.setattr(config_mod, "POLICIES_DIR", e2e_dir["config_dir"] / "policies")
    monkeypatch.setattr(config_mod, "DEFAULT_HOST", "127.0.0.1")
    monkeypatch.setattr(config_mod, "DEFAULT_PORT", 0)

    return ConfigManager()


@pytest.fixture
def e2e_port():
    return find_free_port()


@pytest.fixture
def e2e_setup(e2e_config, e2e_dir, e2e_port):
    # Dock server-everything using the service method
    e2e_config.add_server("everything", command="npx -y @modelcontextprotocol/server-everything")

    # Create identities using the service method (generates tokens, hashes, stores in keyring)
    full_token = e2e_config.add_identity("full-access")
    restricted_token = e2e_config.add_identity("restricted")
    no_policy_token = e2e_config.add_identity("no-policy")

    # Grant permissions using the service method
    e2e_config.grant_permission("full-access", "everything", tool="*")
    e2e_config.grant_permission("restricted", "everything", tool="echo")
    e2e_config.grant_permission("restricted", "everything", tool="get-sum",
                                arg_policies=["a=re:^\\d+$"])

    # no-policy identity gets nothing — default deny

    return {
        "config": e2e_config,
        "port": e2e_port,
        "tokens": {
            "full-access": full_token,
            "restricted": restricted_token,
            "no-policy": no_policy_token,
        },
    }


@pytest_asyncio.fixture
async def e2e_daemon(e2e_setup):
    gateway = HarbourGateway()
    port = e2e_setup["port"]

    daemon_task = asyncio.create_task(gateway.serve("127.0.0.1", port))

    for _ in range(50):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            await w.wait_closed()
            break
        except ConnectionRefusedError:
            await asyncio.sleep(0.1)
    else:
        daemon_task.cancel()
        pytest.fail("Daemon did not start in time")

    yield {"port": port, **e2e_setup}

    daemon_task.cancel()
    try:
        await daemon_task
    except (asyncio.CancelledError, Exception):
        pass


# ─── Handshake Tests ────────────────────────────────────────────────


class TestE2EHandshake:
    @pytest.mark.asyncio
    async def test_valid_token_connects(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            ack = await client.connect(e2e_daemon["tokens"]["full-access"])
            assert ack["status"] == "ok"
            assert ack["identity"] == "full-access"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            ack = await client.connect("harbour_sk_bogus_token_that_doesnt_exist")
            assert ack["error"] == "Invalid token"
        finally:
            await client.close()


# ─── Initialize Tests ───────────────────────────────────────────────


class TestE2EInitialize:
    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["full-access"])
            resp = await client.initialize()
            assert resp["result"]["serverInfo"]["name"] == "mcp-harbour"
            assert "tools" in resp["result"]["capabilities"]
        finally:
            await client.close()


# ─── List Tools Tests ───────────────────────────────────────────────


class TestE2EListTools:
    @pytest.mark.asyncio
    async def test_full_access_sees_all_tools(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["full-access"])
            await client.initialize()
            tools = await client.list_tools()
            tool_names = [t["name"] for t in tools]

            assert len(tools) > 5
            assert "echo" in tool_names
            assert "get-sum" in tool_names
            assert "get-tiny-image" in tool_names
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_restricted_sees_filtered_tools(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["restricted"])
            await client.initialize()
            tools = await client.list_tools()
            tool_names = [t["name"] for t in tools]

            assert "echo" in tool_names
            assert "get-sum" in tool_names
            assert "get-tiny-image" not in tool_names
            assert "get-env" not in tool_names
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_no_policy_sees_no_tools(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["no-policy"])
            await client.initialize()
            tools = await client.list_tools()
            assert len(tools) == 0
        finally:
            await client.close()


# ─── Tool Call Tests ────────────────────────────────────────────────


class TestE2ECallTool:
    @pytest.mark.asyncio
    async def test_echo_returns_input(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["full-access"])
            await client.initialize()

            resp = await client.call_tool("echo", {"message": "hello harbour"})
            result_str = json.dumps(resp["result"])
            assert "hello harbour" in result_str
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_sum_returns_correct_result(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["full-access"])
            await client.initialize()

            resp = await client.call_tool("get-sum", {"a": 7, "b": 3})
            result_str = json.dumps(resp["result"])
            assert "10" in result_str
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_restricted_can_call_echo(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["restricted"])
            await client.initialize()

            resp = await client.call_tool("echo", {"message": "allowed"})
            result_str = json.dumps(resp["result"])
            assert "allowed" in result_str
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_restricted_denied_on_unpermitted_tool(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["restricted"])
            await client.initialize()

            resp = await client.call_tool("get-env")
            result = resp["result"]
            assert result.get("isError") is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_restricted_denied_on_argument_policy(self, e2e_daemon):
        """get-sum with non-numeric 'a' should fail the regex policy."""
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["restricted"])
            await client.initialize()

            resp = await client.call_tool("get-sum", {"a": "not_a_number", "b": 3})
            result = resp["result"]
            assert result.get("isError") is True
        finally:
            await client.close()


# ─── Multi-Session Tests ────────────────────────────────────────────


class TestE2EMultipleSessions:
    @pytest.mark.asyncio
    async def test_two_clients_get_isolated_sessions(self, e2e_daemon):
        client_a = MCPClient("127.0.0.1", e2e_daemon["port"])
        client_b = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client_a.connect(e2e_daemon["tokens"]["full-access"])
            await client_b.connect(e2e_daemon["tokens"]["restricted"])

            await client_a.initialize()
            await client_b.initialize()

            tools_a = await client_a.list_tools()
            tools_b = await client_b.list_tools()

            assert len(tools_a) > len(tools_b)
            names_a = {t["name"] for t in tools_a}
            names_b = {t["name"] for t in tools_b}
            assert "get-env" in names_a
            assert "get-env" not in names_b
        finally:
            await client_a.close()
            await client_b.close()
