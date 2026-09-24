# Desktop plugin packaging

The intended user experience is entirely inside ChatGPT Desktop and the phone's browser. See the [plugin guide](../plugins/multicam/README.md) for setup, prompts, use cases, and present availability limits.

## Package architecture

- `plugins/multicam/`: manifest, MCP launch configuration, workflow skill, and user guide.
- `multicam_plugin.py`: bundled executable entrypoint. Desktop launches it over STDIO. It starts or reuses one local service and forwards MCP messages through the SDK's Streamable HTTP transport.
- The service uses an OS lock to prevent duplicate launches, a dynamically allocated loopback HTTPS port, and a random internal bearer token stored in a private state file. Each service instance generates a TLS identity; the bridge trusts only that instance's certificate from the private state file and verifies it before sending any bearer token or MCP traffic, including on reconnects. Stale plaintext state is rejected. Each desktop connection sends a heartbeat. The service exits after approximately 120 seconds without a connection's traffic.
- The phone HTTPS listener and desktop QR page stay on the local machine. The QR contains only the URL. Registration is closed until a tool opens a 60-second window; uploads require an authorized token plus an eight-second, single-use capture request ID. Idle phones send only metadata polls. Phone pairing credentials are exposed only through MCP and the local joining file, never through the unauthenticated phone page.
- The phone page and full Python runtime/dependencies are included by PyInstaller. No end-user dependency download or terminal command is needed to run a native build.

## Maintainer builds

These commands are for maintainers, not end users:

```bash
uv sync --frozen --extra dev
uv pip install pyinstaller==6.22.3
uv run python scripts/build_plugin.py
```

Output: `dist/<platform>-<architecture>/multicam/` and a ZIP alongside it. Build on the target OS; PyInstaller does not cross-compile. The workflow `.github/workflows/plugin-package.yml` builds Linux, Windows, and macOS artifacts without publishing a release.

The source manifest is a Codex compatibility manifest, retained for the plugin-creator validator. The package's `.mcp.json` points at the bundled executable through `${CLAUDE_PLUGIN_ROOT}`; Windows builds add `.exe`. Do not replace this with `uvx` or a system Python command in consumer packages.

Use the plugin-creator scaffold to register the completed build in a personal marketplace. Installing a ZIP is not a documented replacement for marketplace registration. After install/reinstall, start a new task to load its skills and tools. Do not ship a development checkout without its native runtime.

## Validation and remaining release work

Automated checks cover PIN retrieval with leading zeros, service locking, rejected unauthenticated access, two simultaneous MCP clients, QR image return, and keeping the service available after one client closes. Security tests cover timed pairing, PIN-only registration, global/per-address guess limits, session-token revocation, host/origin restrictions, bounded bodies, and unsolicited/replayed/expired capture rejection. The browser-loop harness verifies that idle polls do not encode or upload images.

Before public release, test installation on clean Windows/macOS machines, native code signing/notarization where appropriate, local-network/firewall prompts, phone camera permission, and actual QR scanning. Validate iOS Safari separately. Confirm the public distribution arrangement with OpenAI. The local self-signed HTTPS warning remains a first-use friction point; packaging alone does not remove it.

## Official references (checked 2026-09-23)

- [MCP support and server instructions](https://learn.chatgpt.com/docs/extend/mcp)
- [Plugin packaging and public local-MCP limitations](https://developers.openai.com/plugins/build/plugins)
- [Desktop voice availability](https://learn.chatgpt.com/docs/features/voice)
