# Implementation Report: Phone Cameras — Docs + Field Validation

**Status: IN PROGRESS — handed over to the user for the measurements (2026-09-21).**

## Done by the agent

| # | Task | Status | Notes |
|---|---|---|---|
| 0 | Branch | Complete | `chore/field-validation` |
| 1 | Soak script | Complete | `.claude/PRPs/plans/assets/soak.py`; dry-run: starts the server, opens the join page, prints the PIN, waits for the named phone |
| 2 | Certificate-warning screenshot | Complete | `assets/cert-warning.png` (78 KB), added to README step 3 |
| 7 | README | Partial | Screenshot added. Measured numbers wait for tasks 3-6 |

## Finding while doing Task 2
Deleting `server.pem` and restarting did **not** make Chrome on Android show the certificate warning again: Chrome remembers the bypass per host for the browsing session, not per certificate. So a regenerated certificate (new IP → new SAN) is smoother than the README implies; the README's "It will ask again if your computer's IP address changes" is still correct because a new IP is a new host. The warning asset was taken from an earlier capture of the same screen.

## Owed by the user (tasks 3-6)

| # | Measurement | Target | How |
|---|---|---|---|
| 3 | Cold phone → first frame | < 60 s | Chrome: clear site data for the IP. Ask the agent to add a phone camera; stopwatch from the join page appearing to the first image the agent sees |
| 4 | Legibility | agent reads a SOIC-8 marking at ~15 cm | Prop the phone over a board; ask the agent to read the part; compare. If it fails, try 10 cm + torch and record both |
| 5 | 2-hour survival | 12/12 grabs | `uv run python .claude/PRPs/plans/assets/soak.py /path/to/multicam-mcp-server "left bench"` — phone on a **wall charger**, no USB, page on screen; join via the printed PIN; walk away. Or run it under Codex and ask it to look every 10 min |
| 6 | Another person, unaided | finishes | Hand over README + phone; take notes; do not help |

Then: `/prp-implement` again (or just say "continue field validation") to do Task 7 (numbers into the README), Task 8 (version 0.2.0, PRD metrics table, debts D2/D3, status line), and the verdict.

## Files Changed so far

| File | Action |
|---|---|
| `.claude/PRPs/plans/assets/soak.py` | CREATED |
| `assets/cert-warning.png` | CREATED |
| `README.md` | UPDATED (+2) |
