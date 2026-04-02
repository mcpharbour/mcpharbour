"""Tests for gateway session creation, tool listing, tool calling, and process lifecycle."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from mcp_harbour.models import Server, AgentPolicy, ToolPermission, ArgumentPolicy
from tests.conftest import make_mock_process, make_gateway, get_tools, call_tool


def create_admin_policy(config_manager, servers=None):
    if servers is None:
        servers = ["filesystem"]
    perms = {s: [ToolPermission(name="*")] for s in servers}
    config_manager.save_policy(AgentPolicy(identity_name="admin", permissions=perms))


# ─── Session Creation ────────────────────────────────────────────────


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_stdio_server_spawns_per_client(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        create_admin_policy(config_manager)

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("filesystem", ["read_file", "write_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("admin")

        gateway.daemon.spawn_stdio_instance.assert_called_once()
        assert len(owned) == 1
        assert owned[0] is mock_proc

    @pytest.mark.asyncio
    async def test_http_server_reuses_shared(self, config_manager, sample_http_server):
        config_manager.add_server(sample_http_server)
        config_manager.save_policy(
            AgentPolicy(identity_name="admin", permissions={"web-search": [ToolPermission(name="*")]})
        )

        gateway = make_gateway(config_manager)
        shared_proc = make_mock_process("web-search", ["search"])
        gateway.daemon.shared_processes["web-search"] = shared_proc

        _, owned = await gateway.create_session("admin")
        assert len(owned) == 0


# ─── Tool Discovery ─────────────────────────────────────────────────


class TestToolDiscovery:
    @pytest.mark.asyncio
    async def test_single_server(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.save_policy(
            AgentPolicy(identity_name="agent", permissions={"filesystem": [ToolPermission(name="*")]})
        )

        gateway = make_gateway(config_manager)
        gateway.daemon.spawn_stdio_instance = AsyncMock(
            return_value=make_mock_process("filesystem", ["read_file", "write_file", "list_dir"])
        )

        session_server, _ = await gateway.create_session("agent")
        tools = await get_tools(session_server)

        assert len(tools) == 3
        assert {t.name for t in tools} == {"read_file", "write_file", "list_dir"}

    @pytest.mark.asyncio
    async def test_multiple_servers(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.add_server(Server(name="git", command="echo"))
        config_manager.save_policy(AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="*")], "git": [ToolPermission(name="*")]},
        ))

        gateway = make_gateway(config_manager)
        gateway.daemon.spawn_stdio_instance = AsyncMock(side_effect=[
            make_mock_process("filesystem", ["read_file", "write_file"]),
            make_mock_process("git", ["git_status", "git_log"]),
        ])

        session_server, _ = await gateway.create_session("agent")
        tools = await get_tools(session_server)

        assert {t.name for t in tools} == {"read_file", "write_file", "git_status", "git_log"}

    @pytest.mark.asyncio
    async def test_filtered_by_exact_tool_name(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        config_manager.save_policy(
            AgentPolicy(identity_name="reader", permissions={"filesystem": [ToolPermission(name="read_file")]})
        )

        gateway = make_gateway(config_manager)
        gateway.daemon.spawn_stdio_instance = AsyncMock(
            return_value=make_mock_process("filesystem", ["read_file", "write_file", "delete_file"])
        )

        session_server, _ = await gateway.create_session("reader")
        tool_names = [t.name for t in await get_tools(session_server)]

        assert "read_file" in tool_names
        assert "write_file" not in tool_names

    @pytest.mark.asyncio
    async def test_filtered_by_glob(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.save_policy(
            AgentPolicy(identity_name="agent", permissions={"filesystem": [ToolPermission(name="read_*")]})
        )

        gateway = make_gateway(config_manager)
        gateway.daemon.spawn_stdio_instance = AsyncMock(
            return_value=make_mock_process("filesystem", ["read_file", "read_dir", "write_file", "delete_file"])
        )

        session_server, _ = await gateway.create_session("agent")
        assert {t.name for t in await get_tools(session_server)} == {"read_file", "read_dir"}

    @pytest.mark.asyncio
    async def test_server_not_in_policy_skipped(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.add_server(Server(name="bash", command="echo"))
        config_manager.save_policy(
            AgentPolicy(identity_name="agent", permissions={"filesystem": [ToolPermission(name="*")]})
        )

        gateway = make_gateway(config_manager)
        gateway.daemon.spawn_stdio_instance = AsyncMock(
            return_value=make_mock_process("filesystem", ["read_file"])
        )

        await gateway.create_session("agent")
        assert gateway.daemon.spawn_stdio_instance.call_count == 1


# ─── Default Deny ────────────────────────────────────────────────────


class TestDefaultDeny:
    @pytest.mark.asyncio
    async def test_no_policy(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        gateway = make_gateway(config_manager)
        gateway.daemon.spawn_stdio_instance = AsyncMock()

        session_server, _ = await gateway.create_session("unknown-agent")
        assert len(await get_tools(session_server)) == 0

    @pytest.mark.asyncio
    async def test_empty_policy(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.save_policy(AgentPolicy(identity_name="empty-agent", permissions={}))

        gateway = make_gateway(config_manager)
        gateway.daemon.spawn_stdio_instance = AsyncMock()

        session_server, _ = await gateway.create_session("empty-agent")
        assert len(await get_tools(session_server)) == 0
        gateway.daemon.spawn_stdio_instance.assert_not_called()


# ─── Tool Calls ──────────────────────────────────────────────────────


class TestToolCalls:
    @pytest.mark.asyncio
    async def test_routes_to_correct_server(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.add_server(Server(name="git", command="echo"))
        config_manager.save_policy(AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="*")], "git": [ToolPermission(name="*")]},
        ))

        gateway = make_gateway(config_manager)
        fs_proc = make_mock_process("filesystem", ["read_file"])
        git_proc = make_mock_process("git", ["git_status"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(side_effect=[fs_proc, git_proc])

        session_server, _ = await gateway.create_session("agent")

        await call_tool(session_server, "read_file", {"path": "/tmp/test"})
        fs_proc.call_tool.assert_called_once_with("read_file", {"path": "/tmp/test"})
        git_proc.call_tool.assert_not_called()

        await call_tool(session_server, "git_status")
        git_proc.call_tool.assert_called_once_with("git_status", {})

    @pytest.mark.asyncio
    async def test_argument_policy_allowed(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.save_policy(AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [
                ToolPermission(name="read_file", policies=[
                    ArgumentPolicy(arg_name="path", match_type="glob", pattern="/home/user/**")
                ])
            ]},
        ))

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, _ = await gateway.create_session("agent")
        await call_tool(session_server, "read_file", {"path": "/home/user/project/main.py"})
        mock_proc.call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_argument_policy_denied(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.save_policy(AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [
                ToolPermission(name="read_file", policies=[
                    ArgumentPolicy(arg_name="path", match_type="glob", pattern="/home/user/**")
                ])
            ]},
        ))

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, _ = await gateway.create_session("agent")
        result = await call_tool(session_server, "read_file", {"path": "/etc/shadow"})

        assert result.root.isError is True
        mock_proc.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_denied_tool_returns_error(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        config_manager.save_policy(
            AgentPolicy(identity_name="readonly", permissions={"filesystem": [ToolPermission(name="read_file")]})
        )

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("filesystem", ["read_file", "write_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, _ = await gateway.create_session("readonly")
        result = await call_tool(session_server, "write_file", {"path": "/etc/passwd", "content": "x"})

        mock_proc.call_tool.assert_not_called()
        assert result.root.isError is True
        assert "not allowed" in result.root.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_unknown_tool(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.save_policy(
            AgentPolicy(identity_name="agent", permissions={"filesystem": [ToolPermission(name="read_file")]})
        )

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, _ = await gateway.create_session("agent")
        result = await call_tool(session_server, "nonexistent_tool")

        assert result.root.isError is True
        mock_proc.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_unavailable_server(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.save_policy(
            AgentPolicy(identity_name="agent", permissions={"filesystem": [ToolPermission(name="*")]})
        )

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("filesystem", ["read_file"])
        mock_proc.session = None
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, _ = await gateway.create_session("agent")
        result = await call_tool(session_server, "read_file")

        assert result.root.isError is True


# ─── Process Lifecycle ───────────────────────────────────────────────


class TestProcessLifecycle:
    @pytest.mark.asyncio
    async def test_owned_processes_can_be_stopped(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        create_admin_policy(config_manager)

        gateway = make_gateway(config_manager)
        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        _, owned = await gateway.create_session("admin")
        for proc in owned:
            await proc.stop()
        mock_proc.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_separate_processes_per_session(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        create_admin_policy(config_manager)

        gateway = make_gateway(config_manager)
        proc_a = make_mock_process("filesystem", ["read_file"])
        proc_b = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(side_effect=[proc_a, proc_b])

        _, owned_a = await gateway.create_session("admin")
        _, owned_b = await gateway.create_session("admin")

        assert owned_a[0] is not owned_b[0]
        assert gateway.daemon.spawn_stdio_instance.call_count == 2
