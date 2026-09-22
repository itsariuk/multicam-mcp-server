# Implementation Report: Phone Cameras — Ingest Server + Phone Page

## Summary
Opt-in HTTPS server inside the MCP process (`ENABLE_FRAMEGRAB_PHONE_CAMERAS=true`). A phone opens the page, names itself, and POSTs JPEG stills; it then appears in the existing `_grabber_cache`, so `list_framegrabbers`, `grab_frame`, `get_framegrabber_config`, and `release_grabber` work on it with no tool changes. Verified end to end over a real MCP stdio session against a Pixel 11 Pro XL (Android 17, Chrome 153).

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Confidence | 8/10 server, 6/10 page | Server: passed first run. Page: worked first run on device; one small fix after (see Issues) |
| Files Changed | 6 (+ lockfile) | 6 + `uv.lock` |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 0 | Branch | Complete | `feat/phone-cameras` |
| 1 | Dependencies + packaging | Complete | cryptography 50.0.1 locked |
| 2-5 | Tests + `PhoneCamera` + app | Complete | Deviated: all tests written in one pass, confirmed failing (collection error), then the module — not one test at a time |
| 6 | Cert + threaded TLS server | Complete | |
| 7 | Wire into MCP lifespan | Complete | Tool-integration test passed **before** any server-module change, confirming the duck-typing |
| 8 | Smoke test vs real process | Complete | Done through a real stdio MCP client (scratch script), not just curl |
| 9 | Phone page | Complete | |
| 10 | On-device validation | Complete | See table below. Driven over adb |
| 11 | README | Complete | States no-auth and iOS-untested plainly |
| 12 | Packaging check | Complete | Wheel has 3 files; installed wheel imported from site-packages finds the page |
| 13 | Format, lint, tests | Complete | See Validation |

## On-device results (Task 10)

| Check | Result |
|---|---|
| Name appears in `list_framegrabbers`; `grab_frame` returns the phone's current view | Pass — 2160×4080, ~490 KB, **130-190 ms** per grab (PRD target < 2 s) |
| Second tap on Start ignored | Pass (double-tapped; button disabled, stream fine) |
| 2nd tab + Start → "open in another tab?" hint | Pass (shown at ~5 s); name was prefilled from the previous session |
| Camera picker | Pass, but lists only "camera 0, facing back" / "camera 1, facing front" — Chrome exposes the Pixel's rear lenses as one logical camera. Front ↔ back switching verified by grabbed frame size (2160×3440 vs 2160×4080) |
| Torch | Pass — mean frame brightness 128.6 → 146.5 with torch on |
| `release_grabber` → page stops | Pass — "Released by the agent. Tap Start to rejoin."; frame POST gets 410 |
| Start after release → back in list | Pass |
| Server restart, phone untouched | Pass — back in `list_framegrabbers` and grabbable within ~10 s; stored cert reused, no new warning |
| Page closed → stale error | Pass — "Phone camera 'left bench' last sent a frame 15s ago. Is the page still open on the phone?" reached the MCP client as a tool error |
| Permission denied flow | **Not tested** (permission was already granted on this phone) |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass for new files | `ruff format` clean; `ruff check` clean on `framegrab_mcp_phone.py` and `test_phone_cameras.py`. `framegrab_mcp_server.py` reports 12 findings under this machine's ruff rule set — 11 are in pre-existing code and were left alone; 1 (`logger.error(..., exc_info=True)`) is new and deliberately mirrors the existing line above it |
| Unit Tests | Pass | 17 passed (11 test functions, one parametrised ×7) |
| Build | Pass | Wheel contains `framegrab_mcp_server.py`, `framegrab_mcp_phone.py`, `framegrab_mcp_phone.html` |
| Integration | Pass | Real stdio MCP session + curl + Pixel |
| stdout purity | Pass | 0 bytes on stdout with the feature on and off |
| Edge Cases | Pass except "permission denied" (untested) and "two phones, same name" (accepted until phase 4) |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `framegrab_mcp_phone.py` | CREATED | ~250 |
| `framegrab_mcp_phone.html` | CREATED | ~195 |
| `test_phone_cameras.py` | CREATED | ~125 |
| `framegrab_mcp_server.py` | UPDATED | +17 |
| `pyproject.toml` | UPDATED | +16 / -1 |
| `README.md` | UPDATED | +30 |
| `uv.lock` | UPDATED (generated) | includes format churn from a newer uv (lock revision 2→3) |

## Deviations from Plan
- Tests written in one pass rather than one-failing-test-at-a-time. Same tests, same red→green proof, fewer round trips.
- Added `test_page_is_served` and a non-object JSON case (`["name"]`) beyond the plan's list.
- `_register` catches `(ValueError, KeyError, TypeError)` instead of the plan's `except Exception` — narrower, same behaviour for every tested input.
- Task 8/10 used a scratch stdio MCP client instead of the MCP inspector: scriptable, and it is the transport Codex actually uses.

## Issues Encountered
- Camera picker stayed enabled after a release (could turn the camera on with nothing sending). Fixed: `stopAll()` now disables it. **This one-line fix was not re-verified on the phone.**
- `ruff --isolated` still reported the extended rules, so the uvx ruff version's defaults appear broader than expected; did not investigate further.
- Pre-existing, unrelated: the stdio server does not exit on stdin EOF (same with the feature off).

## Observations for later phases
- A closed phone page leaves its name in `list_framegrabbers` indefinitely (grab gives the actionable stale error). Acceptable now; phase 4 could drop cameras after N minutes of silence, or leave it.
- No lens choice on Android Chrome (one logical back camera). For macro work the phone picks the lens itself. Relevant to phase 5 legibility.
- Machine-local side effect by design: certificate + key stored in the platform user-data dir (`~/.local/share/framegrab-mcp-server/` here).

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `test_phone_cameras.py` | 17 | contract (register/frame, all status codes), name validation, size cap, camera cap, stale + no-frame errors, release/rejoin, TLS start/verify/port-conflict/stop, real MCP tool functions on a phone camera |

## Addendum: project renamed (2026-09-21)

At the user's request, made after the work above: `framegrab-mcp-server` → **`multicam-mcp-server`**, keeping credit to the original.

- Renamed: distribution name, console script, modules (`multicam_mcp_server.py`, `multicam_mcp_phone.py`, `multicam_mcp_phone.html`), FastMCP server name (`"multicam"`), cert CN and user-data dir, Makefile, README (incl. config keys). File names in the tables above are the pre-rename names.
- Kept on purpose: tool names, the `framegrabbers` resource, and the `FRAMEGRAB_*` / `ENABLE_FRAMEGRAB_*` env vars (they refer to the `framegrab` library, still the capture backend; renaming them is an API break for no gain), `LICENSE`, the screenshot asset file name.
- Credits: `authors` keeps Groundlight AI first and adds Ilya Tsaryuk; README has a Credits section linking upstream (`groundlight/framegrab-mcp-server`, verified to exist, Apache-2.0); new `NOTICE` states the fork origin and the changes (Apache-2.0 §4). Both LICENSE and NOTICE ship in the wheel.
- README now runs the server via `uvx --from git+https://github.com/itsariuk/multicam-mcp-server multicam-mcp-server`, because the new name is not on PyPI. **That URL only resolves after the GitHub repo is renamed** (not done — outward-facing, user's action).
- Re-validated after the rename: 17 tests pass, ruff clean on new files, console script starts with 0 bytes on stdout, wheel contents correct.
- Not touched: `.github/workflows/publish-package.yaml` (PyPI trusted publishing is bound to Groundlight's project and will not work for the new name as is), `reviewer-lottery.yml`, `autoassign.yaml`, the local directory name, and historical `.claude/PRPs` documents.

## Addendum: code review fixes (2026-09-21)

`/code-review` (medium) reported 7 findings, no high severity. All fixed at the user's request.

| # | Finding | Fix | Verified |
|---|---|---|---|
| 1 | Re-register error ignored on 404 → page claims "Live" forever | `halt(error)` — one function now ends every session (stop sending, free camera, re-enable Start, say why) | By reading; not provoked on device |
| 2 | Failed camera switch re-uploads a frozen frame as fresh | `openCamera` clears the `<video>` before switching; switch failure and `track.onended` both `halt()` | Normal switching re-verified on the Pixel |
| 2b | **Found while testing #2, not in the review:** when another app takes the camera, Chrome keeps the track `live` + unmuted and the `<video>` frozen; the page kept uploading that frame (two grabs 11 s apart were byte-identical). `onended` never fires | Send only when `video.currentTime` has advanced since the last send | On the Pixel via CDP: uploads stop (`sent` frozen at 9), page explains, agent gets the stale error at 12 s, streaming resumes by itself |
| 3 | Corrupt/SAN-less stored cert disables phone cameras forever | Load wrapped in `try`; falls through to regenerate | `test_unusable_stored_cert_is_replaced` |
| 4 | `start()` reports success if the uvicorn thread died; key/cert could mismatch after a crash between two writes | Cert + key now in **one** file (`server.pem`), written to a temp file and `os.replace`d; `start()` returns `None` with an error if HTTPS did not come up, and stops waiting as soon as the thread dies | `test_start_reports_failure_when_https_cannot_come_up` |
| 5 | Torch label stale after camera switch | Label reset in `openCamera` | Screenshots on the Pixel: "Torch on" → "Torch" |
| 6 | pytest not a dev dependency | Added to the `dev` extra; README uses `uv sync --extra dev` / `uv run pytest -q` | Ran it that way |
| 7 | Bad `FRAMEGRAB_PHONE_CAMERAS_PORT` crashed the whole server at import | Parsed inside the guarded start; empty string means default | Reload test ×2; real run with `PORT=abc`: error logged, stdout 0 bytes |

Also added a send-loop session counter so a loop left over from a halted session cannot run alongside a new one.

**Behaviour change to know about:** fix 2b also pauses sending while Chrome is in the background (the video clock does not advance when the page is hidden). The agent gets the stale error rather than a possibly frozen picture, and sending resumes when the page is visible again. The spike had suggested background streaming worked; whether those frames were live was only checked for the screen-off case, not for HOME. This matters for PRD debt D2 (survival): the phone must stay on the page with the screen on — the wake lock covers the normal case. The "background" wording of the on-page message was changed after the device run and not re-checked on the phone.

Tests: 21 passing. Stored certificate file name changed (`cert.pem` + `key.pem` → `server.pem`), so phones see the certificate warning once more.

## Next Steps
- [ ] `/code-review`, then `/prp-commit` (nothing is committed yet)
- [ ] `/prp-plan .claude/PRPs/prds/phone-browser-cameras.prd.md` → phase 4 (PIN + QR + join tool). Do not publish a release advertising this feature before phase 4: the endpoint is unauthenticated
