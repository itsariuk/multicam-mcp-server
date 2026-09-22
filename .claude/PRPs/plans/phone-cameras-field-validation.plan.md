# Plan: Phone Cameras — Docs + Field Validation

## Summary
Fill the PRD's success-metrics table with real numbers, pay down the validation debts that can be paid, and finish the docs. Almost no product code changes: one throwaway soak script, a README pass, and a version bump. Most of the work is running the phone for two hours and asking one other person to set it up unaided.

## User Story
As the person who wrote the hypothesis, I want measured numbers for time-to-first-frame, session survival, latency and legibility, so that I know whether "AI, look at this" became a zero-step action or just a demo.

## Problem → Solution
Every phase so far verified *mechanics* (does it join, does it grab) with the phone lying on a desk → measure the *experience* with the phone propped over a real board for a real session, and write down what happened.

## Metadata
- **Complexity**: Small (code); Large (calendar: ~3 h of wall-clock tests, one other human)
- **Source PRD**: `.claude/PRPs/prds/phone-browser-cameras.prd.md`
- **PRD Phase**: 6 — Docs + field validation
- **Estimated Files**: 3 updated (`README.md`, `pyproject.toml`, PRD) + 1 scratch script kept under `.claude/PRPs/plans/assets/`

---

## UX Design

N/A — no product behaviour changes. The README gains measured numbers and a screenshot of the certificate warning.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `.claude/PRPs/prds/phone-browser-cameras.prd.md` | Success Metrics, Validation Debt, Open Questions | What has to be measured and which debts are payable |
| P0 | `.claude/PRPs/reports/phone-cameras-hires-on-demand-report.md` | On-device results, Observations | Latest numbers (grab 0.5–1.4 s), and the note that all phase 5 images were black |
| P1 | `.claude/PRPs/reports/phone-cameras-join-flow-report.md` | Observations | The 0.2 fps Wi-Fi episode; how state is read from the page over Chrome remote debugging |
| P1 | `README.md` | 80-118 | The phone-camera section that gets the final numbers |
| P2 | scratch `drive.py` (`/tmp/claude-1000/…/scratchpad/drive.py`) | all | The stdio MCP driver used in every phase; the soak script extends it. **Scratch dirs are session-local; copy it to `.claude/PRPs/plans/assets/` first** |

## External Documentation

No external research needed — this phase runs the product, it does not extend it.

```
KEY_INSIGHT (phase 2-3, fix 2b): the page pauses when hidden or when the video clock stops.
APPLIES_TO: the 2-hour soak
GOTCHA: the phone must stay on the page with the screen on. The wake lock does that on
        Android when the page is visible; a lock screen or a notification shade does not.
        The soak measures exactly this — do not "help" it by keeping adb awake. No USB:
        wall charger only (PRD D2 says the earlier screen-off result was confounded by USB).

KEY_INSIGHT (phase 4): the stdio server restarts when the MCP client restarts; a live
        Codex voice session keeps it alive.
APPLIES_TO: what process runs during the soak
GOTCHA: use the real client (Codex) if possible so the test also covers Codex holding the
        server for 2 h. Fallback: the scratch driver, which behaves the same for the server.
```

---

## Patterns to Mirror

### SCRATCH_DRIVER (how every phase talked to the server)
```python
# SOURCE: scratchpad/drive.py:28-35, 44-51
    params = StdioServerParameters(command="uv", args=["run", "--project", REPO, "multicam-mcp-server"],
                                   env={**os.environ, "ENABLE_FRAMEGRAB_PHONE_CAMERAS": "true"})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        ...
        res = await s.call_tool(tool, args)
```
Commands are appended to `cmd.txt`; results go to `drive.log` with ms timings. The soak script is this loop with a timer instead of a command file.

### README SECTION SHAPE
```markdown
# SOURCE: README.md:112-118
### Security
...
### Tested with
Chrome on Android. Safari on iOS has not been tested yet.
```
Short headed subsections; plain sentences; numbers stated as measured, with the device named.

### PRD BOOKKEEPING (what earlier phases did)
Phase status in the table → `complete (…)` with plan + report links; debts get a "paid on <date>: …" note in the "How to pay it off" cell, or stay open with the reason; open questions get `[x]` + a resolution note.

---

## Files to Change

| File | Action | Responsibility |
|---|---|---|
| `.claude/PRPs/plans/assets/soak.py` | CREATE (scratch, kept as record) | Hold the server over stdio, call `grab_frame` every 10 min for 2 h, log outcome + ms + image size, save every frame |
| `README.md` | UPDATE | Measured numbers; certificate-warning screenshot; "Tested with" with the soak result; iOS status unchanged unless D1 gets paid |
| `assets/cert-warning.png` | CREATE | The Chrome "connection is not private" screen, cropped, referenced from the README's step 3 |
| `pyproject.toml` | UPDATE | `version = "0.2.0"` (phone cameras is a new feature on a renamed package). No publish |
| `.claude/PRPs/prds/phone-browser-cameras.prd.md` | UPDATE | Metrics table filled; debts D2, D3 resolved or annotated; phase 6 complete; status line changed from DRAFT |

## NOT Building

- Any product code. If a test **fails**, file it in the report and the PRD; fixing it is a new plan, not a silent addition here.
- iOS testing (D1): no device. Stays open; README keeps saying so.
- A shipped `soak` or `doctor` command. The script stays under `.claude/`.
- Publishing to PyPI; deleting `auto-format.yaml` (separate, one-line decision for the user).
- A second phone / second bench in the soak. One phone, two hours.

---

## Step-by-Step Tasks

### Task 0: Branch
- **ACTION**: `git checkout -b chore/field-validation`.

### Task 1: Soak script
- **ACTION**: Copy `drive.py` to `.claude/PRPs/plans/assets/soak.py` and change the command loop to a timer.
- **IMPLEMENT**: keep the `StdioServerParameters`/`ClientSession` setup verbatim. Body:
  ```python
  INTERVAL_S, DURATION_S, NAME = 600, 2 * 3600, sys.argv[2]
  t_end = time.monotonic() + DURATION_S
  n = 0
  while time.monotonic() < t_end:
      t0 = time.monotonic()
      res = await s.call_tool("grab_frame", {"framegrabber_name": NAME, "format": "jpg"})
      ms = (time.monotonic() - t0) * 1000
      n += 1
      if res.isError:
          log(f"{n:02d} FAIL {ms:.0f}ms {res.content[0].text}")
      else:
          data = base64.b64decode(res.content[0].data)
          (HERE / f"soak_{n:02d}.jpg").write_bytes(data)
          log(f"{n:02d} ok {ms:.0f}ms {len(data)}B")
      await asyncio.sleep(INTERVAL_S)
  log("DONE")
  ```
  Usage: `uv run python .claude/PRPs/plans/assets/soak.py <repo> "left bench"` — start it **after** the phone has joined (it spawns its own server, so join through this server's PIN: call `add_phone_camera` first via a 5-line pre-step in the script that logs the PIN, then wait for `list_framegrabbers` to contain NAME before starting the timer).
- **GOTCHA**: One server per process. If Codex is running the server on 8443 at the same time, the soak's server fails to bind and the script must say so — check `add_phone_camera` raises and exit with the message. If the user prefers to soak under Codex itself, skip this script and use Codex: ask it every 10 min to look at the bench; the numbers then come from its transcript, less precise but the more honest test.
- **VALIDATE**: `uv run python .claude/PRPs/plans/assets/soak.py <repo> nothing` against no phone → 12 `FAIL … has not sent a frame yet` lines if left running (or Ctrl-C after the first); `log` lines have timestamps.

### Task 2: Certificate-warning screenshot
- **ACTION**: Phone on USB for this one step only. Delete `~/.local/share/multicam-mcp-server/server.pem` so the phone sees the warning again; start the server; `adb shell am start -a android.intent.action.VIEW -d https://<ip>:8443/ com.android.chrome`; `adb exec-out screencap -p`; crop the status bar as in phase 3 (`img[140:2900]`, resize to 600 wide); save `assets/cert-warning.png`; proceed past the warning so the phone trusts the new cert.
- **VALIDATE**: file exists, < 300 KB, shows "Your connection is not private" and the Advanced button.

### Task 3: Cold-phone stopwatch run (metric 1, debt D3 part 1)
- **ACTION**: User + phone, no adb. Forget the site in Chrome (Site settings → clear & reset for the IP) so both the certificate exception and the token are gone. Ask the agent (Codex) to add a phone camera; start the stopwatch when the join page appears on the PC; scan the QR with the camera app; Advanced → Proceed; allow camera; name; Start; stop the stopwatch when `grab_frame` returns the first image (ask the agent to look).
- **RECORD**: seconds, number of taps, anything that confused you. Target < 60 s.
- **GOTCHA**: this is the one run that must not be automated; the point is the human path.

### Task 4: Legibility (metric 4, debt D3 part 2)
- **ACTION**: Prop the phone ~15 cm above a PCB with an SOIC-8 or similar marked part, good light (torch if needed). Ask the agent to read the marking on that part. Then compare with the real marking.
- **RECORD**: what the agent read vs the truth; the `grab_frame` ms; `lastPreviewBytes` (over remote debugging, or skip). If it fails: try 10 cm and the torch; record both. If it still fails, note "upgrade path: `ImageCapture.takePhoto()`" for a follow-up plan.
- **VALIDATE**: PRD row filled with pass/fail and distance.

### Task 5: Two-hour soak (metric 2, debt D2)
- **ACTION**: Phone on a wall charger, **not USB**, propped over the bench, screen on the page, Wi-Fi only. Secure lock screen enabled as it normally is. Start `soak.py` (or use Codex, see Task 1 gotcha). Walk away for two hours. Do normal things on the PC; do not touch the phone.
- **RECORD**: from `soak.log`: ok/FAIL per 10-min slot, ms per grab, size per image; from the phone afterwards: status line (`… frames sent · N photos`), battery %, whether it was warm. Look at `soak_01.jpg` and `soak_12.jpg` side by side.
- **GOTCHA**: a single FAIL with "last sent a frame Ns ago" means the page paused — check whether the screen went off (wake lock lost) or Wi-Fi dropped (Chrome shows "Reconnecting…"). That distinction decides the follow-up.
- **VALIDATE**: 12 rows in the log.

### Task 6: One other person, unaided
- **ACTION**: Give someone the README and a phone, and a Codex/Claude Desktop session with the server configured. Do not help. Note where they stop and read, what they ask, whether they finish.
- **RECORD**: minutes to first frame; the first thing they got wrong; the sentence in the README they needed and did not find.
- **GOTCHA**: this validates the "other people" assumption in the PRD's Evidence section, which has been an assumption since day one.

### Task 7: README final pass
- **ACTION**: Add the screenshot to step 3 (`![…](assets/cert-warning.png)` at width 300). Under "Tested with", replace the sentence with measured facts: device, join time, soak result (e.g. "kept a Pixel 11 Pro XL on a charger for 2 hours: 12 of 12 grabs succeeded"), typical `grab_frame` time. Fix whatever the Task 6 person tripped on. Keep "Safari on iOS has not been tested yet."
- **VALIDATE**: every number in the README appears in a report; no claim without a measurement behind it.

### Task 8: Version + PRD bookkeeping
- **ACTION**: `version = "0.2.0"` in `pyproject.toml`; `uv lock` (expect only the project's own version line to change). PRD: fill the metrics table with measured values; D2 and D3 → paid or annotated; D1 stays open; phase 6 → complete; change `*Status: DRAFT - needs validation*` to `*Status: VALIDATED on Android, <date>. iOS untested.*` (or "PARTLY VALIDATED" if any metric failed); Open Question "Should `.claude/PRPs/` be committed?" → ask the user once in the final report, don't decide.
- **VALIDATE**: `uv run pytest -q` still green (nothing should change); `git diff --stat` shows README, pyproject, uv.lock, assets, `.claude/`.

### Task 9: Report
- **ACTION**: Write `.claude/PRPs/reports/phone-cameras-field-validation-report.md` with the filled metrics table, the soak log, the other person's notes, and a plain verdict on the PRD hypothesis: **held / partly held / did not hold**, with the reason.

---

## Testing Strategy

There are no unit tests to add; the existing 35 must stay green. The measurements are the tests:

| Measurement | Target | Owner | Tooling |
|---|---|---|---|
| Cold phone → first frame | < 60 s | user | stopwatch |
| 2 h survival, no touches | 12/12 grabs | user starts, script measures | `soak.py` |
| `grab_frame` latency | < 4 s | script | `soak.log` ms column |
| SOIC-8 marking at ~15 cm | agent reads it correctly | user | Codex |
| Another person, unaided | finishes | user observes | notes |
| Manual photos during a real repair session | 0 | user, self-report | — |

### Edge Cases Checklist
- [ ] Soak: Wi-Fi drop mid-run → page shows "Reconnecting…", resumes; grab fails only during the gap
- [ ] Soak: phone screen dims but stays on (wake lock) — the expected steady state
- [ ] Soak: phone screen turns off — a real finding; record the time it happened
- [ ] Legibility fails at 15 cm but passes at 10 cm — record both; the metric row gets refined, not fudged

---

## Validation Commands

```bash
uv run pytest -q                       # unchanged code: 35 passed
uv run ruff check multicam_mcp_phone.py test_phone_cameras.py
grep -n 'not been tested\|Tested with' README.md   # iOS sentence still present
ls -la assets/cert-warning.png          # exists, < 300 KB
grep -c '^[0-9][0-9] ' .claude/PRPs/plans/assets/soak.log   # 12 after the soak
```

---

## Acceptance Criteria
- [ ] Metrics table in the PRD has a measured value in every row, or "not measured: <reason>"
- [ ] D2 and D3 resolved or annotated with what was measured; D1 explicitly still open
- [ ] README states only measured facts; screenshot of the certificate warning present
- [ ] Version 0.2.0; tests green; nothing pushed to PyPI
- [ ] Report gives a one-word verdict on the hypothesis with the evidence beside it

## Completion Checklist
- [ ] No product code changed in this phase (if it was, there is a report entry saying why)
- [ ] Soak run on charger, not USB
- [ ] The unaided-person run happened with a person who is not the author

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Screen turns off during the soak (wake lock dropped after a notification, battery saver) | M | Survival metric fails | It is a finding, not a failure of the test; the follow-up is a page change (re-request the wake lock on an interval) planned separately |
| Nobody available for the unaided run | M | "Other people" stays an assumption | Say so in the PRD; do not substitute yourself |
| Legibility fails | M | Core use case weaker than hoped | Record the distance at which it passes; `ImageCapture.takePhoto()` is the named upgrade |
| Codex voice mode does not show images or the join page | L | Stopwatch run uses the driver instead | Record which client was used |

## Notes
- Input to `/prp-plan` was blank; this is the PRD's last pending phase.
- Almost every task here needs the user and the phone in the workshop; an agent can do Tasks 0, 1, 2, 7, 8, 9 and must hand 3, 4, 5, 6 over. Expect this phase to span more than one session.
- `auto-format.yaml` is still in `.github/workflows` (targets a `src/` that does not exist). Removing it is a one-line decision for the user; the plan deliberately does not fold it in.
