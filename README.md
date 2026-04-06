# MCP Harbour

The port authority for your MCP servers. Dock your servers once, control which agents can access which tools, and manage everything from a single place.

Built as an implementation of the [GPARS](https://gpars.io) plane boundary — the user-controlled layer that verifies agent identity and governs what agents are permitted to do.

## Install

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/mcpharbour/mcpharbour/main/scripts/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/mcpharbour/mcpharbour/main/scripts/install.ps1 | iex
```

This downloads the binaries, registers the daemon as a system service, and starts it. No Python or package manager required.

## Quick Start

```bash
# Dock an MCP server
harbour dock --name filesystem \
  --command "npx -y @modelcontextprotocol/server-filesystem /home/user/projects"

# Create an identity for your agent
harbour identity create my-agent

# Grant permissions
harbour permit allow my-agent filesystem --tool "*" --args "path=/home/user/projects/**"
```

Then configure your MCP client (Claude Desktop, VS Code, Cursor):

```json
{
  "mcpServers": {
    "harbour": {
      "command": "harbour-bridge",
      "args": ["--token", "harbour_sk_..."]
    }
  }
}
```

The agent sees tools only from servers and tools its policy permits. No policy means no access.

## How It Works

```
Agent → harbour-bridge → TCP:4767 → Harbour Daemon → MCP Servers
              │                           │
         (no admin          identity verification
          access)           policy enforcement
                            AUTHORIZATION_DENIED / SERVER_UNAVAILABLE
```

## Documentation

| Doc | Description |
|-----|-------------|
| [Architecture](docs/concepts/architecture.mdx) | System design, GPARS alignment |
| [CLI Reference](docs/reference/cli.mdx) | All commands and options |
| [Permissions](docs/concepts/permissions.mdx) | Policy engine, error codes |
| [Configuration](docs/reference/configuration.mdx) | Config format, file layout |
| [Contributing](CONTRIBUTING.md) | Development setup, guidelines |

## Author

[Ismael Kaissy](https://github.com/15m43lk4155y)

## License

This project is licensed under the [MIT License](./LICENSE).
