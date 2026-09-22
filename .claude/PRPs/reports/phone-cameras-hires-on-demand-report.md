# Implementation Report: Phone Cameras — On-demand Hi-res, Small Preview

## Summary
The phone now sends a ~1280-px preview once a second. `grab_frame` sets a request flag, the next preview response carries `hires_requested: true`, the phone posts a full-resolution photo (`?kind=photo`), and the waiting `grab()` returns it. If no photo arrives within `HIRES_TIMEOUT_S` (4 s) the latest preview is returned with a logged warning. Verified on the Pixel over a real stdio MCP session.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Confidence | 8/10 | Everything passed first run on the device; no code fixes after the device checks |
| Files Changed | 4 | 4 (+170 / −37) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 0 | Branch | Complete | `feat/phone-hires` |
| 1 | Failing tests | Complete | 4 new; two older tests now skip the photo wait (they have no phone to answer) |
| 2 | `PhoneCamera` request/wait/fallback | Complete | `_decode` extracted; `?kind=` validated before the body is read |
| 3 | Page | Complete | `capture(longEdge, quality)` + `send(blob, kind)`; status shows photos count; `lastPreviewBytes` kept for the soak |
| 4 | On-device validation | Complete except legibility | see below |
| 5 | README | Complete | |
| 6 | Format, lint, build | Complete | 34 tests; stdout 0 bytes; wheel unchanged in shape |

## On-device results (Task 4)

| Check | Result |
|---|---|
| Join, idle 10 s, then `grab_frame` | Pass: 2160×4080 returned; `photos` 0 → 1; `sent` kept counting |
| 5 × `grab_frame` latency | **1386, 931, 1382, 526, 1110 ms** (PRD target < 4 s); all five 2160×4080 |
| Preview size | canvas 678×1280 as designed; **6 KB per preview, but the lens was covered** (phone face-down): the scene was black, so the byte figure is not representative. Photos were ~160 KB for the same reason |
| Idle upload, 60 s, no grabs | 54 previews, 0 photos |
| Fallback | Scratch server with a 10 ms timeout against the live page: warning logged, 678×1280 preview returned. The page also rejoined that server by itself (404 → token re-register), which re-proves the restart path |
| **Legibility (PRD D3)** | **Not done: needs the user** to point the phone at a PCB from ~15 cm |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | ruff format + check clean |
| Unit Tests | Pass | 34 (4 new): photo round-trip from a thread, fallback + warning, no wait on a dead page, photo path validation |
| Build | Pass | 3 `multicam_mcp_*` files |
| Integration | Pass | real stdio session + Pixel |
| stdout purity | Pass | 0 bytes |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `multicam_mcp_phone.py` | UPDATED | +50 / −11 |
| `multicam_mcp_phone.html` | UPDATED | +76 / −22 |
| `test_phone_cameras.py` | UPDATED | +74 / −2 |
| `README.md` | UPDATED | +7 / −2 |

## Deviations from Plan
- Two existing tests (`test_register_post_grab_roundtrip`, `test_grab_frame_tool_serves_phone_camera`) set `HIRES_TIMEOUT_S = 0` — without it the suite took 9 s (two silent 4 s fallbacks).
- `send()` returns `{}` (not `null`) for "server rejected" so the loop keeps going; the plan's wording left that open.

## Issues Encountered
- The phone was lying lens-down during the run, so all images were black. Every check that depends on content (preview bytes, legibility) is unmeasured or unrepresentative; every structural check passed.

## Observations
- Preview bytes on a real scene should be measured during the legibility test (read `lastPreviewBytes` over CDP); expected tens of KB.
- The MCP loop is blocked for the wait (≤ 4 s) — by design, single stdio client.

## Addendum: code review fixes (2026-09-21)

`/code-review` (medium) reported 5 low findings, no blocking bugs. Fixed at the user's request:

| # | Finding | Fix | Verified |
|---|---|---|---|
| 1 | Race: a late photo landing as a new `grab()` resets its state could leave that grab waiting for a request the phone never sees | The three handshake fields are changed under one lock; `put(photo)` drops a photo when nobody is waiting | `test_late_photo_is_dropped_and_undecodable_photo_falls_back` |
| 2 | `grab()` blocks the MCP loop ≤ 4 s | Not changed: by design (single stdio client), documented in the plan; `anyio.to_thread` in `grab_frame` is the upgrade if needed | — |
| 3 | Undecodable photo raised instead of falling back to the preview | `try/except RuntimeError` around the photo decode, warning logged, preview returned | same test (thread posts a bad photo while `grab()` waits) |
| 4 | `lastPreviewBytes` unused; `photos` not reset on Start | `photos` reset on Start; `lastPreviewBytes` kept with a comment saying it is read over remote debugging for soak tests | by reading |
| 5 | Camera switch between preview and photo → empty POST → 400 → 4 s stall | Photo capture skipped when `video.videoWidth` is 0; the request stays pending and the next tick answers it | by reading; not provoked on device |

Tests: 35 passing. The page changes (4, 5) are small and were not re-run on the Pixel.

## Next Steps
- [ ] User: legibility test (D3) — point at a PCB, `grab_frame`, check a SOIC-8 marking; also read `lastPreviewBytes`
- [ ] `/code-review`, `/prp-commit`, merge, push
- [ ] `/prp-plan` → phase 6 (docs + field validation: 2-hour soak, stopwatch run, one other person)
