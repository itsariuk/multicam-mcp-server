# multicam-mcp-server

Give Codex or another MCP client on-demand snapshots from several named cameras. Put a phone over each work area, connect an existing camera or stream, and ask for the view you need: `workbench`, `close-up`, or `meter`.

Phone cameras join from a browser—there is no phone app to install. The server also works with webcams, USB cameras, RTSP and HLS streams, YouTube live streams, video files, RealSense cameras, Basler cameras, and other inputs supported by [framegrab](https://github.com/groundlight/framegrab).

<p>
  <img src="assets/phone-camera-page.png" width="300" alt="The phone camera page live as the named camera 'overhand', showing capture diagnostics and an uncropped preview">
  <img src="assets/phone-camera-frame.jpg" width="300" alt="A full-resolution still captured on demand from the same phone camera">
</p>

*Left: the current Android phone page and its full-view preview. Right: an on-demand native still from the same scene. This test phone advertised 3064×4080; dimensions vary by device and camera.*

## What you can do

Use two or three stable camera names to make hands-on work easier:

| Project | Example views | Things to ask Codex |
|---|---|---|
| Electronics repair | `workbench`, `component`, `meter` | “Read the part marking in close-up,” “What does the meter show?”, or “Compare the board before and after this repair.” |
| Crafting | `workspace`, `detail`, `tool` | “Check this alignment,” “Identify the material label,” or “What should I do next?” |
| Painting | `wall`, `paint-label`, `tray` | “Which paint and shade is this?”, “Record this as the first coat,” or “Compare coverage after the second coat.” |

Image quality, focus, glare, and viewing angle still matter. A client interpreting a label, shade, or instrument reading should report uncertainty instead of guessing.

For a user-directed work journal, tell Codex where to save it, then say “record this step” when something matters. Codex can request the relevant snapshot, combine it with your comment, and later turn the selected material into a summary or illustrated instructions. Live help and later documentation can use the same named views.

> [!IMPORTANT]
> This server supplies named camera access and on-demand snapshots. You must explicitly ask or configure Codex to handle conversation, journaling, memory, file storage, or final documents. The server does not continuously record video, watch autonomously, decide when to capture, maintain or synchronize a project log, or create documentation by itself.

This project is in early development. Tools and behavior may change.

## Quick start: one shared server

The recommended Codex setup is one long-lived [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http) server. Every Codex task connects to the same process, so all clients share the camera registry, phone PIN, and phone HTTPS listener.

Install [uv](https://docs.astral.sh/uv/), then start the server:

```bash
ENABLE_FRAMEGRAB_PHONE_CAMERAS=true \
MULTICAM_MCP_TRANSPORT=streamable-http \
uvx --from git+https://github.com/itsariuk/multicam-mcp-server multicam-mcp-server
```

Keep this process running with your preferred service manager. By default, MCP listens only on `127.0.0.1:8000`; the separate phone-camera page listens on HTTPS port `8443` so phones on the local network can reach it.

Point Codex at the shared endpoint in `~/.codex/config.toml`:

```toml
[mcp_servers.multicam]
url = "http://127.0.0.1:8000/mcp"
```

Restart Codex or reload its MCP configuration. The first `uvx` start downloads dependencies and can take a while.

### STDIO: a supported single-client alternative

For one MCP client, it is also fine to let the client launch the server over STDIO:

```bash
codex mcp add multicam --env ENABLE_FRAMEGRAB_PHONE_CAMERAS=true -- \
  uvx --from git+https://github.com/itsariuk/multicam-mcp-server multicam-mcp-server
```

Equivalent Codex configuration:

```toml
[mcp_servers.multicam]
command = "uvx"
args = ["--from", "git+https://github.com/itsariuk/multicam-mcp-server", "multicam-mcp-server"]

[mcp_servers.multicam.env]
ENABLE_FRAMEGRAB_PHONE_CAMERAS = "true"
```

Do not use that STDIO configuration from several clients at once. Each connection launches another process with its own in-memory camera registry and PIN. Only one process can bind phone HTTPS port `8443`, so tool calls can land in a process that has neither the connected phone nor the listener. Use the shared HTTP setup whenever several Codex tasks or MCP clients need the cameras.

Leave out `ENABLE_FRAMEGRAB_PHONE_CAMERAS` if you only need cameras attached to the computer. Phone cameras are opt-in because they open an HTTPS port on the local network.

### Other MCP clients

Clients that support remote Streamable HTTP can use `http://127.0.0.1:8000/mcp`. For a single-client STDIO setup, use the same `uvx` command. For example, Claude Desktop accepts:

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

## Add phone cameras (experimental)

With `ENABLE_FRAMEGRAB_PHONE_CAMERAS=true`, the server provides a small phone page over HTTPS.

1. Ask the agent to add a phone camera. It calls `add_phone_camera`, opens a local page with a QR code and PIN, and returns the same joining information. If the page does not open, the agent can give you the link and PIN.
2. Put the phone on the same Wi-Fi as the computer and scan the QR code. Without a scanner, open `https://<computer-ip>:8443/` on the phone and enter the PIN.
3. Accept the browser's certificate warning. The server creates a self-signed certificate because browsers require HTTPS for camera access.

   <img src="assets/cert-warning.png" width="240" alt="Chrome's 'Your connection is not private' warning. Tap Advanced, then Proceed.">
4. Allow camera access, enter a descriptive name such as `workbench`, and tap **Start**.
5. Prop the phone up, plug it into power, and repeat with any other views.

Names work like any other framegrabber. Ask the client to list cameras, grab one by name, or compare snapshots from several views.

### What happens on the phone

- The page requires `resizeMode: none`, preferring a 4080×3064 landscape mode. A portrait device can expose the corresponding 3064×4080 track. If the browser cannot provide an uncropped track, setup fails clearly instead of silently accepting crop-and-scale behavior.
- While idle, the phone targets one preview after each one-second loop. Previews keep their aspect ratio, use at most 1280 pixels on the long edge, and use JPEG quality `0.7`. Their exact size and timing are device-dependent; the example phone produced 961×1280 previews.
- `grab_frame` asks the page for a native `ImageCapture.takePhoto()` at the maximum still dimensions advertised by the browser. The page does not crop, stretch, or resize that result. If Image Capture is unavailable, it falls back to the complete uncropped video frame.
- The server waits up to four seconds for the requested photo, then falls back to the latest fresh preview. It re-encodes the returned image as the requested `png`, `jpg`, or `webp` format.
- The status line shows the send interval, loop, encoding and upload times, video-frame delta, skipped loops, page visibility, and JavaScript heap usage where the browser exposes it.
- The camera selector switches between front and back cameras. Torch control appears where supported. The page requests a wake lock while live and reconnects automatically after a server restart or short network interruption.

“Native” here means the largest still output the browser reports. The browser, camera HAL, or OEM image pipeline can still apply sensor cropping or other processing, so the server cannot prove that a photo contains every raw physical sensor pixel.

### Connection behavior

| Situation | What happens |
|---|---|
| Agent calls `grab_frame` | The server requests a full-resolution photo and waits up to four seconds; otherwise it uses the latest fresh preview. |
| Agent calls `release_grabber` | The page stops sending and says so. Tap **Start** to rejoin. |
| The shared server restarts | The page reconnects under the same name using its saved token. |
| The page is hidden, the phone sleeps, or another app takes the camera | Uploads stop, and `grab_frame` reports how old the last frame is instead of returning a stale image. Sending resumes when the page is active again. |
| The page is open in two tabs | Only one tab can use the camera; the other reports the conflict. |
| A name belongs to a USB, RTSP, or other camera | The page reports the conflict. Choose another name. |
| The phone asks for the PIN again | Its token is no longer valid, such as after the server data directory was deleted. Request the current PIN. |

### Security

Joining requires the current PIN. It changes whenever the server starts. After joining, the phone stores a name-specific token, so ordinary reloads and server restarts do not require the PIN again. Five wrong PINs from one address pause that address for one minute.

The token key and self-signed certificate are kept in the data directory described under [Configuration](#configuration). Deleting that directory signs every phone out and creates a new certificate on the next start.

Frames travel from the phone to this server over HTTPS. The server does not upload them elsewhere, but the connected MCP client determines how returned snapshots are processed or stored. The phone shows a certificate warning the first time because the certificate is self-signed.

Phone cameras are tested with Chrome on Android. Safari on iOS has not been tested yet.

## Other cameras and streams

Ask the agent to create a framegrabber for a webcam, USB camera, RTSP URL, YouTube live stream, HLS stream, video file, RealSense camera, or Basler camera. It can then grab frames from that source by name.

![Claude Desktop creating a framegrabber for a YouTube live stream and grabbing a frame](assets/framegrab-mcp-in-action.png)

*Screenshot from the original framegrab-mcp-server.*

### Autodiscovery (experimental)

Set `ENABLE_FRAMEGRAB_AUTO_DISCOVERY=true` to add webcams and USB cameras automatically at startup.

With autodiscovery enabled, `FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE` controls the RTSP search. The default is `off`; `complete_fast` performs a thorough search but makes startup slower.

## MCP interface

### Tools

| Tool | What it does |
|---|---|
| `create_framegrabber` | Create a named framegrabber from a framegrab configuration object. |
| `grab_frame` | Grab a frame and return it as `png`, `jpg`, or `webp` (`webp` by default). |
| `list_framegrabbers` | List every available named framegrabber, including phones. |
| `get_framegrabber_config` | Return a framegrabber's configuration. |
| `set_config` | Update a framegrabber's configuration options. Phone cameras have no configurable options. |
| `release_grabber` | Release and remove a framegrabber. |
| `add_phone_camera` | Return the join link, PIN, and QR code, and normally open a local joining page. Pass `open_browser=false` to skip opening it. |

### Resources

- `fg://framegrabbers` lists all available framegrabbers by name.

## Configuration

All settings are environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `ENABLE_FRAMEGRAB_PHONE_CAMERAS` | `false` | Serve the phone-camera page. |
| `FRAMEGRAB_PHONE_CAMERAS_PORT` | `8443` | HTTPS port for phones. If occupied, phone cameras are disabled for that process while other sources remain available. |
| `ENABLE_FRAMEGRAB_AUTO_DISCOVERY` | `false` | Discover webcams and USB cameras at startup. |
| `FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE` | `off` | RTSP discovery mode: `off`, `ip_only`, `light`, `complete_fast`, or `complete_slow`. |
| `MULTICAM_MCP_TRANSPORT` | `stdio` | MCP transport. Set `streamable-http` for a shared long-lived server. |
| `MULTICAM_MCP_HOST` | `127.0.0.1` | MCP listen address in Streamable HTTP mode. |
| `MULTICAM_MCP_PORT` | `8000` | MCP listen port in Streamable HTTP mode. |

The certificate, token key, and joining page are stored in the user data directory—on Linux, `~/.local/share/multicam-mcp-server/`.

## Development

The package uses Python 3.10 or newer and `mcp[cli]>=2.2,<3`.

```bash
uv sync --extra dev
uv run pytest -q                 # tests
make mcp-inspector               # try the tools in the MCP inspector
make run-server                  # run the server over STDIO
make build                       # build the wheel
```

The phone camera server lives in `multicam_mcp_phone.py`, its page is `multicam_mcp_phone.html`, and `multicam_mcp_server.py` contains the MCP tools.

## Credits

multicam-mcp-server is a fork of [framegrab-mcp-server](https://github.com/groundlight/framegrab-mcp-server) by [Groundlight AI](https://www.groundlight.ai/), which provides the original MCP server and framegrabber tools. This fork renames the project and adds browser-based phone cameras. Both are licensed under Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Roadmap

- Test phone cameras on iOS Safari.
