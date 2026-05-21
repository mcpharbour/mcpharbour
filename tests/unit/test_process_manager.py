"""Tests for process_manager command parsing and HarbourDaemon."""

import shlex
from unittest.mock import AsyncMock, patch

import pytest
from mcp_harbour.models import Server
from mcp_harbour.process_manager import HarbourDaemon, ServerHealth
from tests.conftest import make_mock_process


class TestCommandParsing:
    """Verify that commands are split correctly before being passed to StdioServerParameters."""

    def _get_parsed_args(self, command: str):
        """Simulate what ServerProcess.start() does to build the final command."""
        parts = shlex.split(command)
        return parts[0], parts[1:]

    def test_simple_command(self):
        exe, args = self._get_parsed_args("echo hello")
        assert exe == "echo"
        assert args == ["hello"]

    def test_command_with_multiple_args(self):
        exe, args = self._get_parsed_args("npx -y @modelcontextprotocol/server-filesystem /home/user")
        assert exe == "npx"
        assert args == ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"]

    def test_command_with_quoted_path(self):
        exe, args = self._get_parsed_args('npx -y @mcp/server "/home/user/my projects"')
        assert exe == "npx"
        assert args == ["-y", "@mcp/server", "/home/user/my projects"]

    def test_single_word_command(self):
        exe, args = self._get_parsed_args("cat")
        assert exe == "cat"
        assert args == []

    def test_uvx_command(self):
        exe, args = self._get_parsed_args("uvx mcp-server-bash")
        assert exe == "uvx"
        assert args == ["mcp-server-bash"]


class TestHarbourDaemon:
    def test_init_empty(self):
        daemon = HarbourDaemon()
        assert daemon.shared_processes == {}
        assert daemon.server_health == {}

    def test_get_shared_nonexistent(self):
        assert HarbourDaemon().get_shared_process("nope") is None

    def test_get_server_health_nonexistent(self):
        assert HarbourDaemon().get_server_health("nope") is None

    @pytest.mark.asyncio
    async def test_start_shared_server_records_healthy_state(self):
        daemon = HarbourDaemon()
        server = Server(name="test", command="echo")

        with patch("mcp_harbour.process_manager.ServerProcess.start", new=AsyncMock()) as start:
            await daemon.start_shared_server(server)

        start.assert_awaited_once()
        assert daemon.get_shared_process("test") is not None
        health = daemon.get_server_health("test")
        assert health is not None
        assert health.state == "healthy"
        assert health.error is None

    @pytest.mark.asyncio
    async def test_start_shared_server_records_failed_state(self):
        daemon = HarbourDaemon()
        server = Server(name="broken", command="echo")

        with patch(
            "mcp_harbour.process_manager.ServerProcess.start",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ):
            with pytest.raises(RuntimeError, match="connection refused"):
                await daemon.start_shared_server(server)

        assert daemon.get_shared_process("broken") is None
        health = daemon.get_server_health("broken")
        assert health is not None
        assert health.state == "failed"
        assert health.error == "connection refused"

    @pytest.mark.asyncio
    async def test_stop_shared_server_clears_health_state(self):
        daemon = HarbourDaemon()
        proc = make_mock_process("test", ["tool"])
        daemon.shared_processes["test"] = proc
        daemon.server_health["test"] = ServerHealth(state="healthy")

        await daemon.stop_shared_server("test")

        proc.stop.assert_called_once()
        assert daemon.get_shared_process("test") is None
        assert daemon.get_server_health("test") is None

    @pytest.mark.asyncio
    async def test_stop_all_shared(self):
        daemon = HarbourDaemon()
        proc = make_mock_process("test", ["tool"])
        daemon.shared_processes["test"] = proc
        daemon.server_health["test"] = ServerHealth(state="healthy")

        await daemon.stop_all_shared()
        proc.stop.assert_called_once()
        assert daemon.shared_processes == {}
        assert daemon.server_health == {}

    @pytest.mark.asyncio
    async def test_stop_shared_server_clears_failed_health_without_process(self):
        daemon = HarbourDaemon()
        daemon.server_health["broken"] = ServerHealth(state="failed", error="boom")

        await daemon.stop_shared_server("broken")

        assert daemon.get_server_health("broken") is None
