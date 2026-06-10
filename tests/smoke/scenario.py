"""Portable end-to-end usability scenario for MCP Harbour.

Drives an *external* harbour (source CLI, frozen binary, or installed binary)
and validates real MCP behavior. Reused by every layer of the test framework,
on every OS.

Modes:
  serve-check   Self-contained: isolated config, dock/identity/permit, start
                `harbour serve`, connect, assert, tear down. (binary smoke, L5)
  configure     Write servers/identities/policies into the ambient config dir
                (MCP_HARBOUR_CONFIG_DIR or the installed default) and print
                `TOKEN=<api key>`. Used before starting the real service. (L8)
  check         Connect to an already-running daemon at --url with --token and
                run the client assertions only. (L8, against the service)

Exit code 0 = all checks passed, 1 = a check failed or setup errored.
"""

import argparse
import asyncio
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DOWNSTREAM = Path(__file__).resolve().parent / "downstream_server.py"
TOKEN_RE = re.compile(r"harbour_sk_[A-Za-z0-9]+")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def harbour_command(arg: str | None) -> list[str]:
    if arg:
        return shlex.split(arg, posix=(os.name != "nt"))
    return [sys.executable, "-m", "mcp_harbour.main"]


def run_cli(cmd: list[str], env: dict, *step: str) -> subprocess.CompletedProcess:
    result = subprocess.run([*cmd, *step], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"`harbour {' '.join(step)}` failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result


def wait_ready(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                urllib.request.Request(url, method="POST", data=b"{}"), timeout=2
            )
            return True
        except urllib.error.HTTPError:
            return True  # 401/400 etc. — server is up and answering
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[bool, str]] = []

    def check(self, ok: bool, label: str) -> None:
        self.results.append((bool(ok), label))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    def ok(self) -> bool:
        return bool(self.results) and all(ok for ok, _ in self.results)


def downstream_command() -> str:
    # Forward-slash paths survive Harbour's posix shlex.split on every OS.
    return f"{Path(sys.executable).as_posix()} {DOWNSTREAM.as_posix()}"


def setup_config(harbour: list[str], env: dict, checks: Checks) -> str:
    """Dock the downstream, create an identity, grant scoped policy, and assert
    the surrounding CLI surface (list/inspect/permit show/identity list).
    Returns the API token."""
    run_cli(harbour, env, "dock", "--name", "smoke", "--command", downstream_command())
    created = run_cli(harbour, env, "identity", "create", "agent")
    match = TOKEN_RE.search(created.stdout)
    if not match:
        raise RuntimeError(f"could not parse API key from:\n{created.stdout}")
    token = match.group(0)
    run_cli(harbour, env, "permit", "allow", "agent", "smoke", "--tool", "echo")
    run_cli(harbour, env, "permit", "allow", "agent", "smoke", "--tool", "add", "--args", "a=re:^\\d+$")

    # L9 — CLI surface assertions against the same binary.
    listed = run_cli(harbour, env, "list").stdout
    checks.check("smoke" in listed, "`list` shows the docked server")
    inspected = run_cli(harbour, env, "inspect", "smoke").stdout
    checks.check("smoke" in inspected, "`inspect` shows server details")
    identities = run_cli(harbour, env, "identity", "list").stdout
    checks.check("agent" in identities, "`identity list` shows the identity")
    policy = run_cli(harbour, env, "permit", "show", "agent").stdout
    checks.check("echo" in policy and "add" in policy, "`permit show` lists granted tools")

    return token


async def run_client_checks(url: str, token: str, checks: Checks) -> None:
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(
            httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=20)
        )
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(url, http_client=http, terminate_on_close=False)
        )
        session = await stack.enter_async_context(ClientSession(read, write))

        init = await session.initialize()
        checks.check(init.serverInfo.name == "mcp-harbour", "initialize returns mcp-harbour")

        tools = {t.name for t in (await session.list_tools()).tools}
        checks.check(tools == {"echo", "add"}, f"list_tools is policy-filtered (got {sorted(tools)})")
        checks.check("secret" not in tools, "denied tool hidden from discovery")

        echo = await session.call_tool("echo", {"message": "hi"})
        checks.check(
            echo.isError is False and "echo:hi" in json.dumps(echo.model_dump()),
            "allowed tool call succeeds",
        )
        denied = await session.call_tool("secret", {})
        checks.check(denied.isError is True, "denied tool call is rejected")
        good = await session.call_tool("add", {"a": 2, "b": 3})
        checks.check(
            good.isError is False and "5" in json.dumps(good.model_dump()),
            "argument policy allows valid value",
        )
        bad = await session.call_tool("add", {"a": "nope", "b": 3})
        checks.check(bad.isError is True, "argument policy rejects invalid value")


def check_unauthenticated(url: str, checks: Checks) -> None:
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                url, method="POST", data=b"{}",
                headers={"Authorization": "Bearer harbour_sk_bogus"},
            ),
            timeout=5,
        )
        checks.check(False, "unauthenticated request rejected (got 2xx)")
    except urllib.error.HTTPError as e:
        checks.check(e.code == 401, f"unauthenticated request rejected (got {e.code})")
    except (urllib.error.URLError, OSError) as e:
        checks.check(False, f"unauthenticated request rejected (transport error: {e})")


def finish(checks: Checks) -> int:
    print()
    if checks.ok():
        print("SCENARIO PASSED")
        return 0
    print("SCENARIO FAILED")
    return 1


def cmd_serve_check(args) -> int:
    harbour = harbour_command(args.harbour)
    checks = Checks()
    with tempfile.TemporaryDirectory(prefix="harbour-smoke-") as tmp:
        env = {**os.environ, "MCP_HARBOUR_CONFIG_DIR": tmp}
        print(f"harbour: {' '.join(harbour)}\nconfig:  {tmp}")
        token = setup_config(harbour, env, checks)

        port = free_port()
        url = f"http://127.0.0.1:{port}/mcp"
        daemon = subprocess.Popen(
            [*harbour, "serve", "--port", str(port)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            if not wait_ready(url):
                daemon.terminate()
                print("FAIL: daemon did not become ready")
                print(daemon.communicate(timeout=10)[0])
                return 1
            check_unauthenticated(url, checks)
            asyncio.run(run_client_checks(url, token, checks))
        finally:
            daemon.terminate()
            try:
                daemon.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.communicate()
    return finish(checks)


def cmd_configure(args) -> int:
    harbour = harbour_command(args.harbour)
    checks = Checks()
    env = dict(os.environ)  # ambient config dir (installed default or override)
    token = setup_config(harbour, env, checks)
    if not checks.ok():
        finish(checks)
        return 1
    print(f"TOKEN={token}")
    return 0


def cmd_check(args) -> int:
    checks = Checks()
    base = f"{urlparse(args.url).scheme}://{urlparse(args.url).netloc}/mcp"
    if not wait_ready(base):
        print(f"FAIL: daemon at {base} did not respond")
        return 1
    check_unauthenticated(base, checks)
    asyncio.run(run_client_checks(args.url, args.token, checks))
    return finish(checks)


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Harbour usability scenario")
    sub = parser.add_subparsers(dest="mode")

    sc = sub.add_parser("serve-check", help="self-contained serve + assert (L5)")
    sc.add_argument("--harbour")
    sc.set_defaults(func=cmd_serve_check)

    cf = sub.add_parser("configure", help="write config into ambient config dir, print TOKEN (L8)")
    cf.add_argument("--harbour")
    cf.set_defaults(func=cmd_configure)

    ck = sub.add_parser("check", help="assert against a running daemon (L8)")
    ck.add_argument("--url", required=True, help="e.g. http://127.0.0.1:4767/mcp")
    ck.add_argument("--token", required=True)
    ck.set_defaults(func=cmd_check)

    # Backward-compatible default: no subcommand behaves like serve-check.
    parser.add_argument("--harbour", dest="root_harbour", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.mode is None:
        args.harbour = args.root_harbour
        return cmd_serve_check(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
