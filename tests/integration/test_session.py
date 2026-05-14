"""Tests for shared gateway tool listing, tool calling, and process lifecycle."""

from unittest.mock import AsyncMock

import pytest

from tests.conftest import call_tool, get_tools, make_gateway, make_mock_process


def create_admin_policy(config_manager, servers=None):
    if servers is None:
        servers = ["test-server"]
    for s in servers:
        config_manager.grant_permission("admin", s, tool="*")


class TestSharedProcesses:
    @pytest.mark.asyncio
    async def test_stdio_server_starts_as_shared_process(self, config_manager, sample_server):
        gateway = make_gateway(config_manager)
        gateway.daemon.start_shared_server = AsyncMock()

        await gateway.start_shared_processes()

        gateway.daemon.start_shared_server.assert_awaited_once_with(sample_server)

    @pytest.mark.asyncio
    async def test_http_server_starts_as_shared_process(self, config_manager, sample_http_server):
        gateway = make_gateway(config_manager)
        gateway.daemon.start_shared_server = AsyncMock()

        await gateway.start_shared_processes()

        gateway.daemon.start_shared_server.assert_awaited_once_with(sample_http_server)


class TestToolDiscovery:
    @pytest.mark.asyncio
    async def test_single_server(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "test-server", tool="*")

        gateway = make_gateway(config_manager)
        gateway.daemon.shared_processes["test-server"] = make_mock_process(
            "test-server", ["read_file", "write_file", "list_dir"]
        )

        tools = await get_tools(gateway.session_server)

        assert len(tools) == 3
        assert {t.name for t in tools} == {"read_file", "write_file", "list_dir"}

    @pytest.mark.asyncio
    async def test_multiple_servers(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_server("git", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "test-server", tool="*")
        config_manager.grant_permission("agent", "git", tool="*")

        gateway = make_gateway(config_manager)
        gateway.daemon.shared_processes["test-server"] = make_mock_process(
            "test-server", ["read_file", "write_file"]
        )
        gateway.daemon.shared_processes["git"] = make_mock_process(
            "git", ["git_status", "git_log"]
        )

        tools = await get_tools(gateway.session_server)

        assert {t.name for t in tools} == {"read_file", "write_file", "git_status", "git_log"}

    @pytest.mark.asyncio
    async def test_filtered_by_exact_tool_name(self, config_manager, sample_server):
        config_manager.add_identity("reader")
        config_manager.grant_permission("reader", "test-server", tool="read_file")

        gateway = make_gateway(config_manager)
        gateway.daemon.shared_processes["test-server"] = make_mock_process(
            "test-server", ["read_file", "write_file", "delete_file"]
        )

        tool_names = [t.name for t in await get_tools(gateway.session_server, "reader")]

        assert "read_file" in tool_names
        assert "write_file" not in tool_names

    @pytest.mark.asyncio
    async def test_filtered_by_glob(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "test-server", tool="read_*")

        gateway = make_gateway(config_manager)
        gateway.daemon.shared_processes["test-server"] = make_mock_process(
            "test-server", ["read_file", "read_dir", "write_file", "delete_file"]
        )

        assert {t.name for t in await get_tools(gateway.session_server)} == {"read_file", "read_dir"}

    @pytest.mark.asyncio
    async def test_server_not_in_policy_skipped(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_server("bash", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "test-server", tool="*")

        gateway = make_gateway(config_manager)
        test_proc = make_mock_process("test-server", ["read_file"])
        bash_proc = make_mock_process("bash", ["run_command"])
        gateway.daemon.shared_processes["test-server"] = test_proc
        gateway.daemon.shared_processes["bash"] = bash_proc

        tools = await get_tools(gateway.session_server)

        assert {t.name for t in tools} == {"read_file"}
        bash_proc.list_tools.assert_not_awaited()


class TestDefaultDeny:
    @pytest.mark.asyncio
    async def test_no_policy(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_identity("unknown-agent")

        gateway = make_gateway(config_manager)
        gateway.daemon.shared_processes["test-server"] = make_mock_process(
            "test-server", ["read_file"]
        )

        assert len(await get_tools(gateway.session_server, "unknown-agent")) == 0

    @pytest.mark.asyncio
    async def test_empty_policy(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_identity("empty-agent")
        config_manager.create_policy("empty-agent")

        gateway = make_gateway(config_manager)
        proc = make_mock_process("test-server", ["read_file"])
        gateway.daemon.shared_processes["test-server"] = proc

        assert len(await get_tools(gateway.session_server, "empty-agent")) == 0
        proc.list_tools.assert_not_awaited()


class TestToolCalls:
    @pytest.mark.asyncio
    async def test_routes_to_correct_server(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_server("git", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "test-server", tool="*")
        config_manager.grant_permission("agent", "git", tool="*")

        gateway = make_gateway(config_manager)
        fs_proc = make_mock_process("test-server", ["read_file"])
        git_proc = make_mock_process("git", ["git_status"])
        gateway.daemon.shared_processes["test-server"] = fs_proc
        gateway.daemon.shared_processes["git"] = git_proc

        await call_tool(gateway.session_server, "read_file", {"path": "/tmp/test"})
        fs_proc.call_tool.assert_called_once_with("read_file", {"path": "/tmp/test"})
        git_proc.call_tool.assert_not_called()

        await call_tool(gateway.session_server, "git_status")
        git_proc.call_tool.assert_called_once_with("git_status", {})

    @pytest.mark.asyncio
    async def test_argument_policy_allowed(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission(
            "agent", "test-server", tool="read_file", arg_policies=["path=/home/user/**"]
        )

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("test-server", ["read_file"])
        gateway.daemon.shared_processes["test-server"] = mock_proc

        await call_tool(gateway.session_server, "read_file", {"path": "/home/user/project/main.py"})
        mock_proc.call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_argument_policy_denied(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission(
            "agent", "test-server", tool="read_file", arg_policies=["path=/home/user/**"]
        )

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("test-server", ["read_file"])
        gateway.daemon.shared_processes["test-server"] = mock_proc

        result = await call_tool(gateway.session_server, "read_file", {"path": "/etc/shadow"})

        assert result.root.isError is True
        mock_proc.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_denied_tool_returns_error(self, config_manager, sample_server):
        config_manager.add_identity("readonly")
        config_manager.grant_permission("readonly", "test-server", tool="read_file")

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("test-server", ["read_file", "write_file"])
        gateway.daemon.shared_processes["test-server"] = mock_proc

        result = await call_tool(
            gateway.session_server,
            "write_file",
            {"path": "/etc/passwd", "content": "x"},
            "readonly",
        )

        mock_proc.call_tool.assert_not_called()
        assert result.root.isError is True
        assert "not allowed" in result.root.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_unknown_tool(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "test-server", tool="read_file")

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("test-server", ["read_file"])
        gateway.daemon.shared_processes["test-server"] = mock_proc

        result = await call_tool(gateway.session_server, "nonexistent_tool")

        assert result.root.isError is True
        mock_proc.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_unavailable_server(self, config_manager):
        config_manager.add_server("test-server", command="echo")
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "test-server", tool="*")

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("test-server", ["read_file"])
        mock_proc.session = None
        gateway.daemon.shared_processes["test-server"] = mock_proc

        result = await call_tool(gateway.session_server, "read_file")

        assert result.root.isError is True


class TestProcessLifecycle:
    @pytest.mark.asyncio
    async def test_shared_processes_can_be_stopped(self, config_manager, sample_server):
        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("test-server", ["read_file"])
        gateway.daemon.shared_processes["test-server"] = mock_proc

        await gateway.daemon.stop_all_shared()

        mock_proc.stop.assert_awaited_once()
        assert "test-server" not in gateway.daemon.shared_processes

    @pytest.mark.asyncio
    async def test_multiple_identities_reuse_same_process(self, config_manager, sample_server):
        config_manager.add_identity("admin")
        config_manager.add_identity("reader")
        config_manager.grant_permission("admin", "test-server", tool="*")
        config_manager.grant_permission("reader", "test-server", tool="read_file")

        gateway = make_gateway(config_manager)
        proc = make_mock_process("test-server", ["read_file", "write_file"])
        gateway.daemon.shared_processes["test-server"] = proc

        await get_tools(gateway.session_server, "admin")
        await get_tools(gateway.session_server, "reader")

        assert gateway.daemon.shared_processes["test-server"] is proc
        assert proc.list_tools.await_count == 2
