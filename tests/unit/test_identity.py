"""Tests for identity resolution from tokens."""

import pytest
from unittest.mock import patch

from tests.conftest import make_gateway


class TestResolveIdentityFromToken:
    def test_resolves_correct_identity(self, config_manager):
        token_a = config_manager.add_identity("agent-a")
        token_b = config_manager.add_identity("agent-b")

        gateway = make_gateway(config_manager)
        assert gateway._resolve_identity_from_token(token_a) == "agent-a"
        assert gateway._resolve_identity_from_token(token_b) == "agent-b"

    def test_returns_none_for_unknown_token(self, config_manager):
        config_manager.add_identity("agent-a")

        gateway = make_gateway(config_manager)
        assert gateway._resolve_identity_from_token("harbour_sk_wrong_token_here") is None

    def test_returns_none_when_no_identities(self, config_manager):
        gateway = make_gateway(config_manager)
        assert gateway._resolve_identity_from_token("harbour_sk_any") is None

    def test_handles_keyring_error_gracefully(self, config_manager):
        config_manager.add_identity("agent")
        gateway = make_gateway(config_manager)

        with patch("mcp_harbour.gateway.keyring.get_password", side_effect=Exception("keyring broke")):
            assert gateway._resolve_identity_from_token("harbour_sk_test") is None

    def test_does_not_match_partial_token(self, config_manager):
        token = config_manager.add_identity("agent")

        gateway = make_gateway(config_manager)
        assert gateway._resolve_identity_from_token(token[:20]) is None
