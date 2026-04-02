"""Tests for process_manager command parsing and HarbourDaemon."""

import shlex
import pytest
from mcp_harbour.models import Server
from mcp_harbour.process_manager import HarbourDaemon
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
        assert HarbourDaemon().shared_processes == {}

    def test_get_shared_nonexistent(self):
        assert HarbourDaemon().get_shared_process("nope") is None

    @pytest.mark.asyncio
    async def test_stop_all_shared(self):
        daemon = HarbourDaemon()
        proc = make_mock_process("test", ["tool"])
        daemon.shared_processes["test"] = proc

        await daemon.stop_all_shared()
        proc.stop.assert_called_once()
        assert daemon.shared_processes == {}
