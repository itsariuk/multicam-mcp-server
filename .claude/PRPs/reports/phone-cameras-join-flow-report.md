# Implementation Report: Phone Cameras — Join Flow (PIN, QR, join tool)

## Summary
Joining a phone camera now needs the PIN shown on the computer; after joining, the phone holds an HMAC token for its camera name that survives page reloads and server restarts. A new `add_phone_camera` tool returns the link, the PIN and a QR code (`https://<ip>:8443/#pin=<pin>`), and opens a private join page on the computer. Verified end to end over a real stdio MCP session against the Pixel 11 Pro XL.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Confidence | 8/10 | Server code and tool passed first run; page passed first run on device; no code fixes were needed after the device checks |
| Files Changed | 5 | 5 (+450 / −66) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 0 | Branch | Complete | `feat/phone-join-flow` |
| 1 | Fixture authenticates; tests go red | Complete | 18 failed for the expected reasons |
| 2 | `_write_private` + `_load_secret` | Complete | `_ensure_cert` refactored onto the helper |
| 3 | PIN + token auth | Complete | Order: 422 → token → PIN (lockout, missing-PIN-not-counted) → conflict/cap/create |
| 4 | QR + join page | Complete | OpenCV encoder, no new dependency; page rendered in headless Chrome and checked visually |
| 5 | Opener without stdout | Complete | `Popen` with all three streams on `DEVNULL`; xdg-open → google-chrome here |
| 6 | `add_phone_camera` tool | Complete | Registered even when phone cameras are off; error text names the env var and the port |
| 7 | Phone page | Complete | PIN field, `#pin=` prefill + `replaceState`, token in `localStorage`, `Authorization` on every request, 401 → `halt()` |
| 8 | On-device validation | Complete except the human stopwatch | see below |
| 9 | README | Complete | "no authentication yet" removed; PIN/token/lockout/sign-out explained |
| 10 | Format, lint, build | Complete | 30 tests pass; wheel unchanged in shape |

## On-device results (Task 8)

| Check | Result |
|---|---|
| `add_phone_camera` over stdio | Pass: text with link + PIN, plus a 5 KB PNG; session healthy afterwards (`list_framegrabbers` still answers); stdout 0 bytes |
| Returned PNG decodes | Pass: `https://<server-ip>:8443/#pin=<redacted>` |
| Join page opens on the PC | Pass: `join.html` written `0600`; opener spawned without warnings; rendered page shows QR, PIN, three steps |
| Open the join URL as a scanner would | Pass: PIN field prefilled, address bar shows `https://<server-ip>:8443/` with no fragment |
| Start → live, `grab_frame` works | Pass; `token:left bench` stored in `localStorage` |
| Unauthenticated `curl` | Pass: frame POST 401 (same for an unknown name), join without PIN 401 "Enter the PIN…", wrong PIN 401 "Wrong PIN." |
| Server restart (new PIN redacted), phone untouched | Pass: rejoined by token, `grab_frame` works |
| Fresh tab, new name: no PIN / wrong PIN / right PIN | Pass: "Enter the PIN shown on the computer." / "Wrong PIN." / live as `right bench`; both names listed |
| Automated timing, token cleared, cert already accepted | 1.1 s from opening the link to the first frame |
| **Human stopwatch** (scan the on-screen QR with the camera app, cold phone, cert warning included) | **Not done: needs the user.** PRD debt D3 stays open |

Observations:
- **Frame rate dropped to ~0.2 fps for a while** (3 frames in 15 s; ping to the phone averaged 49 ms) after the phone had been asleep, versus ~0.7–1 fps earlier in the day. Not caused by this change (auth adds one HMAC per request). Relevant to PRD D2 and to phase 5: a smaller preview would suffer far less from a weak Wi-Fi link.
- A tab whose `getUserMedia` is pending behind another tab does **not** recover when that tab closes; it needs a reload. Same as the phase 2-3 finding; the hint text already tells the user what is wrong.
- The phone's screen had gone dark between checks once; taps landed on nothing until `KEYCODE_WAKEUP`. Test-harness issue, not a product one.

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | ruff format + check clean on the three Python files |
| Unit Tests | Pass | 30 (was 21): 9 new, all existing ones now authenticate |
| Build | Pass | wheel: 3 `multicam_mcp_*` files + LICENSE + NOTICE |
| Integration | Pass | real stdio session + curl + Pixel |
| stdout purity | Pass | 0 bytes with the feature on |
| Edge Cases | Pass | non-ASCII PIN/token, int PIN, missing PIN not counted, lockout + recovery, corrupt key file, token for another name, unknown name gives the same 401 |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `multicam_mcp_phone.py` | UPDATED | +189 / −20 |
| `multicam_mcp_server.py` | UPDATED | +35 / −4 |
| `multicam_mcp_phone.html` | UPDATED | +35 / −5 |
| `test_phone_cameras.py` | UPDATED | +237 / −40 |
| `README.md` | UPDATED | +20 / −7 |

## Deviations from Plan
- Tests were written in one pass (red for 18, then green), not one at a time.
- Added `test_missing_pin_is_not_counted_as_a_wrong_guess` and non-ASCII header/PIN cases beyond the plan.
- Lambdas in tests became `def`s (ruff E731).
- `write_join_page` uses `self.url or ""` so it cannot raise on an unstarted server; the tool never calls it unstarted anyway.

## Issues Encountered
- Shell quoting of the CDP result broke one adb tap command (tapped nothing); redone with fixed coordinates.
- Phone screen timed out mid-run; woken with `KEYCODE_WAKEUP`.

## Next Steps
- [ ] User: stopwatch run (scan QR from the join page → first frame) to close D3
- [ ] `/code-review`, `/prp-commit`, merge to `main`, push
- [ ] `/prp-plan` → phase 5 (on-demand hi-res + small preview)
