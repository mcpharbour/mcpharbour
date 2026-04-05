"""Tests for the ConfigManager."""

import sys
import pytest
from mcp_harbour.models import Server, Identity, AgentPolicy, ToolPermission, ServerType


class TestConfigManagerServers:
    def test_add_server(self, config_manager):
        config_manager.add_server("filesystem", command="echo hello")
        server = config_manager.get_server("filesystem")
        assert server is not None
        assert server.command == "echo hello"

    def test_add_http_server(self, config_manager):
        config_manager.add_server("remote", url="http://localhost:8000/mcp")
        server = config_manager.get_server("remote")
        assert server.url == "http://localhost:8000/mcp"
        assert server.server_type.value == "http"

    def test_add_server_rejects_both(self, config_manager):
        with pytest.raises(ValueError):
            config_manager.add_server("bad", command="echo", url="http://x")

    def test_add_server_rejects_neither(self, config_manager):
        with pytest.raises(ValueError):
            config_manager.add_server("bad")

    def test_add_duplicate_server_raises(self, config_manager):
        config_manager.add_server("filesystem", command="echo")
        with pytest.raises(ValueError, match="already exists"):
            config_manager.add_server("filesystem", command="echo2")

    def test_list_servers(self, config_manager):
        config_manager.add_server("filesystem", command="echo")
        config_manager.add_server("remote", url="http://localhost/mcp")
        servers = config_manager.list_servers()
        assert len(servers) == 2
        assert {s.name for s in servers} == {"filesystem", "remote"}

    def test_remove_server(self, config_manager):
        config_manager.add_server("filesystem", command="echo")
        config_manager.remove_server("filesystem")
        assert config_manager.get_server("filesystem") is None

    def test_remove_nonexistent_raises(self, config_manager):
        with pytest.raises(ValueError):
            config_manager.remove_server("doesnt-exist")

    def test_get_nonexistent_returns_none(self, config_manager):
        assert config_manager.get_server("nope") is None

    def test_persistence(self, config_manager, tmp_config_dir, monkeypatch):
        config_manager.add_server("filesystem", command="echo hello")

        import mcp_harbour.config as config_mod

        cm2 = config_mod.ConfigManager()
        assert cm2.get_server("filesystem") is not None
        assert cm2.get_server("filesystem").command == "echo hello"


class TestConfigManagerIdentities:
    def test_add_identity(self, config_manager):
        config_manager.add_identity("test-agent")
        assert config_manager.get_identity("test-agent") is not None
        assert config_manager.get_identity("test-agent").name == "test-agent"

    def test_add_duplicate_identity_raises(self, config_manager):
        config_manager.add_identity("test-agent")
        with pytest.raises(ValueError, match="already exists"):
            config_manager.add_identity("test-agent")

    def test_get_nonexistent_identity(self, config_manager):
        assert config_manager.get_identity("ghost") is None

    def test_remove_identity_cascades_to_policy(self, config_manager):
        config_manager.add_identity("test-agent")
        config_manager.grant_permission("test-agent", "filesystem", tool="read_file",
                                        arg_policies=["path=/home/user/public/**"])

        assert config_manager.get_identity("test-agent") is not None
        assert config_manager.load_policy("test-agent") is not None

        config_manager.remove_identity("test-agent")

        assert config_manager.get_identity("test-agent") is None
        assert config_manager.load_policy("test-agent") is None

    def test_remove_nonexistent_identity_raises(self, config_manager):
        with pytest.raises(ValueError):
            config_manager.remove_identity("doesnt-exist")


class TestConfigManagerPolicies:
    def test_grant_permission_creates_policy(self, config_manager):
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "filesystem", tool="read_file")

        policy = config_manager.load_policy("agent")
        assert policy is not None
        assert policy.permissions["filesystem"][0].name == "read_file"

    def test_grant_permission_with_arg_policies(self, config_manager):
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "filesystem", tool="read_file",
                                        arg_policies=["path=/home/user/**"])

        policy = config_manager.load_policy("agent")
        arg = policy.permissions["filesystem"][0].policies[0]
        assert arg.arg_name == "path"
        assert arg.match_type == "glob"
        assert arg.pattern == "/home/user/**"

    def test_grant_permission_with_regex(self, config_manager):
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "db", tool="query",
                                        arg_policies=["sql=re:^SELECT.*"])

        policy = config_manager.load_policy("agent")
        arg = policy.permissions["db"][0].policies[0]
        assert arg.match_type == "regex"
        assert arg.pattern == "^SELECT.*"

    def test_grant_permission_invalid_format_raises(self, config_manager):
        config_manager.add_identity("agent")
        with pytest.raises(ValueError, match="Invalid argument policy"):
            config_manager.grant_permission("agent", "fs", arg_policies=["no_equals_sign"])

    def test_grant_permission_identity_not_found_raises(self, config_manager):
        with pytest.raises(ValueError, match="not found"):
            config_manager.grant_permission("ghost", "filesystem")

    def test_load_nonexistent_policy(self, config_manager):
        assert config_manager.load_policy("nonexistent") is None

    def test_grant_permission_is_additive(self, config_manager):
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "filesystem", tool="read_file")
        config_manager.grant_permission("agent", "filesystem", tool="write_file")

        policy = config_manager.load_policy("agent")
        tool_names = [t.name for t in policy.permissions["filesystem"]]
        assert "read_file" in tool_names
        assert "write_file" in tool_names

    def test_grant_permission_additive_across_servers(self, config_manager):
        config_manager.add_identity("agent")
        config_manager.grant_permission("agent", "filesystem", tool="*")
        config_manager.grant_permission("agent", "git", tool="git_status")

        policy = config_manager.load_policy("agent")
        assert "filesystem" in policy.permissions
        assert "git" in policy.permissions


# ─── Platform Config Dir ───────────────────────────────────────────


class TestConfigPlatformDir:
    # _get_config_dir() reads sys.platform at call time, not import time,
    # so monkeypatching sys.platform works here.

    def test_unix_config_dir(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        from mcp_harbour.config import _get_config_dir
        assert ".mcp-harbour" in str(_get_config_dir())

    def test_windows_config_dir(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", "/fake/appdata")
        from mcp_harbour.config import _get_config_dir
        path = _get_config_dir()
        assert "mcp-harbour" in str(path)
        assert "appdata" in str(path).lower()
