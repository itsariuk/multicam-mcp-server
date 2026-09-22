# Phone Browser Cameras

> **2026-09-21:** the project was renamed from `framegrab-mcp-server` to `multicam-mcp-server` (fork of groundlight/framegrab-mcp-server). Older text below uses the original name; module files are now `multicam_mcp_*.py`.

## Problem Statement

People doing AI-assisted electronics repair at a workbench have to stop, pick up a phone, take a photo, and get it to the agent every time they want the AI to look at something. With both hands on a board and a voice agent (Codex voice mode) otherwise working hands-free, this manual photo step is the slowest and most disruptive part of the loop. Existing phone-as-camera tools are either app-based, paid, or not reachable by AI agents.

## Evidence

- User statement (repo owner, electronics/assisted repair): "today I need to take pictures manually, it takes time and hard."
- User statement on alternatives: "the existing is not easy to use or not free or not exposed to AI agents."
- Market scan (2026-09-21): every camera MCP server found handles PC-attached cameras only. The closest (yunleng) supports phones only via an installed IP Webcam app + pasted RTSP URL. Browser-only phone camera tools (VDO.Ninja) target OBS and need GStreamer/raspberry_ninja to reach Python. Nobody combines browser-only phone page + named cameras + MCP.
- "Other people" having this problem: **Assumption** — needs validation by putting the feature in front of 2-3 other workshop users after MVP.

## Proposed Solution

Add a small HTTPS web server inside the existing `framegrab-mcp-server` process. Any phone on the LAN opens the URL (via QR code), enters a PIN, grants camera permission, and types a camera name. The page sends JPEG stills to the PC; the server keeps the latest per name and exposes them through the existing `list_framegrabbers` / `grab_frame` tools, so agents need no new concepts. Stills over plain `fetch` POST were chosen over WebRTC because the agent consumes single frames, not video — this avoids a heavy dependency (aiortc/GStreamer) and avoids WebSocket, which is unreliable on iOS Safari with self-signed certificates.

## Key Hypothesis

We believe a no-app, browser-based phone camera page will make "AI, look at this" a zero-step action for people doing bench repair with a voice agent.
We'll know we're right when a cold phone goes from QR scan to first agent-visible frame in under 60 seconds, and a registered phone survives a 2-hour session without being touched.

## What We're NOT Building

- Video streaming / WebRTC / OBS integration - the agent needs stills; VDO.Ninja already does video well.
- Native iOS/Android apps - "no app" is the whole point.
- Internet/remote access, tunnels, cloud relay - LAN-only keeps frames private and setup account-free. Users can put their own tunnel in front if they want.
- Recording, history, motion detection, on-server vision models - the agent is the vision model.
- Multi-client shared camera registry (long-running HTTP MCP server) - user connects via stdio; revisit if multiple agents need the same cameras.
- Audio from the phone - voice mode already has a mic.

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Time to first frame (cold phone → `grab_frame` returns its image) | < 60 s, including cert warning + PIN + permission prompt | Stopwatch, on one iPhone and one Android phone not previously connected |
| Session survival | 2 h registered with zero touches (phone on charger) | Leave running; call `grab_frame` every 10 min; count failures |
| `grab_frame` latency for phone cameras | < 2 s preview, < 4 s on-demand hi-res | Log timestamps server-side |
| Legibility | Agent correctly reads the top marking of an SOIC-8 at ~15 cm | Manual test with hi-res capture. **Needs refinement** — distance/part are a first guess |
| Manual photos taken during a repair session | 0 | Self-report |

## Validation Debt

Accepted by the user on 2026-09-21 ("no iphone, skip will test later, mark as debt"). Phases 2-5 proceed on Android evidence only.

| ID | Debt | Why deferred | Risk carried | How to pay it off |
|----|------|--------------|--------------|-------------------|
| D1 | iOS Safari never tested against the self-signed origin (secure context, `getUserMedia`, `fetch` POST, wake lock, behaviour on lock/reopen, max resolution) | No iPhone available | If iOS refuses, the "any modern smartphone" promise is false until `--no-tls` + tunnel is built (currently a Could) | Run `.claude/PRPs/plans/assets/tls_spike.py`, open the URL in Safari, fill the iOS column of Spike Results. ~15 min. Must be paid before phase 6 sign-off and before the README claims iOS support |
| D2 | Screen-off survival on Android is confounded (USB + `stay_on_while_plugged_in=2`, no secure lock) | Test was driven over adb | 2-hour metric may fail on a normally configured phone | Repeat lock test on Wi-Fi + wall charger with a secure lock set; fold into phase 6 soak |
| D3 | Taps-to-first-frame and IC-marking legibility not measured | Page was already open; phone lying flat | 60-second and legibility targets unvalidated | Phase 4 measured the automated lower bound only (1.1 s from link to first frame, cert already accepted). Still owed: a human stopwatch run scanning the join-page QR on a cold phone, and the legibility test in phase 5 |

## Open Questions

- [ ] **iOS Safari + self-signed cert**: after tapping through the warning, do `getUserMedia` and same-origin `fetch` POST both work reliably? **Android Chrome: verified yes (2026-09-21, see Spike Results). iOS: still untested — carried as debt D1.** If iOS fails, fall back to documenting a tunnel.
- [x] **Where does the QR/PIN appear?** Resolved 2026-09-21 (phase 4): `add_phone_camera` returns text + QR image and opens a private local join page on the PC. Original note: The server runs over stdio under Codex — it has no terminal. Recommended: an MCP tool (e.g. `add_phone_camera`) that returns URL + PIN + QR image *and* opens a join page in the PC's browser via stdlib `webbrowser`. Needs a check of how Codex voice mode renders image tool results.
- [x] **Cert generation dependency**: resolved 2026-09-21 — `cryptography>=42` (cross-platform; prototype verified). `openssl` shell-out rejected: absent on stock Windows. Decided in the phase 2+3 plan on the standing recommendation; user may still veto.
- [x] **Port conflict**: resolved in phases 2-4 — fixed port, log and disable on conflict, and the tool's error text tells the agent to set `FRAMEGRAB_PHONE_CAMERAS_PORT`. Original note: two stdio clients spawn two servers. Decide: fixed port + log-and-disable on conflict, or free port (QR changes each run, breaks phone reconnect).
- [ ] **iOS hi-res ceiling**: (phase 5 uses the max-resolution video frame on every platform, no `ImageCapture`; so iOS and Android take the same path — the question reduces to debt D1.) iOS Safari has no `ImageCapture.takePhoto()`; hi-res is limited to the max `getUserMedia` video resolution drawn to canvas. Is that enough for part markings? Measure in Phase 5.
- [ ] Does "other people" include shared/makerspace networks? PIN is in v1 regardless, but this affects how strict defaults should be.
- [ ] Should `.claude/PRPs/` be committed to this (public, Groundlight-owned) repo?

---

## Users & Context

**Primary User**
- **Who**: A hobbyist/technician doing electronics repair at one or more benches, already using a voice-mode coding/assistant agent (Codex) connected to this MCP server via stdio. Has a spare or current smartphone, no desire to install or pay for a webcam app.
- **Current behavior**: Stops work, picks up phone, photographs the board, transfers/uploads the image to the agent, resumes.
- **Trigger**: Mid-repair, hands occupied, wants the AI to look at a component, a solder joint, a marking, or a meter reading.
- **Success state**: Says "look at the left bench" and the agent sees it.

**Job to Be Done**
When I'm mid-repair with both hands busy, I want the AI to look at the board itself, so I can keep working without stopping to take and upload photos.

**Non-Users**
Streamers/OBS users (use VDO.Ninja); security/surveillance; remote-over-internet cameras; production-line visual inspection (Groundlight's core product, not this feature); users who already own and are happy with USB/RTSP cameras (already served by `create_framegrabber`).

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | HTTPS server embedded in the MCP process, self-signed cert auto-generated on first run | Mobile browsers require a secure context for `getUserMedia`; user chose self-signed (offline, no accounts) |
| Must | Phone page: grant camera, enter name, send stills | The core flow. Vanilla HTML/JS, no build step |
| Must | Phone cameras appear in `list_framegrabbers` and work with `grab_frame` | Agents need no new tools to *use* a camera |
| Must | QR code to join | Nobody types `https://192.168.1.37:8443` on a phone |
| Must | Access PIN | Trust boundary: anything on the LAN can otherwise register cameras or probe the server |
| Must | Front/back camera picker + torch toggle | Back camera is the useful one; torch for dark enclosures (Android Chrome only — iOS Safari lacks torch constraint; hide the button there) |
| Must | On-demand hi-res capture | Reading part markings needs pixels, not fps. `grab_frame` flags a request; phone's next poll sees it, captures full-res, POSTs it |
| Must | Input validation: name charset/length, max upload size, PIN attempt limiting | Trust boundary |
| Should | Wake Lock + visible "camera live" state + auto-reconnect using name remembered in `localStorage` | 2-hour survival target; cameras vanish when Codex restarts the stdio process |
| Should | Stale-frame handling: `grab_frame` errors (with last-seen age) if the phone hasn't posted in N s | A silent stale frame is worse than an error for repair work |
| Could | Rename / remove camera from the phone page | `release_grabber` already covers removal agent-side |
| Could | Flag to disable TLS for use behind a user-supplied tunnel/reverse proxy | Power users; trivial but not needed for hypothesis |
| Won't | WebRTC, WebSocket transport, audio, recording, long-running HTTP MCP mode | See "What We're NOT Building" |

### MVP Scope

Phases 1-4 below: one phone, self-signed HTTPS, QR + PIN, name it, `grab_frame` returns a recent preview frame. That is enough to test the 60-second and "zero manual photos" parts of the hypothesis. Hi-res (Phase 5) tests legibility; the 2-hour soak (Phase 6) tests survival.

### User Flow

1. User tells agent "add a phone camera" → agent calls join tool → PC browser shows QR + PIN (agent can also read the PIN aloud).
2. Scan QR on phone → tap through cert warning (first time only) → enter PIN.
3. Allow camera → pick back camera → type "left bench" → Start.
4. Prop the phone, plug in charger.
5. "Look at the left bench — what's the marking on U3?" → agent calls `grab_frame("left bench")` → hi-res still → answer.

---

## Technical Approach

**Feasibility**: HIGH (conditional on Phase 1 spike)

**Architecture Notes**
- Codebase is one file, `framegrab_mcp_server.py` (219 lines): stdio FastMCP, global `_grabber_cache: dict[name → FrameGrabber]` (line 30), `grab_frame` at line 101, lifespan hook at line 34 for start/stop.
- Web server runs in the same process (shares the registry), started from `app_lifespan`. `starlette` 0.46.1 and `uvicorn` 0.34.0 are already locked as transitive deps of `mcp` — no new web framework. stdout is the MCP channel: the web server must log to stderr only.
- Transport: phone POSTs JPEG (canvas → `toBlob`) ~1/s at preview size. POST response body carries `{"hires_requested": bool}` — this piggy-backed poll is the server→phone channel, so no WebSocket/SSE. Latency ≤ one poll interval.
- Hi-res: `ImageCapture.takePhoto()` where available (Android Chrome), else max-resolution canvas grab (iOS).
- Registry integration: phone cameras need `grab()` → ndarray (decode with existing `cv2`) plus `release()` and `config` to fit the existing tools without branching every tool. Exact shape is a plan-phase decision; `set_config` likely doesn't apply.
- `mcp` is locked at 1.6.0. No bump needed for the stdio design.
- Phone page: single static HTML file shipped in the package. QR rendering: TBD in plan — small Python lib vs. inline JS on the PC join page.

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| iOS Safari refuses camera or POST on self-signed origin | M | Phase 1 spike on a real iPhone before building anything. Fallback: `--no-tls` + documented tunnel |
| Phone sleeps / tab backgrounded → camera stops | H | Wake Lock API (iOS 16.4+, Android Chrome), visible live indicator, stale-frame error in `grab_frame`, charger in docs |
| Cert warning scares "other people" | M | Join page on PC explains the warning with a screenshot; warning is one-time per phone |
| Port already in use (second stdio spawn) | M | Log to stderr, disable phone feature in that process, surface in join tool's error |
| Server restarts (Codex exit) drop all cameras | H | Phone auto-reconnects with remembered name; fixed port + persisted cert so URL and trust survive |
| Hi-res on iOS insufficient for markings | M | Measure in Phase 5; mitigation is physical (move phone closer / macro lens), documented |
| LAN device registers junk / floods uploads | L | PIN, attempt limiting, upload size cap, camera count cap |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | TLS spike | Throwaway: self-signed HTTPS page doing `getUserMedia` + `fetch` POST, tested on real iPhone + Android | complete (Android only — iOS is debt D1) | - | - | [plan](../plans/completed/phone-cameras-tls-spike.plan.md) · [report](../reports/phone-cameras-tls-spike-report.md) |
| 2 | Ingest server + registry | Embedded HTTPS server, cert generation/persistence, frame POST endpoint, phone cameras visible to `list_framegrabbers`/`grab_frame`, validation, stale-frame error | complete (Android verified) | with 3 | 1 | [plan](../plans/completed/phone-cameras-ingest-and-page.plan.md) · [report](../reports/phone-cameras-ingest-and-page-report.md) |
| 3 | Phone page | Static page: permission, name, front/back picker, torch, preview, wake lock, send loop, reconnect | complete (Android verified) | with 2 | 1 | [plan](../plans/completed/phone-cameras-ingest-and-page.plan.md) · [report](../reports/phone-cameras-ingest-and-page-report.md) |
| 4 | Join flow | PIN auth, QR code, MCP join tool + PC join page | complete (Android verified; stopwatch run pending) | - | 2, 3 | [plan](../plans/completed/phone-cameras-join-flow.plan.md) · [report](../reports/phone-cameras-join-flow-report.md) |
| 5 | On-demand hi-res | Request flag in POST response, full-res capture on phone, `grab_frame` waits for it with timeout → falls back to preview | complete (Android verified; legibility test pending) | - | 4 | [plan](../plans/completed/phone-cameras-hires-on-demand.plan.md) · [report](../reports/phone-cameras-hires-on-demand-report.md) |
| 6 | Docs + field validation | README section, 60-second and 2-hour tests, legibility test, try with one other person | in-progress | - | 5 | [plan](../plans/phone-cameras-field-validation.plan.md) |

### Phase Details

**Phase 1: TLS spike**
- **Goal**: Kill or confirm the biggest risk for ~1 hour of effort.
- **Scope**: Throwaway script, not merged. Self-signed cert, one HTML page, one POST endpoint. Test iOS Safari and Android Chrome.
- **Success signal**: A JPEG from each phone lands on the PC. Record exact taps needed on iOS. Decide `cryptography` vs `openssl`.

**Phase 2: Ingest server + registry**
- **Goal**: A `curl -k` POST of a JPEG under a name makes `grab_frame(name)` return it.
- **Scope**: Server lifecycle in `app_lifespan`, cert persisted in a user data dir, POST endpoint, name/size validation, registry integration, stale-frame error, stderr-only logging, port-conflict handling. One test file covering ingest → grab and validation rejects.
- **Success signal**: Test passes; existing USB/RTSP tools unaffected.

**Phase 3: Phone page**
- **Goal**: A real phone streams stills under a chosen name.
- **Scope**: Single static HTML/JS file against the Phase 2 HTTP contract (agree the contract first: endpoint, fields, response JSON). Camera picker, torch where supported, wake lock, live indicator, `localStorage` name + auto-reconnect.
- **Success signal**: Agent describes what the phone sees via `grab_frame`.

**Phase 4: Join flow**
- **Goal**: Nobody types a URL; strangers on the LAN can't join.
- **Scope**: PIN generated per server start, checked on every POST (session token after first success), attempt limiting; MCP tool returning URL/PIN/QR and opening the PC join page.
- **Success signal**: Cold phone → first frame < 60 s, stopwatch-verified on both platforms. Wrong PIN rejected.

**Phase 5: On-demand hi-res**
- **Goal**: Part markings are legible to the agent.
- **Scope**: Request flag, phone full-res capture path per platform, `grab_frame` wait-with-timeout and fallback to latest preview.
- **Success signal**: SOIC-8 marking test passes on at least one platform; latency < 4 s.

**Phase 6: Docs + field validation**
- **Goal**: Test the hypothesis, not just the code.
- **Scope**: README section with cert-warning screenshots; run the success-metric tests; one other person sets it up unaided.
- **Success signal**: Metrics table filled with real numbers; PRD status updated.

### Parallelism Notes

Phases 2 and 3 can run in parallel once the HTTP contract (endpoint path, form fields, response JSON) is written down — server can be tested with `curl`, page against a stub. Everything else is sequential; the project is small enough that parallelism is optional.

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| Frame transport | JPEG stills via `fetch` POST | WebRTC (aiortc), WebSocket, MJPEG | Agent consumes stills; no heavy deps; WebSocket unreliable on iOS with self-signed certs |
| Server→phone signalling | Flag in POST response | WebSocket, SSE | Reuses the existing loop; ≤1 s latency is fine |
| HTTPS | Auto-generated self-signed cert | BYO tunnel, both | User choice: offline, no accounts, LAN-only. `--no-tls` deferred to Could |
| MCP transport | Stay stdio, web server in-process | Long-running streamable-HTTP MCP (needs `mcp` ≥1.8) | User connects Codex via stdio; avoids dep bump. Cost: cameras drop on agent restart → mitigated by phone auto-reconnect |
| Agent-facing API | Reuse `list_framegrabbers` / `grab_frame` | New `phone_*` tool family | Agents already know these; one new tool only for joining |
| v1 scope | QR, PIN, front/back + torch, on-demand hi-res all in | Defer hi-res/torch | User choice. Phased so MVP (1-4) is testable before hi-res lands |

---

## Research Summary

**Market Context**
- Camera MCP servers (PC-attached only): [videocapture-mcp](https://github.com/13rac1/videocapture-mcp), [webcam_mcp](https://github.com/pavel-kirienko/webcam_mcp), [OpticMCP](https://glama.ai/mcp/servers/Timorleiderman/OpticMCP).
- Closest competitor: [yunleng](https://github.com/ChenLaoshiYF/yunleng) — phone via IP Webcam *app* + RTSP URL; MIT, 1 star, 15 commits.
- Browser-only phone camera: [VDO.Ninja](https://docs.vdo.ninja/getting-started) — WebRTC, aimed at OBS; Python ingest via [raspberry_ninja](https://github.com/steveseguin/raspberry_ninja) (GStreamer).
- Gap: browser-only + named + MCP-exposed does not exist.

**Technical Context**
- `getUserMedia` requires a secure context (HTTPS or localhost): [MDN](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia). iOS is the strictest platform for local certs: [Apple forums](https://developer.apple.com/forums/thread/705838).
- Verified in repo: single-file stdio FastMCP server; `mcp` 1.6.0, `starlette` 0.46.1, `uvicorn` 0.34.0 in `uv.lock`; no TLS/cert library locked.
- Not verified (inferred from general knowledge): iOS Safari behaviour with self-signed certs for `fetch`/`wss`; torch and `ImageCapture` availability on iOS; how Codex voice mode renders image tool results.

**Spike Results — 2026-09-21** (script: `.claude/PRPs/plans/assets/tls_spike.py`; Android checks driven over adb, 244 frames received)

| Check | Android (Pixel 11 Pro XL, Android 17, Chrome 153) | iOS Safari |
|---|---|---|
| `isSecureContext` after cert bypass | true | NOT TESTED |
| `getUserMedia` back camera | works | NOT TESTED |
| `fetch` POST frames → 200 | works, steady ~1/s | NOT TESTED |
| Frame from canvas | 2160×4080, ~460 KB JPEG q0.85; fibre-level detail at close focus | |
| Track max (capabilities) | 4080×3064 | |
| `torch` capability / `ImageCapture` / `wakeLock` | yes / yes / ok | |
| Keeps posting while Chrome backgrounded (HOME) | yes, 12 frames in 15 s | |
| Keeps posting with screen off 30 s | yes, live frames (42 distinct sizes of 45) — **confounded**: phone was on USB with `stay_on_while_plugged_in=2` and no secure lock; repeat on Wi-Fi + charger only | |
| Cert warning again on reopening URL | no | |
| Permission prompt again on reopening URL | no (streamed on first tap) | |
| Taps from QR to first frame | not measured (user opened the page before measurement) | |
| IC marking readable at ~15 cm | not measured (phone was lying on a table) | |

Findings that change later phases:
- **Camera is exclusive per tab in Chrome Android**: a second tab's `getUserMedia` silently pends (no error, no prompt) until the first tab closes. Phase 3 must handle "already open in another tab" — e.g. show a hint if `getUserMedia` hasn't resolved in ~5 s.
- **Start button must be single-shot**: a second tap re-assigns `srcObject` and aborts the first `play()` with `AbortError`, killing the send loop. Phase 3: disable the button on first tap.
- **Full-res canvas frames are cheap enough on Android** (~460 KB). Phase 2 should still cap preview size (e.g. long edge 1600) and keep full-res for on-demand; on Android, phase 5 may not need `ImageCapture` at all.
- Cert-generation recommendation (Open Question 3, not yet decided by user): `cryptography` in phase 2 — spike's `openssl` shell-out worked but isn't available on stock Windows.

---

*Generated: 2026-09-21*
*Status: DRAFT - needs validation*
