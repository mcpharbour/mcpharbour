"""Tests for the ConfigManager."""

import sys
import pytest
from mcp_harbour.models import Server, Identity, AgentPolicy, ToolPermission, ServerType


class TestConfigManagerServers:
    def test_add_server(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        assert config_manager.get_server("filesystem") == sample_server

    def test_list_servers(self, config_manager, sample_server, sample_http_server):
        config_manager.add_server(sample_server)
        config_manager.add_server(sample_http_server)
        servers = config_manager.list_servers()
        assert len(servers) == 2
        names = {s.name for s in servers}
        assert names == {"filesystem", "web-search"}

    def test_remove_server(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        config_manager.remove_server("filesystem")
        assert config_manager.get_server("filesystem") is None

    def test_remove_nonexistent_is_safe(self, config_manager):
        config_manager.remove_server("doesnt-exist")

    def test_get_nonexistent_returns_none(self, config_manager):
        assert config_manager.get_server("nope") is None

    def test_persistence(
        self, config_manager, sample_server, tmp_config_dir, monkeypatch
    ):
        config_manager.add_server(sample_server)

        import mcp_harbour.config as config_mod

        cm2 = config_mod.ConfigManager()
        assert cm2.get_server("filesystem") is not None
        assert cm2.get_server("filesystem").command == sample_server.command


class TestConfigManagerIdentities:
    def test_add_identity(self, config_manager, sample_identity):
        config_manager.add_identity(sample_identity)
        assert config_manager.get_identity("test-agent") == sample_identity

    def test_get_nonexistent_identity(self, config_manager):
        assert config_manager.get_identity("ghost") is None

    def test_remove_identity(
        self, config_manager, sample_identity, restrictive_policy
    ):
        config_manager.add_identity(sample_identity)
        config_manager.save_policy(restrictive_policy)

        assert config_manager.get_identity("test-agent") is not None
        assert config_manager.load_policy("test-agent") is not None

        config_manager.remove_identity("test-agent")

        assert config_manager.get_identity("test-agent") is None
        assert config_manager.load_policy("test-agent") is None

    def test_remove_nonexistent_identity_is_safe(self, config_manager):
        config_manager.remove_identity("doesnt-exist")


class TestConfigManagerPolicies:
    def test_create_policy(self, config_manager):
        policy = config_manager.create_policy("new-agent")
        assert policy.identity_name == "new-agent"
        assert policy.permissions == {}

    def test_save_and_load_policy(self, config_manager, restrictive_policy):
        config_manager.save_policy(restrictive_policy)
        loaded = config_manager.load_policy("test-agent")
        assert loaded is not None
        assert loaded.identity_name == "test-agent"
        assert "filesystem" in loaded.permissions
        assert loaded.permissions["filesystem"][0].name == "read_file"

    def test_load_nonexistent_policy(self, config_manager):
        assert config_manager.load_policy("nonexistent") is None

    def test_policy_with_policies(self, config_manager, restrictive_policy):
        config_manager.save_policy(restrictive_policy)
        loaded = config_manager.load_policy("test-agent")
        policy = loaded.permissions["filesystem"][0].policies[0]
        assert policy.arg_name == "path"
        assert policy.match_type == "glob"
        assert policy.pattern == "/home/user/public/**"

    def test_overwrite_policy(self, config_manager):
        p1 = AgentPolicy(
            identity_name="agent",
            permissions={"fs": [ToolPermission(name="read_file")]},
        )
        config_manager.save_policy(p1)

        p2 = AgentPolicy(
            identity_name="agent",
            permissions={"fs": [ToolPermission(name="*")]},
        )
        config_manager.save_policy(p2)

        loaded = config_manager.load_policy("agent")
        assert loaded.permissions["fs"][0].name == "*"

    def test_additive_append(self, config_manager):
        p = AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="read_file")]},
        )
        config_manager.save_policy(p)

        loaded = config_manager.load_policy("agent")
        loaded.permissions["filesystem"].append(ToolPermission(name="write_file"))
        config_manager.save_policy(loaded)

        final = config_manager.load_policy("agent")
        tool_names = [t.name for t in final.permissions["filesystem"]]
        assert "read_file" in tool_names
        assert "write_file" in tool_names

    def test_additive_across_servers(self, config_manager):
        p = AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="*")]},
        )
        config_manager.save_policy(p)

        loaded = config_manager.load_policy("agent")
        loaded.permissions["git"] = [ToolPermission(name="git_status")]
        config_manager.save_policy(loaded)

        final = config_manager.load_policy("agent")
        assert "filesystem" in final.permissions
        assert "git" in final.permissions


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
