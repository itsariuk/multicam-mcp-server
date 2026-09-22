# Plan: Phone Cameras — TLS Spike

## Summary
Throwaway experiment to confirm or kill the biggest risk in the Phone Browser Cameras PRD: that a phone browser, after tapping through a self-signed certificate warning, can use `getUserMedia` and `fetch`-POST JPEG frames to a LAN server. The spike script is already written and its server side is verified; what remains is running it against a real iPhone and a real Android phone and recording the results in the PRD.

## User Story
As the developer of the phone-camera feature, I want hard evidence of how iOS Safari and Android Chrome behave on a self-signed LAN origin, so that I don't build phases 2-6 on an assumption.

## Problem → Solution
"Believed to work, not verified" (PRD Open Question 1) → a filled-in results table per platform + a go/no-go decision on self-signed HTTPS.

## Metadata
- **Complexity**: Small
- **Source PRD**: `.claude/PRPs/prds/phone-browser-cameras.prd.md`
- **PRD Phase**: 1 — TLS spike
- **Estimated Files**: 1 throwaway script (already exists), 1 PRD update. Nothing merged into the package.

---

## UX Design

N/A — throwaway experiment. The spike page is a button, a `<video>`, and an on-screen log.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `.claude/PRPs/plans/assets/tls_spike.py` | all | The spike. Stdlib-only: `http.server` + `ssl`, cert via `openssl` CLI, inline HTML/JS |
| P1 | `.claude/PRPs/prds/phone-browser-cameras.prd.md` | Open Questions, Technical Risks | What the spike must answer |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Secure context | https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia | `navigator.mediaDevices` is `undefined` on insecure origins — the page logs `isSecureContext` first so this failure is distinguishable from a permission denial |
| iOS local certs | https://developer.apple.com/forums/thread/705838 | iOS is strictest; cert must carry a SAN (the spike adds `IP:<lan-ip>`) |

```
KEY_INSIGHT: iOS requires `playsinline` on <video> or it goes fullscreen / fails to play inline.
APPLIES_TO: spike page and the real phone page (PRD phase 3)
GOTCHA: also needs `muted` + a user gesture for autoplay — hence the Start button.

KEY_INSIGHT: stdlib http.server with a TLS-wrapped listening socket does the handshake inside accept(); one stalled client blocks all others.
APPLIES_TO: spike only. Do NOT carry this server into phase 2 — use uvicorn (already locked) there.
GOTCHA: if the phone "hangs" on connect, restart the spike before concluding anything.
```

---

## Patterns to Mirror

Not applicable — the spike is deliberately outside the package and follows no codebase pattern. Two things it *does* respect because phase 2 will need them:

### LOGGING → stderr only
```python
# SOURCE: .claude/PRPs/plans/assets/tls_spike.py (Handler.do_POST)
print(f"FRAME {len(body)}B from {self.client_address[0]}", file=sys.stderr)
```
In the real server stdout is the MCP stdio channel (`framegrab_mcp_server.py:215`, `mcp.run()`); anything printed there corrupts the protocol.

### TRUST BOUNDARY → size cap + magic-byte check
```python
# SOURCE: .claude/PRPs/plans/assets/tls_spike.py (Handler.do_POST)
if not 0 < length <= MAX_BODY:
    return self._reply(413)
...
if body[:2] != b"\xff\xd8":
    return self._reply(400, b"not a jpeg")
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `spike/tls_spike.py` | CREATE (copy of `.claude/PRPs/plans/assets/tls_spike.py`) | Run location; writes `cert.pem`, `key.pem`, `frames/` beside itself. Never committed |
| `.git/info/exclude` | UPDATE | Add `spike/` — keeps it out of `git status` without touching the committed `.gitignore` |
| `.claude/PRPs/prds/phone-browser-cameras.prd.md` | UPDATE | Record results, resolve Open Questions 1, 3, 5; mark phase 1 complete |

## NOT Building

- WebSocket/`wss` test — design already avoids WebSocket; adds a dependency to a stdlib-only spike.
- PIN, naming, QR in-page, reconnect, torch *toggle* — phases 3-4. The spike only *detects* torch/ImageCapture/wakeLock support.
- Tests, packaging, README — it's throwaway.
- Any change to `framegrab_mcp_server.py` or `pyproject.toml`.

---

## Step-by-Step Tasks

### Task 1: Place the spike
- **ACTION**: `mkdir -p spike && cp .claude/PRPs/plans/assets/tls_spike.py spike/ && echo 'spike/' >> .git/info/exclude`
- **VALIDATE**: `git status --short` shows nothing under `spike/`.

### Task 2: Start it and re-verify the server side
- **ACTION**: `python3 spike/tls_spike.py` (foreground, separate terminal). It prints `https://<lan-ip>:8443/` and, if `qrencode` is installed (it is on this machine), a terminal QR code.
- **GOTCHA**: Needs `openssl` on PATH for first-run cert generation (present: OpenSSL 3.6.4). If the LAN IP changes (different Wi-Fi), delete `spike/cert.pem spike/key.pem` so the SAN is regenerated.
- **GOTCHA**: No firewall is active on this machine (checked: ufw/firewalld/nftables/iptables inactive). On another PC, open TCP 8443.
- **VALIDATE** (already passed once during planning, 2026-09-21):
  ```bash
  curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:8443/                      # 200
  printf '\xff\xd8x' | curl -sk -w '%{http_code}\n' --data-binary @- https://localhost:8443/frame   # ok200
  curl -sk -w '%{http_code}\n' --data-binary 'nope' https://localhost:8443/frame         # not a jpeg400
  ```

### Task 3: Android Chrome run
- **ACTION**: Phone on the same Wi-Fi. Scan QR → "Your connection is not private" → Advanced → Proceed. Tap **Start camera**, allow permission. Leave 60 s.
- **RECORD**: number of taps from scan to first `sent … -> 200` line; the `REPORT` JSON from the PC's stderr (resolution, `maxW/maxH`, `torch`, `imageCapture`, `wakeLock`); whether `spike/frames/latest.jpg` opens and is a sharp back-camera image.
- **VALIDATE**: `FRAME …B from <phone-ip>` lines appear ~1/s on the PC.

### Task 4: iOS Safari run
- **ACTION**: Same, in Safari (not an in-app browser). Warning → Show Details → "visit this website" → confirm.
- **RECORD**: same as Task 3, plus the first on-page line `isSecureContext=… mediaDevices=…`.
- **GOTCHA**: If `mediaDevices=false`, Safari did not grant a secure context after the bypass — that is the **no-go** signal. If `getUserMedia` works but POSTs fail (`POST ERR`), note the exact message: that's a partial failure with a different fix.
- **VALIDATE**: as Task 3.

### Task 5: Survival + reload checks (both phones)
- **ACTION**: (a) Lock the screen for 30 s, unlock — do frames resume without a tap? (b) Switch apps and return. (c) Close the tab, reopen the URL — is the cert warning shown again? Is camera permission asked again? (d) Leave one phone on a charger for 15 min with the page open — does the screen stay on (`wakeLock: ok`)?
- **RECORD**: yes/no per item per platform. This feeds PRD phase 3 (reconnect logic) and the 2-hour metric.

### Task 6: Legibility sanity check
- **ACTION**: Point each phone at a PCB from ~15 cm; open `spike/frames/latest.jpg` on the PC.
- **RECORD**: frame resolution and whether an IC top marking is human-readable. Answers PRD Open Question 5 (is canvas-from-video enough on iOS, or is `ImageCapture` needed where available).

### Task 7: Write results into the PRD
- **ACTION**: In the PRD: add a "Spike Results (date)" table under Research Summary; tick/resolve Open Questions 1 and 5; decide Open Question 3 (see Notes); set phase 1 status `complete`; if no-go on iOS, change the HTTPS decision in the Decisions Log to "`--no-tls` + documented tunnel" and promote that row from Could to Must.
- **VALIDATE**: PRD has no remaining "believed, not verified" wording about iOS.

### Task 8: Clean up
- **ACTION**: `rm -rf spike/`. Keep `.claude/PRPs/plans/assets/tls_spike.py` as the record.

---

## Testing Strategy

The spike *is* the test. No automated tests — the thing under test is two phone browsers.

### Results table to fill in

| Check | Android Chrome | iOS Safari |
|---|---|---|
| OS / browser version | | |
| Taps from QR scan to first frame | | |
| `isSecureContext` after cert bypass | | |
| `getUserMedia` back camera works | | |
| `fetch` POST frames → 200 | | |
| Frame resolution (settings) / max (caps) | | |
| `torch` capability | | |
| `ImageCapture` present | | |
| `wakeLock` | | |
| Resumes after screen lock | | |
| Resumes after app switch | | |
| Cert warning again on reopen | | |
| Permission prompt again on reopen | | |
| IC marking readable at ~15 cm | | |

### Edge Cases Checklist
- [x] Oversized / empty body → 413 (code path present)
- [x] Non-JPEG body → 400 (verified with curl)
- [x] Plain HTTP to the TLS port → connection error, server survives (verified)
- [ ] Permission denied on phone → page logs `ERR NotAllowedError` (verify once, either phone)
- [ ] Two phones at once → both post; `latest.jpg` is last-writer-wins (expected; naming is phase 2)

---

## Validation Commands

### Static Analysis
```bash
python3 -m py_compile spike/tls_spike.py && uvx ruff check spike/tls_spike.py
```
EXPECT: no output / "All checks passed!" (passed during planning)

### Server smoke test
See Task 2.

### Manual Validation
- [ ] Tasks 3-6 results table fully filled for both platforms
- [ ] Go/no-go stated in the PRD

---

## Acceptance Criteria
- [ ] A real JPEG from an Android phone landed in `spike/frames/`
- [ ] A real JPEG from an iPhone landed in `spike/frames/` — **or** the exact failure point on iOS is documented
- [ ] Results table filled; PRD Open Questions 1, 3, 5 resolved
- [ ] PRD phase 1 marked `complete`; decision log updated if no-go
- [ ] `spike/` deleted; nothing from the spike committed to the package

## Completion Checklist
- [ ] No changes to `framegrab_mcp_server.py`, `pyproject.toml`, `uv.lock`
- [ ] No unnecessary scope additions
- [ ] Findings that affect phases 3/5 (reconnect behaviour, max resolution) are written in the PRD, not just remembered

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| iOS refuses secure context after bypass | M | High — invalidates the self-signed decision | That's the point of the spike. Fallback already named in PRD: `--no-tls` + user tunnel. Optional extra experiment before giving up: AirDrop/email `cert.pem` to the iPhone, install profile, enable under Settings → General → About → Certificate Trust Settings, retest |
| No iPhone available | ? | Medium — half the answer | Run Android now, leave phase 1 `in-progress` and list iOS as the blocker; phases 2-3 are platform-neutral enough to start at own risk |
| Router has client isolation (guest Wi-Fi) | L | Phone can't reach PC at all | Use main SSID or a phone hotspot with the PC joined to it |
| stdlib TLS server stalls on a bad handshake | L | Confusing false negative | Restart the spike; noted in gotchas |

## Notes
- **Verified during planning** (by running it): script compiles, passes ruff, generates a cert with `IP:192.168.2.150, DNS:localhost` SAN, serves the page (200), accepts JPEG (200), rejects junk (400), survives plain-HTTP probes. **Not verified**: everything in the browser JS — it has not been executed on any device yet. Expect to possibly fix a typo on first phone run.
- **Open Question 3 (cert generation in the real server)**: the spike shells out to `openssl`, which is fine here but absent on stock Windows. Recommendation to confirm in Task 7: use `cryptography` in phase 2 (~20 lines, cross-platform, wheels everywhere) — the PRD's "platform agnostic" goal outweighs one extra dependency.
- The CI auto-format workflow targets `src/` with black/isort, which doesn't exist in this repo — it's a no-op. `pyproject.toml` lists `ruff` as the dev tool; use ruff in later phases.
- Tasks 3-6 need physical phones and a human; an agent running `/prp-implement` can do tasks 1, 2, 7, 8 and must hand 3-6 to the user.
