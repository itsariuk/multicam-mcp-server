# multicam-mcp-server

Give an MCP client on-demand snapshots from several named cameras. Put a phone over each work area, connect an existing camera or stream, and ask Codex to look at the view you need: `workbench`, `component close-up`, or `meter`.

Phone cameras join from a browser—there is no phone app to install. The server also supports webcams, USB cameras, RTSP and HLS streams, YouTube live streams, video files, RealSense cameras, Basler cameras, and other inputs supported by [framegrab](https://github.com/groundlight/framegrab).

<p>
  <img src="assets/phone-camera-page.png" width="300" alt="The phone camera page in Chrome on Android, live as 'left bench'">
  <img src="assets/phone-camera-frame.jpg" width="300" alt="The frame the agent received from grab_frame at the same moment">
</p>

*Left: the page on the phone. Right: what the agent got back from `grab_frame("left bench")` at the same moment (full size is 2160×4080).*

## Practical workflows

- Put two or three named cameras around an electronics repair or craft project: one over the workbench, one close to the component, and one aimed at an instrument display. Ask Codex to inspect a particular view without stopping what you are doing.
- Ask hands-free questions about what a camera shows, such as reading a visible component or material label, checking an instrument reading, or describing the next repair step. Image quality and viewing angle matter, so the assistant should say when text or a reading is uncertain.
- Direct Codex to keep a work journal: ask it to capture selected snapshots and your comments, note steps, materials, and results, then produce a summary or illustrated instructions. For example, show a paint can and say that it is the first coat; later ask which paint and shade you used, or request a summary of the materials, coats, and result.

> [!IMPORTANT]
> This server supplies named camera access and on-demand snapshots. Journaling, conversation, memory, file storage, and final documents are client-side workflows that you must ask or configure Codex to perform. The server does not continuously record video, watch autonomously, decide when to take snapshots, synchronize a project log, or create documentation by itself.

This project is in early development. Tools and behaviour may change.

## Quick start

You need [uv](https://docs.astral.sh/uv/). The default STDIO mode is the simplest setup: your MCP client starts the server when needed. The first start downloads the dependencies and can take a while.

### Codex
```bash
codex mcp add multicam --env ENABLE_FRAMEGRAB_PHONE_CAMERAS=true -- \
  uvx --from git+https://github.com/itsariuk/multicam-mcp-server multicam-mcp-server
```

Or in `~/.codex/config.toml`:
```toml
[mcp_servers.multicam]
command = "uvx"
args = ["--from", "git+https://github.com/itsariuk/multicam-mcp-server", "multicam-mcp-server"]

[mcp_servers.multicam.env]
ENABLE_FRAMEGRAB_PHONE_CAMERAS = "true"
```

#### One shared server for several Codex clients

STDIO starts a separate server process for each client connection. If several Codex tasks or clients must share the same named cameras, phone PIN, and in-memory state, run one long-lived Streamable HTTP server instead:

```bash
ENABLE_FRAMEGRAB_PHONE_CAMERAS=true \
MULTICAM_MCP_TRANSPORT=streamable-http \
MULTICAM_MCP_PORT=8000 \
uvx --from git+https://github.com/itsariuk/multicam-mcp-server multicam-mcp-server
```

Keep that process running with your preferred service manager, then point Codex at it:

```toml
[mcp_servers.multicam]
url = "http://127.0.0.1:8000/mcp"
```

The MCP endpoint listens only on loopback by default. The separate phone-camera HTTPS page still listens on the LAN when phone cameras are enabled.

### Other MCP clients

#### Claude Desktop
Add this to your `claude_desktop_config.json`:
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

#### Zed
Add this to your Zed `settings.json`:
```json
{
  "context_servers": {
    "multicam": {
      "command": {
        "path": "uvx",
        "args": [
          "--from",
          "git+https://github.com/itsariuk/multicam-mcp-server",
          "multicam-mcp-server"
        ]
      }
    }
  }
}
```

Leave out `ENABLE_FRAMEGRAB_PHONE_CAMERAS` if you only want cameras attached to the computer. Phone cameras are off unless you turn them on, because they open a port on your network. See [Security](#security).

## Phone cameras (experimental)

With `ENABLE_FRAMEGRAB_PHONE_CAMERAS="true"`, the server also serves a small web page over HTTPS on port 8443.

1. Ask the agent to add a phone camera. It calls the `add_phone_camera` tool, which opens a page on the computer with a QR code and a PIN, and tells the agent both. If the page does not open, the agent can read you the link and the PIN.
2. Put the phone on the same Wi-Fi as the computer and scan the QR code with its camera. The PIN arrives with it. Without a scanner, open `https://<your-computer's-ip>:8443/` on the phone and type the PIN.
3. Accept the browser's certificate warning. The server makes its own self-signed certificate, because browsers only allow camera access over HTTPS. You do this once per phone. It will ask again if your computer's IP address changes.

   <img src="assets/cert-warning.png" width="240" alt="Chrome's 'Your connection is not private' warning. Tap Advanced, then Proceed.">
4. Allow camera access, type a name such as `left bench`, and tap **Start**.
5. Prop the phone up and plug it into a charger.

### Working with several cameras

Repeat the setup on each phone and give every view a descriptive, stable name such as `workbench`, `close-up`, and `meter`. The names work like any other framegrabber. Ask the agent to list the available cameras, inspect one by name, or compare snapshots from several views.

What to expect:

| Situation | What happens |
|-----------|--------------|
| Agent calls `grab_frame` | The server asks the phone for a full-resolution photo and waits up to 4 seconds. If the photo does not arrive, it returns the latest preview instead. |
| Agent calls `release_grabber` | The page stops sending and says so. Tap **Start** to rejoin. |
| The MCP client restarts the server | The page reconnects by itself under the same name. You don't need to touch the phone. |
| The page is closed or in the background, the phone sleeps, or another app takes the camera | The page stops sending its preview, and `grab_frame` returns an error saying how long ago the last frame arrived, not an old image. Sending resumes by itself when the page is back on screen. |
| The page is open in two tabs | Only one tab can use the camera. The second tab tells you after a few seconds. |
| A name is already used by a USB or RTSP camera | The page shows an error. Pick another name. |
| The phone asks for the PIN again | Its token is no longer valid, for example after the data directory was deleted. Ask the agent for the current PIN. |

While nobody is requesting a snapshot, the phone targets one small preview per second instead of continuously uploading full-resolution images.

The page requires an uncropped `getUserMedia` mode (`resizeMode: none`) and downscales previews without changing their aspect ratio, so the preview keeps every edge exposed by the camera track. It does not silently accept the browser's cropped video mode. On browsers that also support the Image Capture API, an on-demand snapshot uses the maximum native still-image dimensions and aspect ratio advertised by the phone. The server does not crop, stretch, or resize that photo. Other browsers fall back to the complete uncropped video frame.

On the page you can switch between the front and back camera. Where the browser supports it, you can also turn on the torch. The page asks the phone to keep the screen on while it is live.

### Security
Joining needs the PIN shown on the computer, so only someone who can see that screen can add a camera or take over an existing name. The PIN changes every time the server starts. After a phone has joined, it keeps a token for its camera name, so reloading the page or restarting the server does not ask for the PIN again. Five wrong PINs from one address pause that address for a minute.

The token key and the certificate are kept in the data directory (see [Configuration](#configuration)). Delete that directory to sign every phone out.

Frames travel from the phone to this server over HTTPS. The server does not upload them elsewhere, but the connected MCP client decides how returned snapshots are processed or stored. The certificate is self-signed, which is why the phone shows a warning the first time.

### Tested with
Chrome on Android. Safari on iOS has not been tested yet.

## Other cameras and streams

Ask the agent to create a framegrabber for a webcam, a USB camera, an RTSP URL, a YouTube live stream, an HLS stream, a video file, a RealSense camera, or a Basler camera. It then grabs frames from it by name.

![Claude Desktop creating a framegrabber for a YouTube live stream and grabbing a frame](assets/framegrab-mcp-in-action.png)

*Screenshot from the original framegrab-mcp-server.*

### (experimental) Autodiscovery
Set `ENABLE_FRAMEGRAB_AUTO_DISCOVERY="true"` to add webcams and USB cameras automatically at startup.

With autodiscovery on, `FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE` controls the search for RTSP cameras. The default is `"off"`. For a thorough search, use `"complete_fast"`. This makes the server slower to start.

## MCP interface

### Tools

| Tool | What it does |
|------|--------------|
| `create_framegrabber` | Create a framegrabber from a configuration object and add it to the available grabbers. |
| `grab_frame` | Grab a frame from a framegrabber and return it as an image (`png`, `jpg`, or `webp`). |
| `list_framegrabbers` | List all available framegrabbers by name, including phone cameras. |
| `get_framegrabber_config` | Return the configuration of a framegrabber. |
| `set_config` | Update the configuration options of a framegrabber. Phone cameras have no options. |
| `release_grabber` | Release a framegrabber and remove it from the available grabbers. |
| `add_phone_camera` | Return the link, the PIN and a QR code for joining a phone, and open a page showing them on this computer (`open_browser=false` skips that). |

### Resources

- `fg://framegrabbers`: all available framegrabbers by name.

## Configuration

All settings are environment variables.

| Variable | Default | Meaning |
|----------|---------|---------|
| `ENABLE_FRAMEGRAB_PHONE_CAMERAS` | `false` | Serve the phone camera page. |
| `FRAMEGRAB_PHONE_CAMERAS_PORT` | `8443` | HTTPS port for the phone camera page. If the port is taken, phone cameras are turned off for that run and the rest of the server works as usual. |
| `ENABLE_FRAMEGRAB_AUTO_DISCOVERY` | `false` | Discover webcams and USB cameras at startup. |
| `FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE` | `off` | One of `off`, `ip_only`, `light`, `complete_fast`, `complete_slow`. |
| `MULTICAM_MCP_TRANSPORT` | `stdio` | MCP transport. Use `streamable-http` for one long-lived server shared by multiple clients. |
| `MULTICAM_MCP_HOST` | `127.0.0.1` | Listen address in Streamable HTTP mode. |
| `MULTICAM_MCP_PORT` | `8000` | Listen port in Streamable HTTP mode. |

The self-signed certificate, the token key and the join page are kept in your user data directory (on Linux, `~/.local/share/multicam-mcp-server/`). Delete that directory to make a new certificate and sign every phone out.

## Development

```bash
uv sync --extra dev
uv run pytest -q                 # tests
make mcp-inspector               # try the tools in the MCP inspector
make run-server                  # run the server over stdio
make build                       # build the wheel
```

The phone camera server lives in `multicam_mcp_phone.py`, and the page is `multicam_mcp_phone.html`. `multicam_mcp_server.py` holds the MCP tools.

## Credits

multicam-mcp-server is a fork of [framegrab-mcp-server](https://github.com/groundlight/framegrab-mcp-server) by [Groundlight AI](https://www.groundlight.ai/), which provides the original MCP server and framegrabber tools. This fork renames the project and adds phone browser cameras. Both are licensed under Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Roadmap

- Testing on iOS Safari.
