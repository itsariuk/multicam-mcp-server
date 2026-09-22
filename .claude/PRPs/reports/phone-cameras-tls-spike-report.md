# Implementation Report: Phone Cameras — TLS Spike

## Summary
Ran the throwaway self-signed-HTTPS spike against a real Android phone (Pixel 11 Pro XL, Android 17, Chrome 153), driving the checks over adb. Android passes every check that was run. iOS was not tested (no device) and is recorded as validation debt D1 in the PRD by the user's decision.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Small | Small |
| Confidence | 9/10 that the spike runs | Ran first time; two page bugs found by misuse (see Issues) |
| Files Changed | 1 throwaway + PRD | Same. Nothing in the package touched |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Place the spike | Complete | |
| 2 | Start + server smoke test | Complete | GET 200 (localhost + LAN IP), JPEG 200, junk 400 |
| 3 | Android Chrome run | Complete | 244 frames, 2160×4080, ~460 KB. Taps-to-first-frame NOT measured (page already open) |
| 4 | iOS Safari run | **Skipped — debt D1** | No iPhone |
| 5 | Survival + reload checks | Partial | Android only; screen-off result confounded by USB/dev settings (debt D2). 15-min wake-lock soak not run |
| 6 | Legibility check | **Skipped — debt D3** | Phone was lying flat; close-focus detail was good but no IC marking tested |
| 7 | Write results into PRD | Complete | Spike Results table + Validation Debt section |
| 8 | Clean up | Complete | Server stopped, `spike/` deleted, adb forward removed |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `py_compile` + `ruff check` on the spike script |
| Unit Tests | N/A | Throwaway; the phones are the test |
| Build | N/A | Package untouched |
| Integration | Pass (Android) / Not run (iOS) | |
| Edge Cases | Partial | 413/400/plain-HTTP verified; permission-denied and two-phones-at-once not exercised |

## Files Changed

| File | Action |
|---|---|
| `.claude/PRPs/plans/assets/tls_spike.py` | CREATED (kept as record; rerun it to pay D1) |
| `.claude/PRPs/prds/phone-browser-cameras.prd.md` | UPDATED (Spike Results, Validation Debt, phase 1 status) |
| `.git/info/exclude` | UPDATED (`spike/` — local only, harmless to leave) |
| `spike/` | CREATED then DELETED |

## Deviations from Plan
- No feature branch — nothing from the spike is committed.
- Android checks were driven via adb + Chrome remote debugging instead of by hand. Side effect: the user's original spike tab was closed and a new one opened.
- Phase marked complete with iOS untested, by explicit user decision.

## Issues Encountered
- **Double-tap kills the page**: second tap on Start re-assigns `srcObject`, first `play()` rejects with `AbortError`, send loop never starts. → Phase 3: single-shot button.
- **Camera is exclusive per tab** (Chrome Android): second tab's `getUserMedia` pends silently. → Phase 3: "open in another tab?" hint after ~5 s.
- `pkill -f spike/tls_spike.py` also killed the shell issuing it (pattern matched its own command line). Server did stop; cleanup was re-run. No lasting effect.

## Next Steps
- [ ] `/prp-plan .claude/PRPs/prds/phone-browser-cameras.prd.md` → phases 2 + 3 (parallel-eligible; agree the HTTP contract first)
- [ ] Pay D1 when an iPhone is available (~15 min)
