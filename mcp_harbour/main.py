import typer
import asyncio
from pathlib import Path
from typing import List, Optional
from rich.console import Console
from rich.table import Table
from .config import ConfigManager

app = typer.Typer(help="MCP Harbour: Manage your MCP servers and permissions.")
console = Console()
config_manager = ConfigManager()

# Sub-typer for identity management
identity_app = typer.Typer()
app.add_typer(identity_app, name="identity", help="Manage identities (Captains)")

# Sub-typer for permission management
permit_app = typer.Typer()
app.add_typer(permit_app, name="permit", help="Manage permissions (Policies)")


def _handle(fn, *args, **kwargs):
    """Call a service method and display any error cleanly."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def dock(
    name: str = typer.Option(..., help="Name of the server/ship"),
    command: Optional[str] = typer.Option(None, help="Full command to run the server (stdio)"),
    url: Optional[str] = typer.Option(None, help="Server URL (streamable HTTP)"),
):
    """
    Dock (install/register) a new MCP server.

    Provide --command for stdio servers or --url for HTTP servers (not both).

    Examples:
      harbour dock --name filesystem --command "npx -y @modelcontextprotocol/server-filesystem /home/user"
      harbour dock --name remote-api --url "http://localhost:8000/mcp"
    """
    _handle(config_manager.add_server, name, command=command, url=url)
    console.print(f"[bold green]Success:[/bold green] Server '{name}' docked successfully!")


@app.command()
def undock(name: str):
    """Undock (remove) an MCP server."""
    _handle(config_manager.remove_server, name)
    console.print(f"[bold green]Success:[/bold green] Server '{name}' undocked.")


@app.command("list")
def list_servers():
    """List all docked MCP servers."""
    servers = config_manager.list_servers()
    if not servers:
        console.print("No servers docked.")
        return

    table = Table(title="Docked Ships (MCP Servers)")
    table.add_column("Name", style="cyan")
    table.add_column("Command", style="magenta")
    table.add_column("Type", style="green")
    for server in servers:
        table.add_row(server.name, server.command or server.url, server.server_type.value)
    console.print(table)


@app.command()
def inspect(name: str):
    """Inspect details of a docked server."""
    server = config_manager.get_server(name)
    if not server:
        console.print(f"[bold red]Error:[/bold red] Server '{name}' not found.")
        raise typer.Exit(code=1)

    console.print(f"[bold]Name:[/bold] {server.name}")
    if server.command:
        console.print(f"[bold]Command:[/bold] {server.command}")
    if server.url:
        console.print(f"[bold]URL:[/bold] {server.url}")
    console.print(f"[bold]Env:[/bold] {server.env}")
    console.print(f"[bold]Type:[/bold] {server.server_type}")


@app.command()
def serve(
    host: str = typer.Option(None, help="Host to bind (default: 127.0.0.1)"),
    port: int = typer.Option(None, help="Port to bind (default: 4767)"),
):
    """Start the Harbour Daemon in the foreground."""
    from .gateway import HarbourGateway
    from .config import DEFAULT_HOST, DEFAULT_PORT
    import sys
    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    serve_host = host or DEFAULT_HOST
    serve_port = port or DEFAULT_PORT

    gateway = HarbourGateway()
    sys.stderr.write(f"Starting Harbour Daemon ({serve_host}:{serve_port})...\n")
    asyncio.run(gateway.serve(serve_host, serve_port))


def _win_query_state() -> str:
    """Return the current Windows service state string."""
    import subprocess
    result = subprocess.run(
        ["sc.exe", "query", "MCPHarbour"],
        capture_output=True, text=True
    )
    for token in ("RUNNING", "STOPPED", "START_PENDING", "STOP_PENDING"):
        if token in result.stdout:
            return token
    return "UNKNOWN"


def _win_sc(action: str, expect_state: str) -> str:
    """Run sc.exe start/stop with UAC elevation fallback.

    Returns: 'ok', 'already', 'denied', or 'failed'.
    """
    import subprocess
    import time

    state = _win_query_state()
    if state == expect_state:
        return "already"

    result = subprocess.run(
        ["sc.exe", action, "MCPHarbour"],
        capture_output=True, text=True
    )

    if result.returncode != 0 and "Access is denied" in (result.stdout + result.stderr):
        console.print("Requesting administrator permission...")
        elevated = subprocess.run(
            ["powershell", "-Command",
             f'Start-Process sc.exe -ArgumentList "{action} MCPHarbour" -Verb RunAs -Wait'],
            capture_output=True,
        )
        if elevated.returncode != 0:
            return "denied"

    for _ in range(10):
        if _win_query_state() == expect_state:
            return "ok"
        time.sleep(1)

    return "failed"


@app.command()
def start():
    """Start the Harbour Daemon via the platform service manager."""
    import subprocess
    import sys

    if sys.platform == "linux":
        subprocess.run(["systemctl", "--user", "start", "mcp-harbour"], check=True)
    elif sys.platform == "darwin":
        plist = f"{Path.home()}/Library/LaunchAgents/dev.mcp-harbour.daemon.plist"
        subprocess.run(["launchctl", "load", plist], check=True)
    elif sys.platform == "win32":
        result = _win_sc("start", "RUNNING")
        if result == "already":
            console.print("[yellow]Daemon is already running.[/yellow]")
            return
        elif result == "denied":
            console.print("[bold red]Error:[/bold red] Access denied by user.")
            raise typer.Exit(1)
        elif result == "failed":
            console.print("[bold red]Error:[/bold red] Failed to start daemon.")
            raise typer.Exit(1)
    else:
        console.print("[bold red]Unsupported platform.[/bold red]")
        raise typer.Exit(1)
    console.print("[bold green]Daemon started.[/bold green]")


@app.command()
def stop():
    """Stop the Harbour Daemon via the platform service manager."""
    import subprocess
    import sys

    if sys.platform == "linux":
        subprocess.run(["systemctl", "--user", "stop", "mcp-harbour"], check=True)
    elif sys.platform == "darwin":
        plist = f"{Path.home()}/Library/LaunchAgents/dev.mcp-harbour.daemon.plist"
        subprocess.run(["launchctl", "unload", plist], check=True)
    elif sys.platform == "win32":
        result = _win_sc("stop", "STOPPED")
        if result == "already":
            console.print("[yellow]Daemon is already stopped.[/yellow]")
            return
        elif result == "denied":
            console.print("[bold red]Error:[/bold red] Access denied by user.")
            raise typer.Exit(1)
        elif result == "failed":
            console.print("[bold red]Error:[/bold red] Failed to stop daemon.")
            raise typer.Exit(1)
    else:
        console.print("[bold red]Unsupported platform.[/bold red]")
        raise typer.Exit(1)
    console.print("[bold green]Daemon stopped.[/bold green]")


@app.command()
def status():
    """Check if the Harbour Daemon is running."""
    import subprocess
    import sys

    if sys.platform == "linux":
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "mcp-harbour"],
            capture_output=True, text=True
        )
        state = result.stdout.strip()
        if state == "active":
            console.print("[bold green]Daemon is running.[/bold green]")
        else:
            console.print(f"[yellow]Daemon is {state}.[/yellow]")
    elif sys.platform == "darwin":
        result = subprocess.run(
            ["launchctl", "list", "dev.mcp-harbour.daemon"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            console.print("[bold green]Daemon is running.[/bold green]")
        else:
            console.print("[yellow]Daemon is not running.[/yellow]")
    elif sys.platform == "win32":
        result = subprocess.run(
            ["sc", "query", "MCPHarbour"],
            capture_output=True, text=True
        )
        if "RUNNING" in result.stdout:
            console.print("[bold green]Daemon is running.[/bold green]")
        else:
            console.print("[yellow]Daemon is not running.[/yellow]")
    else:
        console.print("[bold red]Unsupported platform.[/bold red]")
        raise typer.Exit(1)


@identity_app.command("create")
def identity_create(name: str):
    """Create a new identity (Captain) and generate an API key."""
    api_key = _handle(config_manager.add_identity, name)
    console.print(f"[bold green]Identity '{name}' created successfully![/bold green]")
    console.print(f"API Key: [bold]{api_key}[/bold]")
    console.print("[yellow]Keep this key safe! It won't be shown again.[/yellow]")


@identity_app.command("list")
def identity_list():
    """List all identities."""
    identities = config_manager.config.identities
    if not identities:
        console.print("No identities found.")
        return

    table = Table(title="Docked Captains (Identities)")
    table.add_column("Name", style="cyan")
    table.add_column("API Key Prefix", style="magenta")
    for name, identity in identities.items():
        table.add_row(name, identity.key_prefix)
    console.print(table)


@identity_app.command("delete")
def identity_delete(name: str):
    """Delete an identity (Captain) and its policy."""
    _handle(config_manager.remove_identity, name)
    console.print(f"[bold green]Success:[/bold green] Identity '{name}' deleted.")


@permit_app.command("allow")
def permit_allow(
    identity: str,
    server: str,
    tool: str = typer.Option("*", help="Tool name or glob pattern (default: *)"),
    args: Optional[List[str]] = typer.Option(
        None, help="Argument policies: 'arg=pattern' (glob) or 'arg=re:pattern' (regex)"
    ),
):
    """
    Grant permission to an identity.

    Examples:
      harbour permit allow agent filesystem
      harbour permit allow agent filesystem --tool "read_*" --args "path=/home/user/**"
      harbour permit allow agent db --tool "query" --args "sql=re:^SELECT.*" "db=production"
    """
    if not config_manager.get_server(server) and server != "*":
        console.print(f"[yellow]Warning: Server '{server}' is not currently docked.[/yellow]")

    _handle(config_manager.grant_permission, identity, server, tool=tool, arg_policies=args)
    console.print(f"[bold green]Permission granted for '{identity}' on '{server}' tool '{tool}'[/bold green]")


@permit_app.command("show")
def permit_show(identity: str):
    """Show the policy for an identity."""
    policy = config_manager.load_policy(identity)
    if not policy:
        console.print(f"[yellow]No policy found for '{identity}'. (Access Denied All)[/yellow]")
        return

    console.print(f"[bold]Policy for {identity}:[/bold]")
    for server, tools in policy.permissions.items():
        console.print(f"  Server: [cyan]{server}[/cyan]")
        for tool in tools:
            pol_str = ""
            if tool.policies:
                pol_str = " -> " + ", ".join(
                    f"{p.arg_name}={'re:' if p.match_type == 'regex' else ''}{p.pattern}"
                    for p in tool.policies
                )
            console.print(f"    - Tool: [green]{tool.name}[/green]{pol_str}")


if __name__ == "__main__":
    app()
