"""Tests for the daemon handshake protocol, remainder handling, and port conflict."""

import json
import socket
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from tests.conftest import make_mock_process, make_gateway


# ─── Helpers ────────────────────────────────────────────────────────


class MockStream:
    def __init__(self):
        self._inbox = asyncio.Queue()
        self._outbox = asyncio.Queue()

    async def send(self, data: bytes):
        await self._outbox.put(data)

    async def receive(self, max_bytes: int = 4096) -> bytes:
        data = await self._inbox.get()
        if data is None:
            raise Exception("Stream closed")
        return data

    async def inject(self, data: bytes):
        await self._inbox.put(data)

    async def read_response(self, timeout: float = 2.0) -> bytes:
        return await asyncio.wait_for(self._outbox.get(), timeout=timeout)

    async def aclose(self):
        pass


def _make_handshake_gateway(config_manager, token_identity=None):
    gateway = make_gateway(config_manager)
    if token_identity is not None:
        gateway._resolve_identity_from_token = MagicMock(return_value=token_identity)
    return gateway


# ─── Handshake Protocol ─────────────────────────────────────────────


class TestHandshakeProtocol:
    @pytest.mark.asyncio
    async def test_valid_token(self, config_manager):
        gateway = _make_handshake_gateway(config_manager, "test-agent")
        config_manager.add_identity("test-agent")

        stream = MockStream()
        await stream.inject(b'{"auth": "harbour_sk_testtoken"}\n')

        task = asyncio.create_task(gateway._handle_connection(stream))
        ack = json.loads((await stream.read_response()).decode())
        assert ack["status"] == "ok"
        assert ack["identity"] == "test-agent"

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_invalid_token(self, config_manager):
        gateway = _make_handshake_gateway(config_manager, None)

        stream = MockStream()
        await stream.inject(b'{"auth": "bad_token"}\n')
        await gateway._handle_connection(stream)

        assert json.loads((await stream.read_response()).decode())["error"] == "Invalid token"

    @pytest.mark.asyncio
    async def test_missing_token(self, config_manager):
        stream = MockStream()
        await stream.inject(b'{"something": "else"}\n')
        await make_gateway(config_manager)._handle_connection(stream)

        assert json.loads((await stream.read_response()).decode())["error"] == "Missing auth token"

    @pytest.mark.asyncio
    async def test_invalid_json(self, config_manager):
        stream = MockStream()
        await stream.inject(b'not json at all\n')
        await make_gateway(config_manager)._handle_connection(stream)

        assert "error" in json.loads((await stream.read_response()).decode())

    @pytest.mark.asyncio
    async def test_no_newline(self, config_manager):
        stream = MockStream()
        await stream.inject(b'{"auth": "token"}')
        await make_gateway(config_manager)._handle_connection(stream)

        assert "error" in json.loads((await stream.read_response()).decode())


# ─── Remainder Handling ──────────────────────────────────────────────


class TestRemainderHandling:
    @pytest.mark.asyncio
    async def test_data_after_handshake_not_lost(self, config_manager):
        """If handshake and first MCP message arrive in the same TCP chunk, both are processed."""
        config_manager.add_identity("test-agent")
        config_manager.grant_permission("test-agent", "test-server", tool="*")

        gateway = _make_handshake_gateway(config_manager, "test-agent")
        gateway.daemon.spawn_stdio_instance = AsyncMock(
            return_value=make_mock_process("test-server", ["read_file"])
        )

        initialize_msg = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"}
            }
        })
        combined = b'{"auth": "harbour_sk_testtoken"}\n' + initialize_msg.encode() + b'\n'

        stream = MockStream()
        await stream.inject(combined)
        task = asyncio.create_task(gateway._handle_connection(stream))

        ack = json.loads((await stream.read_response(timeout=3.0)).decode())
        assert ack["status"] == "ok"

        try:
            response = json.loads((await asyncio.wait_for(stream.read_response(), timeout=3.0)).decode())
            assert "result" in response or "error" in response
        except asyncio.TimeoutError:
            pytest.fail("Gateway did not respond to initialize — remainder was likely dropped")
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ─── Port Conflict ───────────────────────────────────────────────────


class TestPortConflict:
    @pytest.mark.asyncio
    async def test_exits_on_port_in_use(self, config_manager):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        try:
            with pytest.raises(SystemExit) as exc_info:
                await make_gateway(config_manager).serve("127.0.0.1", port)
            assert exc_info.value.code == 1
        finally:
            sock.close()
