# Plan: Phone Cameras — On-demand Hi-res, Small Preview

## Summary
The phone stops streaming full-resolution frames once a second. It sends a small preview instead, and when the agent calls `grab_frame` the server asks for one full-resolution photo, waits for it, and returns that. The preview keeps the "is the page still open?" signal working and cuts the upload from ~450 KB/s to ~50 KB/s, which matters on the weak Wi-Fi seen in phase 4 (0.2 fps).

## User Story
As someone asking the agent to read a part marking, I want `grab_frame` to return a fresh full-resolution photo, so that the agent can read small text, without the phone having to upload a full-resolution frame every second while nobody is looking.

## Problem → Solution
Every frame is full-resolution (2160×4080, ~450 KB) at 1 fps whether or not anyone wants it; on a weak link this drops to 0.2 fps → a ~1280-px preview at 1 fps, plus one full-resolution photo per `grab_frame`, returned within a few seconds or replaced by the latest preview.

## Metadata
- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/phone-browser-cameras.prd.md`
- **PRD Phase**: 5 — On-demand hi-res
- **Estimated Files**: 4 updated, 0 new

---

## UX Design

### Before
```
phone: full-res JPEG every 1 s ──────────▶ server keeps latest
agent: grab_frame ─▶ latest (≤ 1 s old, full-res)
```

### After
```
phone: small preview every 1 s ──────────▶ server keeps latest preview
                              ◀── {"hires_requested": true} when an agent is waiting
phone: full-res photo now ───────────────▶ server hands it to the waiting grab_frame
agent: grab_frame ─▶ fresh photo (target < 4 s), or the latest preview if the
                     photo does not arrive in time
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Phone upload | ~450 KB/s | ~50 KB/s idle, +1 photo per grab | Battery, heat, Wi-Fi |
| `grab_frame` latency (phone) | 60–190 ms | up to ~1 s (poll) + capture + upload; PRD target < 4 s | Blocks the MCP loop while waiting (see gotcha) |
| `grab_frame` result | latest preview-quality full-res | fresh full-res photo; fallback: latest preview with a logged warning | Agent cannot tell which it got from the image alone |
| Phone page status | "N frames sent" | "N frames sent · M photos" | |
| Closed page | error after 10 s | same: staleness is checked **before** waiting, so no 4 s wait on a dead page | |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `multicam_mcp_phone.py` | 81-120 | `PhoneCamera`: `_latest` tuple swap, `grab()` staleness + decode — the class that gains the request/wait logic |
| P0 | `multicam_mcp_phone.py` | 316-333 | `_frame` handler; the response `{"hires_requested": False}` is the hook |
| P0 | `multicam_mcp_phone.html` | 175-214 | `loop()`: where the preview is drawn and where the response is read |
| P1 | `multicam_mcp_server.py` | 123-150 | `grab_frame` — unchanged, but understand that it runs **synchronously on the MCP event loop** |
| P1 | `test_phone_cameras.py` | 19-35 | `ctx` fixture, `join()` helper, `jpeg(w, h)` — the size argument is how tests tell preview from photo |
| P2 | `.claude/PRPs/reports/phone-cameras-join-flow-report.md` | Observations | The 0.2 fps episode this phase addresses; CDP technique for reading page state |

## External Documentation

No web research needed. Verified locally:

```
KEY_INSIGHT: FastMCP 1.6 calls a sync tool function directly on its event loop
             (mcp/server/fastmcp/utilities/func_metadata.py:66 `return fn(**arguments_parsed_dict)`).
APPLIES_TO: PhoneCamera.grab() waiting on a threading.Event
GOTCHA: While grab_frame waits (≤ HIRES_TIMEOUT_S) the MCP server answers nothing else. Acceptable:
        stdio has one client, and it is waiting on this very call. USB/RTSP grabs already block the
        same way. Ingest keeps flowing because uvicorn runs in its own thread — that is what makes
        the wait resolvable at all. Never wait longer than a few seconds.

KEY_INSIGHT (device, phase 2-3): the video track already runs at the camera's max video resolution
             (2160×4080 on the Pixel; fibres of a paper towel were resolved).
APPLIES_TO: how the "photo" is taken
GOTCHA: A full-resolution canvas grab of the <video> IS the hi-res photo. ImageCapture.takePhoto()
        is deliberately not used: it can return the sensor's full photo resolution (tens of MB on
        a 50 MP phone, over MAX_FRAME_BYTES), is Android-only, and adds failure modes. Upgrade
        path if the legibility test fails: takePhoto() with imageWidth/imageHeight from
        getPhotoCapabilities(), capped to fit MAX_FRAME_BYTES.

KEY_INSIGHT (device, phase 2-3 fix 2b): the page only sends when video.currentTime has advanced.
APPLIES_TO: hi-res capture
GOTCHA: The hi-res request is only ever seen in the response to a preview post, so a frozen
        camera can never produce a "fresh" photo. Keep it that way: no separate hi-res timer.
```

---

## Patterns to Mirror

### LOCK_FREE_LATEST (single tuple swap)
```python
# SOURCE: multicam_mcp_phone.py:88-93
        # One tuple, swapped atomically, so put() and grab() need no lock.
        self._latest: tuple[bytes, float] | None = None

    def put(self, jpeg: bytes) -> None:
        self._latest = (jpeg, time.monotonic())
```
The photo gets the same treatment: one tuple, one `threading.Event`.

### ACTIONABLE_GRAB_ERRORS
```python
# SOURCE: multicam_mcp_phone.py:95-109
    def grab(self) -> np.ndarray:
        latest = self._latest
        if latest is None:
            raise RuntimeError(f"Phone camera '{self.name}' has not sent a frame yet.")
        ...
        if age > STALE_AFTER_S:
            raise RuntimeError(
                f"Phone camera '{self.name}' last sent a frame {age:.0f}s ago. "
                "Is the page still open on the phone?"
            )
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
```
Keep these checks first; extract the decode into `_decode(jpeg)` because it now has two callers.

### LOGGING
```python
# SOURCE: multicam_mcp_phone.py:171, 356
        logger.warning("Stored token key is unusable, replacing it. Phones must re-enter the PIN.")
        logger.info(f"Phone cameras: open {self.url} on a phone on the same network.")
```

### PAGE LOOP (one send path, statuses handled in one place)
```js
// SOURCE: multicam_mcp_phone.html:190-208
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.85));
      const res = await fetch('/api/cameras/' + encodeURIComponent(name) + '/frame', {method: 'POST', headers: authHeaders(), body: blob});
      if (res.status === 401) { ... halt(...) } else if (res.status === 404) { ... } else if (res.status === 410) { ... } else if (res.ok) { ... }
```
Both uploads (preview, photo) must go through the same status handling — factor the fetch + status switch into `send(blob, kind)` that returns the parsed JSON on success or `null` after halting.

### TEST_STRUCTURE
```python
# SOURCE: test_phone_cameras.py:19-35
@pytest.fixture
def ctx(tmp_path): ...  # (registry, TestClient, server)
def join(client, server, name) -> dict: ...  # auth headers
def jpeg(w=64, h=48) -> bytes: ...
```
Waiting `grab()` is tested from a thread; the test thread posts to the `TestClient` while the main thread's `grab()` blocks — mirrors the real thread split (uvicorn thread vs MCP loop).

---

## Files to Change

| File | Action | Responsibility of the change |
|---|---|---|
| `multicam_mcp_phone.py` | UPDATE | `HIRES_TIMEOUT_S`; `PhoneCamera` photo slot + request flag + event; `grab()` request→wait→fallback; `_frame` reads `?kind=photo` and reports `hires_requested` |
| `multicam_mcp_phone.html` | UPDATE | Preview downscale; `send()` helper; photo capture on `hires_requested`; status counter |
| `test_phone_cameras.py` | UPDATE | 4 new tests; one existing assertion (`hires_requested` may now be true) |
| `README.md` | UPDATE | "What to expect" rows, latency wording, roadmap line removed |

## NOT Building

- `ImageCapture.takePhoto()` — see gotcha; upgrade path noted.
- A way for the agent to ask for "preview only" or "photo only" — `grab_frame` has one behaviour; the fallback covers the slow case.
- Per-camera config for preview size / JPEG quality / timeout — page constants and one module constant.
- Telling the agent whether it got a photo or a fallback preview — would need a tool-signature change; a warning is logged instead.
- Torch auto-on for the photo, focus/exposure locking, HDR.
- iOS work (D1 stands).

---

## Step-by-Step Tasks

### Task 0: Branch
- **ACTION**: `git checkout -b feat/phone-hires` from `main` (clean apart from untracked `.claude/`).

### Task 1: Failing tests — the request/wait/fallback contract
- **ACTION**: Add to `test_phone_cameras.py`. Existing `test_register_post_grab_roundtrip` asserts `r.json() == {"hires_requested": False}` — still true there (nobody is waiting), leave it.
- **IMPLEMENT**:
  ```python
  import threading


  def test_grab_asks_for_a_photo_and_returns_it(ctx):
      registry, client, server = ctx
      cam = join(client, server, "cam")
      post = lambda body, **q: client.post("/api/cameras/cam/frame", content=body, headers=cam, params=q)  # noqa: E731
      assert post(jpeg(64, 48)).json() == {"hires_requested": False}
      result = {}
      grabber = threading.Thread(target=lambda: result.update(frame=registry["cam"].grab()))
      grabber.start()
      # The next preview post learns that someone is waiting ...
      for _ in range(50):
          if post(jpeg(64, 48)).json()["hires_requested"]:
              break
          time.sleep(0.01)
      else:
          pytest.fail("grab() never asked for a photo")
      # ... and the photo satisfies the waiting grab().
      assert post(jpeg(640, 480), kind="photo").status_code == 200
      grabber.join(timeout=2)
      assert not grabber.is_alive() and result["frame"].shape == (480, 640, 3)
      assert post(jpeg(64, 48)).json() == {"hires_requested": False}  # request consumed


  def test_grab_falls_back_to_the_preview_when_no_photo_arrives(ctx, monkeypatch, caplog):
      registry, client, server = ctx
      cam = join(client, server, "cam")
      client.post("/api/cameras/cam/frame", content=jpeg(64, 48), headers=cam)
      monkeypatch.setattr(phone, "HIRES_TIMEOUT_S", 0.05)
      assert registry["cam"].grab().shape == (48, 64, 3)
      assert "did not send a photo" in caplog.text


  def test_grab_does_not_wait_on_a_dead_page(ctx, monkeypatch):
      registry, client, server = ctx
      cam = join(client, server, "cam")
      client.post("/api/cameras/cam/frame", content=jpeg(), headers=cam)
      monkeypatch.setattr(phone, "STALE_AFTER_S", -1)
      monkeypatch.setattr(phone, "HIRES_TIMEOUT_S", 30)
      started = time.monotonic()
      with pytest.raises(RuntimeError, match="Is the page still open"):
          registry["cam"].grab()
      assert time.monotonic() - started < 1


  def test_photo_upload_is_validated_like_a_preview(ctx):
      _, client, server = ctx
      cam = join(client, server, "cam")
      assert client.post("/api/cameras/cam/frame", content=b"GIF89a", headers=cam, params={"kind": "photo"}).status_code == 400
      assert client.post("/api/cameras/cam/frame", content=jpeg(), params={"kind": "photo"}).status_code == 401
      assert client.post("/api/cameras/cam/frame", content=jpeg(), headers=cam, params={"kind": "banana"}).status_code == 422
  ```
  (Write the lambda as a `def` — ruff E731 is enforced.) Add `import time` at the top.
- **VALIDATE**: `uv run pytest -q` → 4 failures (`TypeError`/`AssertionError`/`AttributeError: HIRES_TIMEOUT_S`).

### Task 2: `PhoneCamera` — request, wait, fallback
- **ACTION**: Edit `multicam_mcp_phone.py`.
- **IMPLEMENT**:
  ```python
  HIRES_TIMEOUT_S = 4.0  # how long grab() waits for a full-resolution photo
  ```
  ```python
  class PhoneCamera:
      def __init__(self, name: str, on_release):
          self.name = name
          self._on_release = on_release
          # One tuple, swapped atomically, so put() and grab() need no lock.
          self._latest: tuple[bytes, float] | None = None  # small preview, ~1/s
          self._photo: bytes | None = None  # full resolution, only when asked for
          self._photo_ready = threading.Event()
          self.photo_wanted = False  # read by the frame handler, reported to the phone

      def put(self, jpeg: bytes, photo: bool = False) -> None:
          if photo:
              self._photo = jpeg
              self.photo_wanted = False
              self._photo_ready.set()
          else:
              self._latest = (jpeg, time.monotonic())

      def grab(self) -> np.ndarray:
          latest = self._latest
          if latest is None:
              raise RuntimeError(...)  # unchanged
          jpeg, received_at = latest
          age = time.monotonic() - received_at
          if age > STALE_AFTER_S:
              raise RuntimeError(...)  # unchanged
          # Ask the phone for a full-resolution photo; the next preview post carries the
          # request and the phone answers with the photo. Fall back to the preview if it
          # does not arrive in time (slow Wi-Fi), rather than failing the call.
          # ponytail: one waiter at a time; a second concurrent grab() shares the same photo.
          self._photo_ready.clear()
          self._photo = None
          self.photo_wanted = True
          if self._photo_ready.wait(HIRES_TIMEOUT_S) and self._photo:
              return _decode(self.name, self._photo)
          self.photo_wanted = False
          logger.warning(
              f"Phone camera '{self.name}' did not send a photo within {HIRES_TIMEOUT_S:.0f}s; "
              "returning the latest preview instead."
          )
          return _decode(self.name, jpeg)
  ```
  ```python
  def _decode(name: str, jpeg: bytes) -> np.ndarray:
      frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
      if frame is None:
          raise RuntimeError(f"Phone camera '{name}' sent an undecodable image.")
      return frame
  ```
  In `_frame`, after the JPEG check:
  ```python
      kind = request.query_params.get("kind", "preview")
      if kind not in ("preview", "photo"):
          return _error(422, "kind must be 'preview' or 'photo'.")
      camera.put(body, photo=kind == "photo")
      return JSONResponse({"hires_requested": camera.photo_wanted})
  ```
  Put the `kind` check **before** reading the body, next to the other cheap checks.
- **MIRROR**: LOCK_FREE_LATEST, ACTIONABLE_GRAB_ERRORS, LOGGING.
- **IMPORTS**: `threading` (already imported).
- **GOTCHA**: `self._photo = None` before setting `photo_wanted`, otherwise a stale photo from a previous grab could satisfy the new wait if the event was somehow left set. A photo that arrives *after* the timeout sets the event for nothing — harmless; the next grab clears it first.
- **VALIDATE**: `uv run pytest -q` all green.

### Task 3: Page — small preview, photo on request
- **ACTION**: Edit `multicam_mcp_phone.html`.
- **IMPLEMENT**:
  - Constants next to `SEND_EVERY_MS`: `const PREVIEW_LONG_EDGE = 1280; const PREVIEW_QUALITY = 0.7; const PHOTO_QUALITY = 0.92;` and `let photos = 0;` beside `sent`.
  - `capture(longEdge, quality)`: compute `scale = longEdge ? Math.min(1, longEdge / Math.max(video.videoWidth, video.videoHeight)) : 1`; set `canvas.width/height` to the rounded scaled size; `drawImage(video, 0, 0, canvas.width, canvas.height)`; return the `toBlob` promise.
  - `send(blob, kind)`: the existing fetch with `'/frame' + (kind === 'photo' ? '?kind=photo' : '')`, then the existing 401/404/410/ok/else switch. Returns `await res.json()` on `res.ok`, otherwise `null` (after `halt()` where the old code halted; on 404 do the re-register as now and return `null`).
  - In `loop()`, replace the draw + fetch + switch with:
    ```js
      lastFrameTime = video.currentTime;
      const reply = await send(await capture(PREVIEW_LONG_EDGE, PREVIEW_QUALITY), 'preview');
      if (!reply) return;                       // halted, or rejoining: try again next tick
      status('Live as "' + name + '" · ' + (++sent) + ' frames sent' + (photos ? ' · ' + photos + ' photos' : ''));
      if (reply.hires_requested) {
        if (await send(await capture(0, PHOTO_QUALITY), 'photo')) photos++;
      }
    ```
    Keep `setTimeout(() => loop(mySession), SEND_EVERY_MS)` at the end, but **return early without rescheduling only when halted** — the 404 branch must still reschedule. Simplest: `send()` returns `null` for "halted" and `{}` for "rejoined, nothing to do", so `if (!reply) return;` only fires on halt.
- **MIRROR**: PAGE LOOP.
- **GOTCHA**: The preview is drawn at the smaller size — `drawImage` with dest size scales; do not re-use the previous canvas size. The photo call reuses `canvas` right after the preview; that is fine because `toBlob` resolved before we resize again. Keep the `video.videoWidth` guard: `capture` on a 0-size video yields a `null` blob.
- **VALIDATE**: `uv run pytest -q` (page served) then Task 4.

### Task 4: On-device validation (Pixel, adb + CDP, real stdio MCP session)
- **ACTION**: Reuse the scratch driver and `cdp.py` from the last phases (`adb forward tcp:9222 localabstract:chrome_devtools_remote`; remove afterwards). Join as before (token is stored; PIN is per start).
- **VALIDATE** (record in the report):
  - [ ] Join, wait 10 s, then `grab_frame`: returns 2160×4080; CDP shows `photos` went 0 → 1 and `sent` kept counting
  - [ ] Preview size: add `let lastPreviewBytes` set in `loop()` (keep it; useful for the phase 6 soak) and read it over CDP — expect roughly 40–80 KB, long edge 1280
  - [ ] `grab_frame` latency: 5 calls; the driver logs ms per call; PRD target < 4 s, expect ~1–1.5 s on good Wi-Fi
  - [ ] Fallback: a scratch `uv run python` session that imports `multicam_mcp_phone`, sets `HIRES_TIMEOUT_S = 0.01`, starts the server on 8443 and calls `grab()` → the "did not send a photo" warning and a frame with long edge ≤ 1280
  - [ ] Idle upload: over 60 s with no grabs, `photos` stays 0 and `sent` ≈ 60
  - [ ] **Legibility (PRD metric, debt D3):** the user points the phone at a PCB from ~15 cm; `grab_frame`; open the image — is a SOIC-8 top marking readable? adb can drive everything else, but someone has to hold the phone
- **GOTCHA**: The phone's screen must be on and the page visible (phase 2-3 fix 2b pauses sending when hidden). Use `adb shell input keyevent KEYCODE_WAKEUP` first.

### Task 5: README
- **ACTION**: In "What to expect": change the `grab_frame` row to "It asks the phone for a full-resolution photo and returns it, usually within a second or two. If the photo does not arrive within 4 seconds (slow Wi-Fi), it returns the latest preview instead." Change the closed-page row's "one full-resolution frame per second" wording wherever it appears to "a small preview once a second". Remove the roadmap line "Full-resolution capture on demand…". Mention the upload figures once (about 50 KB/s while idle).
- **VALIDATE**: `grep -n 'full-resolution frame per second' README.md` → nothing.

### Task 6: Format, lint, build, final run
- **ACTION**: `uv run ruff format multicam_mcp_phone.py test_phone_cameras.py && uv run ruff check multicam_mcp_phone.py test_phone_cameras.py && uv run pytest -q`; wheel build + listing; stdout purity run.
- **VALIDATE**: clean; green; 3 `multicam_mcp_*` files; 0 bytes on stdout.

---

## Testing Strategy

### Unit / integration tests

| Test | Input | Expected | Edge? |
|---|---|---|---|
| grab asks for a photo and returns it | grab() in a thread; previews until `hires_requested`; photo 640×480 | grab returns (480, 640, 3); next preview sees `false` | |
| fallback | no photo within 0.05 s | returns preview shape; warning logged | yes |
| dead page | stale preview, long timeout | raises immediately, < 1 s | yes |
| photo validation | GIF / no token / bad kind | 400 / 401 / 422 | yes |
| all 30 existing | unchanged | green | |

### Edge Cases Checklist
- [x] No preview yet → same error as before, no wait
- [x] Stale preview → error before waiting
- [x] Photo never arrives → preview + warning
- [x] Photo arrives late (after timeout) → ignored; next grab clears it
- [x] Bad `kind` → 422; photo path shares auth/size/JPEG checks
- [ ] Two concurrent grabs → both get the same photo (accepted; `ponytail:` note)
- [ ] Camera switch mid-request → preview loop skips a tick (`videoWidth` 0); request stays pending; next tick answers it

---

## Validation Commands

```bash
uv run ruff format --check multicam_mcp_phone.py test_phone_cameras.py && uv run ruff check multicam_mcp_phone.py test_phone_cameras.py
uv run pytest -q
ENABLE_FRAMEGRAB_PHONE_CAMERAS=true timeout 8 uv run multicam-mcp-server </dev/null >"$SCRATCH/o.txt" 2>/dev/null; wc -c < "$SCRATCH/o.txt"   # 0
uv build --no-sources --wheel -o "$SCRATCH/dist" && python3 -m zipfile -l "$SCRATCH"/dist/*.whl
```
Plus the Task 4 device checklist.

---

## Acceptance Criteria
- [ ] Idle phone uploads previews only; `grab_frame` returns a full-resolution frame (2160×4080 on the Pixel) taken after the call
- [ ] `grab_frame` on a phone camera completes in < 4 s on the bench Wi-Fi; fallback path proven with a short timeout
- [ ] A closed page still fails fast with the "Is the page still open" error
- [ ] 34 tests green; ruff clean; stdout purity 0; wheel shape unchanged
- [ ] README updated; PRD phase 5 status + D3 legibility result recorded (or "still owed: user")

## Completion Checklist
- [ ] No `ImageCapture`, no new dependency, no env var
- [ ] Both uploads share one send/status path on the page
- [ ] `ponytail:` note on the single-waiter assumption
- [ ] Error messages unchanged for the agent-facing failure cases

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Photo too slow on weak Wi-Fi (450 KB at 0.2 fps ≈ 5 s) | M | Fallback preview (1280 px) — markings may be unreadable | Timeout is a constant; preview is still 3× the pixels of a 640-px thumbnail; user can move closer |
| Blocking the MCP loop for up to 4 s | Certain per call | Client waits; nothing else is served meanwhile | Single stdio client; documented |
| 1280-px preview not enough for the "is it live" use | L | Agent sees a soft image only when the photo fails | The photo is the normal path |
| Canvas `toBlob` at 8.8 MP twice in one tick (preview then photo) | L | ~200 ms extra on the phone | Only when asked |

## Notes
- Input to `/prp-plan` was blank; planned the PRD's next pending phase (5).
- **Decision (veto-able):** no `ImageCapture.takePhoto()`. The max-resolution video frame is the photo. Reason in the gotcha above; upgrade path noted for the legibility test.
- **Decision:** `grab_frame` always requests a photo. There is no "cheap grab" mode; the fallback is the cheap mode.
- Phase 6 (soak + docs) benefits directly: idle upload drops ~9×, which is the main battery/heat lever for the 2-hour target (PRD D2).
