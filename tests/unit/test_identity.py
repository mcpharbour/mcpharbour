"""Tests for identity resolution from tokens."""

import pytest
from unittest.mock import patch
import bcrypt

from mcp_harbour.models import Identity
from tests.conftest import make_gateway


class TestResolveIdentityFromToken:
    def _setup_gateway_with_identities(self, config_manager, identities: dict):
        """Set up a gateway with identities and their tokens stored in a mock keyring."""
        hashed_tokens = {}
        for name, token in identities.items():
            config_manager.add_identity(Identity(name=name, key_prefix=token[:15] + "..."))
            hashed_tokens[name] = bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()

        gateway = make_gateway(config_manager)

        def mock_keyring_get(service, name):
            return hashed_tokens.get(name)

        return gateway, mock_keyring_get

    def test_resolves_correct_identity(self, config_manager):
        gateway, mock_get = self._setup_gateway_with_identities(config_manager, {
            "agent-a": "harbour_sk_aaaa",
            "agent-b": "harbour_sk_bbbb",
        })

        with patch("mcp_harbour.gateway.keyring.get_password", side_effect=mock_get):
            assert gateway._resolve_identity_from_token("harbour_sk_aaaa") == "agent-a"
            assert gateway._resolve_identity_from_token("harbour_sk_bbbb") == "agent-b"

    def test_returns_none_for_unknown_token(self, config_manager):
        gateway, mock_get = self._setup_gateway_with_identities(config_manager, {
            "agent-a": "harbour_sk_aaaa",
        })

        with patch("mcp_harbour.gateway.keyring.get_password", side_effect=mock_get):
            assert gateway._resolve_identity_from_token("harbour_sk_wrong") is None

    def test_returns_none_when_no_identities(self, config_manager):
        gateway = make_gateway(config_manager)

        with patch("mcp_harbour.gateway.keyring.get_password", return_value=None):
            assert gateway._resolve_identity_from_token("harbour_sk_any") is None

    def test_handles_keyring_error_gracefully(self, config_manager):
        config_manager.add_identity(Identity(name="agent", key_prefix="harbour_sk_test..."))
        gateway = make_gateway(config_manager)

        with patch("mcp_harbour.gateway.keyring.get_password", side_effect=Exception("keyring broke")):
            assert gateway._resolve_identity_from_token("harbour_sk_test") is None

    def test_does_not_match_partial_token(self, config_manager):
        gateway, mock_get = self._setup_gateway_with_identities(config_manager, {
            "agent": "harbour_sk_full_token_here",
        })

        with patch("mcp_harbour.gateway.keyring.get_password", side_effect=mock_get):
            assert gateway._resolve_identity_from_token("harbour_sk_full") is None
