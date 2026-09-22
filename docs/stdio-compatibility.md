# STDIO compatibility

The main [README](../README.md) recommends one long-lived Streamable HTTP server. Use STDIO compatibility when an MCP client cannot connect to a remote Streamable HTTP endpoint or when one client needs only cameras attached to its own computer.

STDIO launches one server process for each client connection. Do not use this configuration from several clients that need the same phone cameras: each process has its own in-memory camera registry and PIN, while only one process can bind phone HTTPS port `8443`. Tool calls can therefore reach a process that has neither the connected phone nor the HTTPS listener.

## Codex

```bash
codex mcp add multicam --env ENABLE_FRAMEGRAB_PHONE_CAMERAS=true -- \
  uvx --from git+https://github.com/itsariuk/multicam-mcp-server multicam-mcp-server
```

Equivalent `~/.codex/config.toml` entry:

```toml
[mcp_servers.multicam]
command = "uvx"
args = ["--from", "git+https://github.com/itsariuk/multicam-mcp-server", "multicam-mcp-server"]

[mcp_servers.multicam.env]
ENABLE_FRAMEGRAB_PHONE_CAMERAS = "true"
```

## Claude Desktop

For one Claude Desktop connection, add the following entry to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "multicam": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/itsariuk/multicam-mcp-server",
        "multicam-mcp-server"
      ],
      "env": {
        "ENABLE_FRAMEGRAB_PHONE_CAMERAS": "true"
      }
    }
  }
}
```

Leave out `ENABLE_FRAMEGRAB_PHONE_CAMERAS` if the client only needs cameras attached to the computer. STDIO is the runtime default for compatibility; set `MULTICAM_MCP_TRANSPORT=streamable-http` for the shared-server setup described in the main README.
