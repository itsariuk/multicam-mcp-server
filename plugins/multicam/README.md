# Multicam for ChatGPT Desktop

Multicam combines conversation with requested snapshots from named phone cameras for hands-on work. Demonstrated uses include two-camera capture, label reading, finger counting, and an item journal with saved photos. Good fits include journaling and inventory, step-by-step documentation, and repairs using a wide view and a close-up. Continuous monitoring and precise measurement have not been validated. The native plugin package includes the camera service and its runtime. You do not need a console, Python, or a separate desktop app. Your phone uses its existing browser.

## First use

1. Install **Multicam** from the plugin source provided by its publisher, enable it, and start a new local Codex task in ChatGPT Desktop.
2. Say or type **“Connect my phone camera.”** Multicam opens joining for **60 seconds** and shows a QR code and a separate PIN on your computer. Codex explains that the page is reachable on the local network, but its address alone grants no access.
3. Put your phone on the same Wi-Fi and scan the code. Enter the PIN shown separately on the desktop; the QR code does not contain it. The local camera page uses a self-signed certificate, so the phone currently shows a certificate warning for your computer's address.
4. Allow camera access, name the view **workbench**, and tap **Start**. Keep that page visible.
5. Tell Codex **“The camera is ready. Take a look.”** Codex checks an actual snapshot.

If voice is available on your account, start a voice chat in Desktop. The desktop microphone handles the conversation; the phone supplies pictures. Typing works too.

## Things to say

| Say | Result |
| --- | --- |
| “Show the QR code again.” | Open the joining page and show the code. |
| “What's my PIN?” | Read the current PIN while joining is open; explain if it has expired. |
| “Open joining again.” | Open a new 60-second window with a fresh PIN. |
| “Connect another phone.” | Join another named view using the same code. |
| “Read the label in detail.” | Take a fresh snapshot from the relevant view. |
| “Compare this with the previous step.” | Compare against an available earlier image. |
| “Record this step.” | Announce the intended view, capture, and save the photo with a note to your chosen journal using available file tools. |
| “Add this item to my inventory.” | Capture the item and relevant label, then save visible details and photos to your chosen inventory. |
| “Stop the workbench camera.” | Disconnect that view. |

For repairs, use `workbench`, `detail`, and `meter`. For crafts, compare alignment between steps. For painting, inspect coverage and label text. Exact color matching is affected by lighting and camera processing.

## What to expect

Pictures are taken on request by Codex. The preview stays on the phone; only metadata is polled while idle. A photo is uploaded only in response to a single-use, expiring capture request; this is not continuous AI observation or a video recording. Snapshots returned to ChatGPT are processed under your ChatGPT settings. Journaling needs an agreed destination and file tools. Once these are available, a request to record an item or step includes saving its photo with the note, without a separate photo-saving request. Codex announces the intended subject and view before capture and confirms the actual saved files afterward. If it cannot save the photo, it says so; the camera server does not save a project journal itself.

Desktop tasks share one service, camera registry, and PIN. About two minutes after the last plugin connection closes, the service shuts down. Joining starts closed. Ask Codex to reopen it for 60 seconds and get a new PIN. Tokens stay only in memory. A phone-page reload or server restart requires a new PIN entry during an open window. Existing connected cameras can answer snapshots after the window closes. Keep the phone page visible and awake; a hidden page may stop sending images.

A phone cannot connect through a guest Wi-Fi network that isolates devices. On first use the operating system may request local-network/firewall permission. The phone initiates outbound HTTPS and exposes no camera-listening port. The desktop service accepts phone access only on its selected LAN interface. MCP itself listens only on the computer's loopback interface and requires an internal random token.

If the plugin is unavailable, confirm that it is enabled and start a new task. If a phone stops answering, check Wi-Fi. After reloading the page, ask Codex to open joining again and enter the new PIN. Use **Stop camera** on the phone to stop its camera and revoke its session token. If another manually launched Multicam server is running, stop that old server before using the packaged version; do not run both phone listeners on the same port.

## Availability

This is a native plugin package, with a separate build for each operating system and architecture. The source folder alone is not runnable: a release build adds `bin/`. Linux has been tested with automated MCP protocol checks. Windows/macOS build jobs are provided but their resulting packages and phone permissions still need testing. Android Chrome is the tested phone browser; iOS Safari is unverified.

The plugin is not yet published in OpenAI's public directory. OpenAI's current public submission instructions require a remote HTTPS MCP endpoint or contacting OpenAI for local MCP support. A ZIP is a package artifact, not a promised Desktop double-click installer. Local marketplace installation is the development/testing route.
