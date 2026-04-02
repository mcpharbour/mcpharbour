"""Tests for the bridge module's handshake logic."""

import json
import socket
import asyncio
import pytest


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestBridgeHandshake:
    """Tests for run_bridge handshake behavior using a mock TCP server."""

    async def _run_mock_server(self, response: bytes):
        """Start a TCP server on a free port, accept one connection, read handshake, send response.
        Returns (server, port, got_handshake_future)."""
        got_handshake = asyncio.Future()

        async def handler(reader, writer):
            line = await reader.readline()
            if not got_handshake.done():
                got_handshake.set_result(line)
            writer.write(response)
            await writer.drain()
            writer.close()

        # Bind to port 0 to let the OS assign a free port — no race condition
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        return server, port, got_handshake

    @pytest.mark.asyncio
    async def test_sends_token_in_handshake(self):
        server, port, got_handshake = await self._run_mock_server(
            b'{"status": "ok", "identity": "agent"}\n'
        )

        async with server:
            from mcp_harbour.bridge import run_bridge

            task = asyncio.create_task(run_bridge("harbour_sk_test123", "127.0.0.1", port))

            handshake = await asyncio.wait_for(got_handshake, timeout=3)
            parsed = json.loads(handshake.decode())
            assert parsed == {"auth": "harbour_sk_test123"}

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, SystemExit, Exception):
                pass

    @pytest.mark.asyncio
    async def test_exits_on_error_response(self):
        server, port, _ = await self._run_mock_server(
            b'{"error": "Invalid token"}\n'
        )

        async with server:
            from mcp_harbour.bridge import run_bridge

            with pytest.raises(SystemExit) as exc_info:
                await run_bridge("bad_token", "127.0.0.1", port)
            assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_exits_on_connection_refused(self):
        from mcp_harbour.bridge import run_bridge

        # Use a free port that we know is not listening
        port = _find_free_port()
        with pytest.raises(SystemExit) as exc_info:
            await run_bridge("token", "127.0.0.1", port)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_exits_on_invalid_json_response(self):
        server, port, _ = await self._run_mock_server(b'not json\n')

        async with server:
            from mcp_harbour.bridge import run_bridge

            with pytest.raises(SystemExit) as exc_info:
                await run_bridge("token", "127.0.0.1", port)
            assert exc_info.value.code == 1
