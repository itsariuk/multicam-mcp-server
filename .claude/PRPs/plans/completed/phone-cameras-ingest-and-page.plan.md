# Plan: Phone Cameras — Ingest Server + Phone Page

## Summary
Add an opt-in HTTPS server inside the `framegrab-mcp-server` process. A phone opens its page, names itself, and POSTs JPEG stills; each named phone appears in the existing `_grabber_cache`, so `list_framegrabbers` / `grab_frame` / `release_grabber` work on it unchanged. Covers PRD phases 2 and 3 together because they share one HTTP contract and only the pair can be validated on a real phone.

## User Story
As someone doing bench repair with a voice agent, I want to open a URL on my phone, type "left bench", and prop it up, so that the agent can call `grab_frame("left bench")` and see my board without me taking photos.

## Problem → Solution
Agent can only see PC-attached cameras created via `create_framegrabber` → agent can also see any phone browser on the LAN that has joined under a name.

## Metadata
- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/phone-browser-cameras.prd.md`
- **PRD Phase**: 2 (Ingest server + registry) and 3 (Phone page)
- **Estimated Files**: 6 (3 new, 3 updated)

---

## UX Design

### Before
```
repair → stop → pick up phone → photo → transfer to agent → resume
```

### After
```
once:    phone → https://<pc-ip>:8443/ → accept cert warning → allow camera
         → name "left bench" → Start → prop phone, plug charger
always:  "look at the left bench" → agent: grab_frame("left bench") → answer
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| MCP config | — | `ENABLE_FRAMEGRAB_PHONE_CAMERAS=true` | Off by default: it opens a LAN port and has no auth until phase 4 |
| `list_framegrabbers` | PC cameras | + joined phone names | no tool changes |
| `grab_frame(name)` | grabs from device | for phones: decodes latest posted JPEG; errors if older than 10 s | error text tells the agent the page is probably closed |
| `release_grabber(name)` | releases device | phone page stops and says "Released by the agent" | phone must tap Start to rejoin |
| `set_config(name, …)` | applies options | for phones: error "no configurable options" | |
| Finding the URL | — | stderr log line only | QR + join tool are phase 4 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `framegrab_mcp_server.py` | 19-56 | Env-var config pattern, `_grabber_cache`, lifespan start/stop — the integration point |
| P0 | `framegrab_mcp_server.py` | 97-133, 144-201 | How tools use a grabber: `.grab()`, `.config`, `.apply_options()`, `.release()` — the duck-type surface `PhoneCamera` must satisfy |
| P0 | `.claude/PRPs/plans/assets/proto_server.py` | all | **Verified** prototype: cert generation/reuse/regeneration, uvicorn in a thread on a pre-bound socket, verifying TLS client, clean stop, zero stdout |
| P1 | `.claude/PRPs/plans/assets/tls_spike.py` | 22-58 | Page JS proven on the Pixel (getUserMedia constraints, canvas→toBlob→fetch) and `lan_ip()` |
| P1 | `.claude/PRPs/prds/phone-browser-cameras.prd.md` | Spike Results, Validation Debt | Device findings that drive page requirements |
| P2 | `README.md` | 61-88 | Format of an env-var feature section |

## External Documentation

No web research needed beyond the PRD's. Library facts below were verified by reading the installed packages (`.venv`, Python 3.13.15): mcp 1.6.0, uvicorn 0.34.0, starlette 0.46.1, framegrab 0.11.1, httpx 0.28.1, platformdirs 4.3.7 (transitive via zeep), opencv-python 4.11, numpy 2.2.4. `cryptography` and `pytest` are NOT installed.

```
KEY_INSIGHT: uvicorn's default LOGGING_CONFIG sends the access log to sys.stdout (uvicorn/config.py:90).
APPLIES_TO: uvicorn.Config(...)
GOTCHA: stdout is the MCP stdio channel. MUST pass log_config=None, access_log=False. Prototype confirmed 0 bytes on stdout.

KEY_INSIGHT: FastMCP 1.6 calls sync tool functions directly on its event loop (mcp/server/fastmcp/tools/base.py:83) — a slow grab() blocks that loop.
APPLIES_TO: where uvicorn runs
GOTCHA: run uvicorn in its own thread (own loop) so phone ingest never stalls behind a tool call. In a non-main thread uvicorn's capture_signals() is a no-op (server.py:315), so MCP's signal handling is untouched.

KEY_INSIGHT: uvicorn calls sys.exit(1) if it fails to bind (server.py:173).
APPLIES_TO: port-conflict handling (two stdio clients → two processes)
GOTCHA: bind the socket ourselves, catch OSError, pass server.run(sockets=[sock]). Also gives port 0 for tests.

KEY_INSIGHT: hatchling currently ships ONLY framegrab_mcp_server.py (single-module heuristic; verified by building the wheel).
APPLIES_TO: pyproject.toml
GOTCHA: adding a second module + an .html silently drops them from the wheel unless [tool.hatch.build.targets.wheel] include = [...] is set. Validate by listing the wheel.

KEY_INSIGHT: `mcp dev file.py` inserts the file's directory into sys.path (mcp/cli/cli.py:132).
APPLIES_TO: sibling import `import framegrab_mcp_phone`
GOTCHA: none — works for `mcp dev`, `uv run framegrab_mcp_server.py`, and the installed console script.

KEY_INSIGHT: FrameGrabber.config is a pydantic model (framegrab/config.py:58); get_framegrabber_config returns it as-is.
APPLIES_TO: PhoneCamera.config
GOTCHA: a plain dict is fine — FastMCP serialises both.

KEY_INSIGHT (device, from spike): Chrome Android gives the camera to one tab at a time; a second tab's getUserMedia pends silently. A second tap on Start aborts the first play() with AbortError.
APPLIES_TO: page start()
GOTCHA: disable the button on first tap; show an "open in another tab?" hint if getUserMedia hasn't resolved in 5 s.

KEY_INSIGHT: a screen wake lock is released automatically whenever the page becomes hidden.
APPLIES_TO: page wake-lock handling
GOTCHA: re-request on `visibilitychange` → visible. (General web-platform knowledge; not yet observed on device.)
```

---

## Patterns to Mirror

### CONFIG_VIA_ENV
```python
# SOURCE: framegrab_mcp_server.py:21-27
ENABLE_FRAMEGRAB_AUTO_DISCOVERY = (
    os.getenv("ENABLE_FRAMEGRAB_AUTO_DISCOVERY", "false").lower() == "true"
)
FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE = os.getenv(
    "FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE",
    "off",  # "off", "ip_only", "light", "complete_fast", "complete_slow"
)
```

### LOGGING_PATTERN
```python
# SOURCE: framegrab_mcp_server.py:19, 36, 44
logger = logging.getLogger(__name__)
logger.info("Autodiscovering generic_usb and basler framegrabbers...")
logger.error("Error autodiscovering framegrabbers.", exc_info=True)
```
f-strings, sentence case, trailing period. FastMCP routes `logging` to stderr — never `print`.

### LIFESPAN_START_STOP
```python
# SOURCE: framegrab_mcp_server.py:33-56
@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[Any]:
    if ENABLE_FRAMEGRAB_AUTO_DISCOVERY:
        ...
    yield {}
    logger.info("Framegrab MCP server is stopping, releasing framegrabbers...")
    for _, grabber in _grabber_cache.items():
        try:
            grabber.release()
```
Optional feature guarded by its flag before `yield`; teardown after. Teardown calls `release()` on every cache entry, including `PhoneCamera`s — `release()` must be safe to call at any time.

### ERROR_HANDLING (tool side)
```python
# SOURCE: framegrab_mcp_server.py:104-108
grabber: FrameGrabber = _grabber_cache.get(framegrabber_name)
if not grabber:
    raise ValueError(
        f"Framegrabber with name {framegrabber_name} not found. Options are: {list(_grabber_cache.keys())}."
    )
```
Tools raise `ValueError`/`RuntimeError` with a message the agent can act on. `grab_frame` does not wrap `grabber.grab()`, so whatever `PhoneCamera.grab()` raises reaches the agent verbatim — write that message for the agent.

### REGISTRY
```python
# SOURCE: framegrab_mcp_server.py:29-30
# Cache to store created FrameGrabbers, maps name to FrameGrabber
_grabber_cache = {}
```
One module-level dict is the whole registry. Phone cameras go **in this dict** — no second registry, no branching in tools.

### TEST_STRUCTURE
None exists in the repo. Use plain pytest functions in one file at repo root; run with `uv run --with pytest pytest -q` (no new dev dependency).

### FORMATTING
No ruff/black config in `pyproject.toml`; existing code is black-style at 88 columns. Use `uvx ruff format` + `uvx ruff check` defaults. (The CI auto-format workflow targets a non-existent `src/` and is a no-op.)

---

## HTTP Contract (shared by server and page)

| Method + path | Request | Responses |
|---|---|---|
| `GET /` | — | `200 text/html` the phone page |
| `POST /api/cameras` | JSON `{"name": "left bench"}` | `201 {"name"}` created · `200 {"name"}` already a phone camera (reconnect) · `409 {"error"}` name belongs to a non-phone grabber · `422 {"error"}` bad JSON / bad name · `429 {"error"}` more than 16 phone cameras |
| `POST /api/cameras/{name}/frame` | body = raw JPEG bytes | `200 {"hires_requested": false}` · `400` not a JPEG · `404` unknown name (server restarted → page re-registers) · `410` released by the agent (page stops) · `413` over 20 MB |

Name rule: `^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$`. `hires_requested` is always `false` until phase 5; the page ignores it. Phase 4 will add a PIN to the register call and a token to frame posts — do not build that now.

---

## Files to Change

| File | Action | Responsibility |
|---|---|---|
| `framegrab_mcp_phone.py` | CREATE | `PhoneCamera` (grabber duck-type), cert ensure, Starlette app (contract above), `PhoneCameraServer.start/stop` (uvicorn thread). Imports nothing from `framegrab_mcp_server` — the registry dict is passed in |
| `framegrab_mcp_phone.html` | CREATE | The phone page. Vanilla HTML/CSS/JS, no build step |
| `test_phone_cameras.py` | CREATE | The one test file |
| `framegrab_mcp_server.py` | UPDATE | Two env constants; start/stop the phone server in `app_lifespan` |
| `pyproject.toml` | UPDATE | Add `cryptography>=42`, `platformdirs`, explicit `uvicorn`, `starlette`; hatch wheel `include` |
| `README.md` | UPDATE | "(experimental) Phone cameras" section after the autodiscovery section |
| `uv.lock` | UPDATE (generated) | `uv lock`. Expect unrelated reformatting noise (lock revision 2→3) from newer uv — acceptable |

## NOT Building

- PIN / auth / tokens, QR code, join MCP tool, PC join page — phase 4.
- On-demand hi-res, preview downscaling — phase 5. Page sends full-resolution frames at ~1/s for now (~460 KB each on the Pixel).
- `--no-tls` flag, tunnels — PRD "Could".
- New MCP tools, changes to existing tool signatures, `mcp` version bump.
- Auto-start on page load, rename/remove from the phone, multi-camera-per-phone.
- Version bump / publish — release is the maintainer's call.
- Config knobs for stale timeout, max cameras, max frame size — module constants until someone needs to tune them. Port **is** a knob (conflicts are real).

---

## Step-by-Step Tasks

### Task 0: Branch
- **ACTION**: `git checkout -b feat/phone-cameras`
- **VALIDATE**: `git branch --show-current`

### Task 1: Dependencies and packaging
- **ACTION**: Edit `pyproject.toml`.
- **IMPLEMENT**:
  ```toml
  dependencies = [
      "mcp[cli]", "framegrab>=0.11.1", "pypylon", "streamlink",
      "uvicorn", "starlette", "cryptography>=42", "platformdirs",
  ]

  [tool.hatch.build.targets.wheel]
  include = ["framegrab_mcp_server.py", "framegrab_mcp_phone.py", "framegrab_mcp_phone.html"]
  ```
  Keep the existing one-line style of `dependencies` if it fits; otherwise wrap as above. Then `uv lock && uv sync`.
- **GOTCHA**: `uvicorn`, `starlette`, `platformdirs` are already installed transitively; declaring them makes the import legal, it doesn't add downloads. `cryptography>=42` is needed for `cert.not_valid_after_utc`.
- **VALIDATE**: `uv run python -c "import cryptography, platformdirs, uvicorn, starlette; print(cryptography.__version__)"` prints ≥ 42.

### Task 2: Failing test — register → frame → grab roundtrip
- **ACTION**: Create `test_phone_cameras.py`.
- **IMPLEMENT**:
  ```python
  import cv2
  import numpy as np
  import pytest
  from starlette.testclient import TestClient

  import framegrab_mcp_phone as phone


  def jpeg(w=64, h=48) -> bytes:
      ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 127, np.uint8))
      assert ok
      return buf.tobytes()


  @pytest.fixture
  def ctx():
      registry = {}
      server = phone.PhoneCameraServer(registry)
      return registry, TestClient(server.app)


  def test_register_post_grab_roundtrip(ctx):
      registry, client = ctx
      assert client.post("/api/cameras", json={"name": "left bench"}).status_code == 201
      assert client.post("/api/cameras", json={"name": "left bench"}).status_code == 200
      r = client.post("/api/cameras/left bench/frame", content=jpeg())
      assert r.status_code == 200 and r.json() == {"hires_requested": False}
      assert registry["left bench"].grab().shape == (48, 64, 3)
      assert registry["left bench"].config["input_type"] == "phone_browser"
  ```
- **VALIDATE**: `uv run --with pytest pytest -q` fails with `ModuleNotFoundError: framegrab_mcp_phone`.

### Task 3: `PhoneCamera` + app — make Task 2 pass
- **ACTION**: Create `framegrab_mcp_phone.py`.
- **IMPLEMENT**:
  ```python
  import logging
  import re
  import time
  from pathlib import Path

  import cv2
  import numpy as np
  from starlette.applications import Starlette
  from starlette.requests import Request
  from starlette.responses import JSONResponse, Response
  from starlette.routing import Route

  logger = logging.getLogger(__name__)

  MAX_FRAME_BYTES = 20 * 1024 * 1024
  MAX_PHONE_CAMERAS = 16
  STALE_AFTER_S = 10.0
  NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")
  PAGE = Path(__file__).with_suffix(".html").read_bytes()


  class PhoneCamera:
      """A phone browser posting JPEG frames. Duck-types the parts of
      framegrab.FrameGrabber that the MCP tools use: grab, config, apply_options, release."""

      def __init__(self, name: str, on_release):
          self.name = name
          self._on_release = on_release
          self._latest: tuple[bytes, float] | None = None  # one tuple so put/grab need no lock

      def put(self, jpeg: bytes) -> None:
          self._latest = (jpeg, time.monotonic())

      def grab(self) -> np.ndarray:
          latest = self._latest
          if latest is None:
              raise RuntimeError(f"Phone camera '{self.name}' has not sent a frame yet.")
          jpeg, received_at = latest
          age = time.monotonic() - received_at
          if age > STALE_AFTER_S:
              raise RuntimeError(
                  f"Phone camera '{self.name}' last sent a frame {age:.0f}s ago. "
                  "Is the page still open on the phone?"
              )
          frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
          if frame is None:
              raise RuntimeError(f"Phone camera '{self.name}' sent an undecodable image.")
          return frame

      @property
      def config(self) -> dict:
          return {"name": self.name, "input_type": "phone_browser"}

      def apply_options(self, options: dict) -> None:
          raise ValueError("Phone cameras have no configurable options.")

      def release(self) -> None:
          self._on_release(self.name)
  ```
  `PhoneCameraServer.__init__(self, registry: dict)` stores `self.registry`, `self._released: set[str] = set()`, and builds `self.app = Starlette(routes=[Route("/", self._page), Route("/api/cameras", self._register, methods=["POST"]), Route("/api/cameras/{name}/frame", self._frame, methods=["POST"])])`.

  Handlers (async methods):
  - `_page`: `Response(PAGE, media_type="text/html")`.
  - `_register`: `try: name = (await request.json())["name"]` / `except Exception:` → 422. `if not isinstance(name, str) or not NAME_RE.fullmatch(name)` → 422 with `{"error": "Name must be 1-40 letters, digits, spaces, _ or -."}`. `existing = self.registry.get(name)`; PhoneCamera → discard from `_released`, 200; other non-None → 409 `{"error": f"'{name}' is already used by another camera."}`; count of `PhoneCamera` values ≥ `MAX_PHONE_CAMERAS` → 429; else create `PhoneCamera(name, self._released.add)`, store, `_released.discard(name)`, `logger.info(f"Phone camera registered: {name}")`, 201.
  - `_frame`: `name = request.path_params["name"]`; in `_released` → 410; `cam = self.registry.get(name)`; not a `PhoneCamera` → 404; `body = await _read_capped(request, MAX_FRAME_BYTES)`; `None` → 413; `body[:2] != b"\xff\xd8"` → 400; `cam.put(body)`; `JSONResponse({"hires_requested": False})`.
  ```python
  async def _read_capped(request: Request, limit: int) -> bytes | None:
      declared = request.headers.get("content-length", "")
      if declared.isdigit() and int(declared) > limit:
          return None
      chunks, size = [], 0
      async for chunk in request.stream():
          size += len(chunk)
          if size > limit:
              return None
          chunks.append(chunk)
      return b"".join(chunks)
  ```
  Reference `MAX_FRAME_BYTES` / `STALE_AFTER_S` as module globals at call time (not default args) so tests can monkeypatch them.
- **MIRROR**: LOGGING_PATTERN, ERROR_HANDLING.
- **GOTCHA**: `PAGE` is read at import → Task 3 needs a placeholder `framegrab_mcp_phone.html` (`<!doctype html><title>framegrab phone camera</title>`) until Task 9. Check-then-set in `_register` has no `await` between check and set, so it is atomic on uvicorn's single loop; cross-thread access from MCP tools is single dict operations. Add one comment: `# ponytail: relies on the GIL + single event loop; add a lock if handlers ever await mid-update.`
- **VALIDATE**: `uv run --with pytest pytest -q` → 1 passed.

### Task 4: Failing tests — trust-boundary rejections
- **ACTION**: Add to `test_phone_cameras.py`:
  ```python
  @pytest.mark.parametrize("name", ["", " x", "a" * 41, "../etc", "na/me", 5, None])
  def test_bad_names_rejected(ctx, name):
      assert ctx[1].post("/api/cameras", json={"name": name}).status_code == 422


  def test_bad_json_rejected(ctx):
      assert ctx[1].post("/api/cameras", content=b"nope").status_code == 422


  def test_name_owned_by_other_grabber_conflicts(ctx):
      registry, client = ctx
      registry["usb0"] = object()
      assert client.post("/api/cameras", json={"name": "usb0"}).status_code == 409
      assert client.post("/api/cameras/usb0/frame", content=jpeg()).status_code == 404


  def test_frame_rejections(ctx, monkeypatch):
      registry, client = ctx
      assert client.post("/api/cameras/ghost/frame", content=jpeg()).status_code == 404
      client.post("/api/cameras", json={"name": "cam"})
      assert client.post("/api/cameras/cam/frame", content=b"GIF89a").status_code == 400
      monkeypatch.setattr(phone, "MAX_FRAME_BYTES", 10)
      assert client.post("/api/cameras/cam/frame", content=jpeg()).status_code == 413


  def test_camera_cap(ctx, monkeypatch):
      monkeypatch.setattr(phone, "MAX_PHONE_CAMERAS", 1)
      assert ctx[1].post("/api/cameras", json={"name": "a"}).status_code == 201
      assert ctx[1].post("/api/cameras", json={"name": "b"}).status_code == 429
  ```
- **VALIDATE**: run; fix the implementation (not the tests) until green. If Task 3 was implemented as specified these should pass immediately — that is fine; they are the regression net.

### Task 5: Failing tests — stale and release semantics
- **ACTION**: Add:
  ```python
  def test_grab_errors_are_actionable(ctx, monkeypatch):
      registry, client = ctx
      client.post("/api/cameras", json={"name": "cam"})
      with pytest.raises(RuntimeError, match="has not sent a frame"):
          registry["cam"].grab()
      client.post("/api/cameras/cam/frame", content=jpeg())
      monkeypatch.setattr(phone, "STALE_AFTER_S", -1)
      with pytest.raises(RuntimeError, match="Is the page still open"):
          registry["cam"].grab()


  def test_release_stops_phone_until_it_rejoins(ctx):
      registry, client = ctx
      client.post("/api/cameras", json={"name": "cam"})
      registry.pop("cam").release()  # what release_framegrabber does
      assert client.post("/api/cameras/cam/frame", content=jpeg()).status_code == 410
      assert client.post("/api/cameras", json={"name": "cam"}).status_code == 201
      assert client.post("/api/cameras/cam/frame", content=jpeg()).status_code == 200
  ```
- **VALIDATE**: green.

### Task 6: Cert + threaded TLS server — failing test first
- **ACTION**: Add the integration test, then port the verified prototype.
- **IMPLEMENT (test)**:
  ```python
  import ssl
  import urllib.request


  def test_tls_server_starts_serves_and_stops(tmp_path):
      server = phone.PhoneCameraServer({}, cert_dir=tmp_path)
      url = server.start(host="127.0.0.1", port=0)
      try:
          assert url.startswith("https://")
          port = url.rsplit(":", 1)[1].rstrip("/")
          tls = ssl.create_default_context(cafile=str(tmp_path / "cert.pem"))  # verifies the SAN
          body = urllib.request.urlopen(f"https://127.0.0.1:{port}/", context=tls, timeout=5).read()
          assert b"<!doctype html>" in body.lower()
          # port already taken → disabled, not crashed
          assert phone.PhoneCameraServer({}, cert_dir=tmp_path).start(host="127.0.0.1", port=int(port)) is None
      finally:
          server.stop()
  ```
- **IMPLEMENT (code)**: copy `ensure_cert` **verbatim** from `.claude/PRPs/plans/assets/proto_server.py` (rename `_ensure_cert`). Copy `lan_ip()` from `tls_spike.py:61-65`, wrapped in `try/except OSError: return "127.0.0.1"` (no network → still start). Then:
  ```python
  def __init__(self, registry: dict, cert_dir: Path | None = None):
      ...
      self._cert_dir = cert_dir or Path(platformdirs.user_data_dir("framegrab-mcp-server"))
      self._server: uvicorn.Server | None = None
      self._thread: threading.Thread | None = None

  def start(self, host: str = "0.0.0.0", port: int = 8443) -> str | None:
      ip = _lan_ip()
      sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      try:
          sock.bind((host, port))
      except OSError as e:
          sock.close()
          logger.error(f"Phone cameras disabled: cannot listen on port {port}: {e}")
          return None
      cert, key = _ensure_cert(self._cert_dir, ip)
      config = uvicorn.Config(
          self.app, ssl_certfile=str(cert), ssl_keyfile=str(key),
          log_config=None, access_log=False,  # uvicorn's defaults log to stdout = the MCP channel
      )
      self._server = uvicorn.Server(config)
      self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [sock]}, daemon=True)
      self._thread.start()
      deadline = time.monotonic() + 5
      while not self._server.started and time.monotonic() < deadline:
          time.sleep(0.05)
      url = f"https://{ip}:{sock.getsockname()[1]}/"
      logger.info(f"Phone cameras: open {url} on a phone on the same network.")
      return url

  def stop(self) -> None:
      if self._server:
          self._server.should_exit = True
          self._thread.join(timeout=5)
  ```
- **IMPORTS**: `datetime, ipaddress, socket, threading`, `platformdirs`, `uvicorn`, `from cryptography import x509`, `from cryptography.hazmat.primitives import hashes, serialization`, `from cryptography.hazmat.primitives.asymmetric import ec`, `from cryptography.x509.oid import NameOID`.
- **GOTCHA**: the port-conflict half of the test binds a second socket on a port that is *listening*; `SO_REUSEADDR` does not allow that on Linux (prototype saw `OSError 98`). On Windows `SO_REUSEADDR` **does** allow it — if Windows support matters later use `SO_EXCLUSIVEADDRUSE` there; note it, don't build it. The SAN always includes `127.0.0.1` + `localhost` + LAN IP; when the LAN IP changes the cert is regenerated and phones see the warning once more — expected.
- **VALIDATE**: `uv run --with pytest pytest -q` green, and `uv run --with pytest pytest -q 2>/dev/null | head -1` shows only pytest's own output (no uvicorn lines on stdout).

### Task 7: Wire into the MCP server — failing test first
- **ACTION**: Test that a phone camera works through the real tool, then add the lifespan wiring.
- **IMPLEMENT (test)**:
  ```python
  def test_grab_frame_tool_serves_phone_camera():
      import framegrab_mcp_server as srv

      client = TestClient(phone.PhoneCameraServer(srv._grabber_cache).app)
      try:
          client.post("/api/cameras", json={"name": "tool-test"})
          client.post("/api/cameras/tool-test/frame", content=jpeg())
          assert "tool-test" in srv.list_framegrabbers()
          assert srv.grab_frame("tool-test", "jpg").data[:2] == b"\xff\xd8"
          assert srv.release_framegrabber("tool-test") is True
          assert client.post("/api/cameras/tool-test/frame", content=jpeg()).status_code == 410
      finally:
          srv._grabber_cache.pop("tool-test", None)
  ```
  This should pass with **no** server-module changes — that is the proof the duck-typing works. Then add the wiring:
  ```python
  # after FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE
  ENABLE_FRAMEGRAB_PHONE_CAMERAS = (
      os.getenv("ENABLE_FRAMEGRAB_PHONE_CAMERAS", "false").lower() == "true"
  )
  FRAMEGRAB_PHONE_CAMERAS_PORT = int(os.getenv("FRAMEGRAB_PHONE_CAMERAS_PORT", "8443"))
  ```
  In `app_lifespan`, after the autodiscovery block and before the "has started" log:
  ```python
  phone_server = None
  if ENABLE_FRAMEGRAB_PHONE_CAMERAS:
      try:
          phone_server = PhoneCameraServer(_grabber_cache)
          phone_server.start(port=FRAMEGRAB_PHONE_CAMERAS_PORT)
      except Exception:
          logger.error("Error starting phone camera server.", exc_info=True)
  ```
  After `yield {}`, before releasing grabbers: `if phone_server: phone_server.stop()`.
- **IMPORTS**: `from framegrab_mcp_phone import PhoneCameraServer` (top of file, after the `mcp` import).
- **MIRROR**: CONFIG_VIA_ENV, LIFESPAN_START_STOP (broad `except Exception` + `exc_info=True` matches line 43-44: an optional feature must never stop the MCP server from starting).
- **GOTCHA**: `Image.data` is the attribute name in mcp 1.6 (`Image(data=..., format=...)`, server file line 133). If the assertion on `.data` fails, inspect `mcp.server.fastmcp.utilities.types.Image` rather than changing the tool.
- **VALIDATE**: tests green; then
  ```bash
  ENABLE_FRAMEGRAB_PHONE_CAMERAS=true timeout 6 uv run framegrab_mcp_server.py </dev/null >out.txt 2>err.txt; wc -c out.txt; grep 'Phone cameras' err.txt; rm out.txt err.txt
  ```
  EXPECT `0 out.txt` and the "open https://…:8443/" line. (stdin at EOF makes the stdio server exit by itself; `timeout` is a backstop.)

### Task 8: curl smoke test against the real process
- **ACTION**: In one terminal `ENABLE_FRAMEGRAB_PHONE_CAMERAS=true uv run framegrab_mcp_server.py` held open with `sleep 60 |` on stdin; in another:
  ```bash
  curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:8443/                                            # 200
  curl -sk -w ' %{http_code}\n' -H 'content-type: application/json' -d '{"name":"curl cam"}' https://localhost:8443/api/cameras   # 201
  curl -sk -w ' %{http_code}\n' --data-binary @assets/framegrab-mcp-in-action.png 'https://localhost:8443/api/cameras/curl%20cam/frame'  # 400 (PNG)
  ```
- **VALIDATE**: codes as annotated. This is the PRD phase-2 success signal minus the JPEG (covered by tests).

### Task 9: The phone page
- **ACTION**: Replace the placeholder `framegrab_mcp_phone.html`.
- **IMPLEMENT**: one file, inline `<style>` and `<script>`, no external resources.
  - **Markup**: `<meta name=viewport content="width=device-width,initial-scale=1">`; `<h1>`; `<label for=name>Camera name</label><input id=name maxlength=40 autocomplete=off>`; `<label for=device>Camera</label><select id=device disabled>`; `<button id=start>Start</button>`; `<button id=torch hidden aria-pressed=false>Torch</button>`; `<p id=status role=status aria-live=polite>`; `<video id=video playsinline autoplay muted>`. Inputs/buttons `font-size:16px` minimum (iOS zooms on smaller), tap targets ≥ 44 px, `color-scheme: light dark`.
  - **State**: `let stream, track, running = false, sent = 0`. `store(k, v)` / `load(k)` wrap `localStorage` in try/catch. On load: `name.value = load('name') || ''`; if `!isSecureContext || !navigator.mediaDevices` → status "This page needs HTTPS. Open the https:// address and accept the certificate warning." and disable Start.
  - **`start()`** (Start click): validate name against the same regex → else status + return. `startBtn.disabled = true` (**single-shot** — spike finding). `hint = setTimeout(() => status("Waiting for the camera… is this page open in another tab?"), 5000)`. `await openCamera(load('deviceId'))`; `clearTimeout(hint)`. Populate `<select>` from `enumerateDevices()` filtered to `videoinput` (labels exist only after permission), select the active `track.getSettings().deviceId`, enable it. `await register()`; on failure show `error` from the JSON body, `stopAll()`, re-enable Start, return. `store('name', …)`. `requestWakeLock()`. `running = true; loop()`. Wrap the whole body in try/catch → `status(e.name + ': ' + e.message)`, `stopAll()`, re-enable Start.
  - **`openCamera(deviceId)`**: stop existing tracks; `getUserMedia({video: deviceId ? {deviceId: {exact: deviceId}, width: {ideal: 4096}, height: {ideal: 2160}} : {facingMode: 'environment', width: {ideal: 4096}, height: {ideal: 2160}}})`; if the `exact` request throws `OverconstrainedError`/`NotFoundError`, retry once without `deviceId` (stored id from another phone/browser profile). `video.srcObject = stream; await video.play()`. `track = stream.getVideoTracks()[0]`. `torch.hidden = !(track.getCapabilities && 'torch' in track.getCapabilities())`.
  - **`register()`**: `fetch('/api/cameras', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({name})})` → ok if 200/201.
  - **`loop()`** — self-scheduling, never `setInterval` (no pile-up on slow Wi-Fi):
    ```js
    async function loop() {
      if (!running) return;
      try {
        canvas.width = video.videoWidth; canvas.height = video.videoHeight;
        canvas.getContext('2d').drawImage(video, 0, 0);
        const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.85));
        const res = await fetch('/api/cameras/' + encodeURIComponent(name) + '/frame', {method: 'POST', body: blob});
        if (res.status === 404) { await register(); }               // server restarted: rejoin silently
        else if (res.status === 410) { running = false; stopAll(); startBtn.disabled = false;
                                       status('Released by the agent. Tap Start to rejoin.'); return; }
        else if (res.ok) { status('Live as "' + name + '" · ' + (++sent) + ' frames sent'); }
        else { status('Server rejected frame: ' + res.status); }
      } catch (e) { status('Reconnecting… (' + e.message + ')'); }
      setTimeout(loop, 1000);
    }
    ```
  - **Camera `<select>` change**: `store('deviceId', value); await openCamera(value)` (loop keeps running; it reads `video` each tick).
  - **Torch click**: toggle `aria-pressed`; `track.applyConstraints({advanced: [{torch: on}]})`; on rejection hide the button.
  - **Wake lock**: `requestWakeLock()` = `if ('wakeLock' in navigator) navigator.wakeLock.request('screen').catch(() => {})`; `document.addEventListener('visibilitychange', () => { if (running && document.visibilityState === 'visible') requestWakeLock(); })`.
  - **`stopAll()`**: stop tracks, `video.srcObject = null`, hide torch.
- **GOTCHA**: skip a tick when `video.videoWidth === 0` (camera switching) — a 0×0 canvas makes `toBlob` yield `null` and `fetch` would POST the string "null" → 400. `encodeURIComponent` is required (names may contain spaces). Do not add `capture`/`ImageCapture` — phase 5.
- **VALIDATE**: `uv run --with pytest pytest -q` still green (page is served); then Task 10.

### Task 10: On-device validation (Pixel over adb)
- **ACTION**: Start the server as in Task 8 (long `sleep` on stdin). Phone already trusts nothing at the new cert → expect one warning. Then:
  ```bash
  adb shell am start -a android.intent.action.VIEW -d 'https://<lan-ip>:8443/' com.android.chrome
  adb exec-out screencap -p > /tmp/…/scratchpad/page.png    # look at it
  ```
  Type the name and tap Start by hand (or `adb shell input text` / `input tap` using screenshot coordinates ×1.5). Drive checks from the PC with a throwaway script that imports nothing — just use the tools through `mcp dev`/inspector, or simplest: temporarily `curl` is not enough here because frames live in-process; use `make mcp-inspector` with the env var set and call `list_framegrabbers` + `grab_frame`.
- **VALIDATE** (record results in the report):
  - [ ] `list_framegrabbers` shows the typed name; `grab_frame` returns a current image of what the phone sees
  - [ ] second tap on Start impossible (button disabled)
  - [ ] open the URL in a 2nd tab + Start → "open in another tab?" hint after 5 s
  - [ ] camera `<select>` lists the Pixel's lenses; switching changes the image; torch button toggles the LED
  - [ ] `release_grabber` → page shows "Released by the agent", Start re-enabled; Start → back in the list
  - [ ] restart the server process → page shows "Reconnecting…" then resumes without touching the phone; name is back in `list_framegrabbers`
  - [ ] close page → within ~10 s `grab_frame` fails with "Is the page still open on the phone?"
- **GOTCHA**: Chrome remote debugging (`adb forward tcp:9222 localabstract:chrome_devtools_remote`; `curl localhost:9222/json`) lists/closes tabs if stale spike tabs hold the camera. Remove the forward afterwards.

### Task 11: README
- **ACTION**: Add `### (experimental) Phone cameras` after the autodiscovery section, same shape (paragraph + JSON config block with `"ENABLE_FRAMEGRAB_PHONE_CAMERAS": "true"`). State: opens HTTPS on port 8443 (`FRAMEGRAB_PHONE_CAMERAS_PORT`); open `https://<pc-ip>:8443/` on the phone, accept the self-signed-certificate warning once, allow the camera, name it, Start; the name then works with `grab_frame`. State plainly: **no authentication yet — anyone on the network can register a camera; enable only on networks you trust.** State: tested on Android Chrome; iOS Safari untested. Keep the phone on a charger.
- **VALIDATE**: renders; no claim of iOS support (PRD debt D1).

### Task 12: Packaging check
- **ACTION**: `uv build --no-sources --wheel -o /tmp/…/scratchpad/dist && python3 -m zipfile -l /tmp/…/scratchpad/dist/*.whl`
- **VALIDATE**: lists `framegrab_mcp_server.py`, `framegrab_mcp_phone.py`, `framegrab_mcp_phone.html`. If the html is missing the installed package crashes at import (`PAGE = …read_bytes()`), so this check is not optional.

### Task 13: Format, lint, final run
- **ACTION**: `uvx ruff format framegrab_mcp_phone.py framegrab_mcp_server.py test_phone_cameras.py && uvx ruff check . && uv run --with pytest pytest -q`
- **VALIDATE**: clean, all green. `git diff --stat` touches only the files in "Files to Change".

---

## Testing Strategy

### Unit / integration tests (`test_phone_cameras.py`)

| Test | Input | Expected | Edge? |
|---|---|---|---|
| roundtrip | register ×2, post JPEG, `grab()` | 201, 200, 200, ndarray (48,64,3) | |
| bad names | `""`, leading space, 41 chars, `../etc`, `na/me`, `5`, `null` | 422 | yes |
| bad JSON | `nope` | 422 | yes |
| name collision | registry has non-phone `usb0` | register 409, frame 404 | yes |
| frame rejections | unknown name / GIF bytes / over limit | 404 / 400 / 413 | yes |
| camera cap | cap=1, second register | 429 | yes |
| actionable grab errors | no frame yet / stale | RuntimeError with agent-readable text | yes |
| release semantics | pop+release → frame → register → frame | 410 → 201 → 200 | yes |
| TLS server | start port 0, verifying client, port conflict, stop | html served, second start `None`, thread stops | yes |
| tool integration | real `grab_frame` / `list_framegrabbers` / `release_framegrabber` | JPEG bytes, listed, True, then 410 | |

### Edge Cases Checklist
- [x] Empty / invalid input — names, JSON, non-JPEG
- [x] Maximum size input — 413 via declared length and via streamed overflow
- [x] Invalid types — non-string name
- [x] Concurrent access — single-tuple swap in `PhoneCamera`; registry single-op; documented ceiling
- [x] Network failure — page "Reconnecting…" loop; server restart → 404 → silent re-register (device check)
- [x] Permission denied — page shows `NotAllowedError` and re-enables Start (device check)
- [ ] Two phones, same name — second silently takes over (both get 200). Accepted until phase 4 tokens; noted in Risks

---

## Validation Commands

### Static Analysis
```bash
uvx ruff format --check framegrab_mcp_phone.py framegrab_mcp_server.py test_phone_cameras.py && uvx ruff check .
```
EXPECT: clean. (No type checker is configured in this repo.)

### Tests
```bash
uv run --with pytest pytest -q
```
EXPECT: all pass.

### stdout purity (MCP channel)
```bash
ENABLE_FRAMEGRAB_PHONE_CAMERAS=true timeout 6 uv run framegrab_mcp_server.py </dev/null 2>/dev/null | wc -c
```
EXPECT: `0`

### Build
```bash
uv build --no-sources --wheel -o "$SCRATCH/dist" && python3 -m zipfile -l "$SCRATCH"/dist/*.whl
```
EXPECT: both `.py` files and the `.html`.

### Manual Validation
- [ ] Task 10 checklist on the Pixel

---

## Acceptance Criteria
- [ ] With the env var unset, behaviour is byte-for-byte unchanged: no port opened, no cert written
- [ ] With it set: a phone joins under a name and `grab_frame(name)` returns its current view
- [ ] Existing tools' signatures and descriptions untouched
- [ ] All tests pass; ruff clean; stdout purity check is 0 bytes; wheel contains 3 files
- [ ] Task 10 device checklist recorded (pass/fail per line) in the report
- [ ] README documents the feature, the missing auth, and the untested iOS status

## Completion Checklist
- [ ] `framegrab_mcp_phone.py` imports nothing from `framegrab_mcp_server`
- [ ] No `print`, no uvicorn default logging
- [ ] Error messages written for the agent (tool side) and for the person holding the phone (page side)
- [ ] No PIN/QR/hi-res code snuck in
- [ ] `ponytail:` comment on the lock-free registry assumption
- [ ] PRD phases 2 and 3 updated; Open Question 3 marked resolved (`cryptography`)

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Unauthenticated LAN endpoint ships before phase 4 | Certain (by design) | Anyone on the LAN can add/overwrite phone cameras or fill memory with 16 × 20 MB frames | Off by default; README warning; caps on count and size; name rule blocks path tricks. Do not publish a release advertising the feature until phase 4 lands |
| Same-name takeover by a second phone | L | Agent sees the wrong bench | Phase 4 session token binds a name to the phone that registered it |
| Full-res JPEG at 1 fps heats/drains the phone over 2 h | M | Fails PRD survival metric | Unknown until the phase 6 soak; phase 5 introduces a small preview + on-demand full-res, which is the fix |
| `uv lock` rewrites the lockfile format (rev 2→3) | H | Noisy diff in a Groundlight-owned repo | Mention in the PR description; or lock with the uv version pinned in their CI if one is found |
| iOS behaves differently (debt D1) | M | Page may need iOS-specific fixes | Page already uses `playsinline`, 16 px inputs, gesture-initiated start; test when a device is available |
| Windows: `SO_REUSEADDR` lets a second process bind the same port | L | Two servers, phones reach a random one | Documented in Task 6; fix with `SO_EXCLUSIVEADDRUSE` when Windows is actually targeted |
| Laptop changes Wi-Fi → LAN IP changes → cert regenerated | M | One extra warning tap per phone | Expected; logged at startup |

## Notes
- **Decision made in this plan**: certificate generation uses `cryptography` (PRD Open Question 3). It was the standing recommendation, the user did not object, and the prototype is verified with it. `openssl` shell-out rejected: absent on stock Windows.
- **Decision made in this plan**: feature is **opt-in** (`ENABLE_FRAMEGRAB_PHONE_CAMERAS`), mirroring `ENABLE_FRAMEGRAB_AUTO_DISCOVERY`. The package is published by Groundlight and used by others; silently opening a LAN port on upgrade would be wrong.
- **Verified during planning** (executed, Python 3.13.15, locked deps + `cryptography`): cert create / reuse / regenerate-on-IP-change; uvicorn thread on pre-bound socket with TLS; verifying client succeeds against the SAN; port conflict surfaces as `OSError` in our code; clean stop; 0 bytes on stdout. Wheel currently contains only `framegrab_mcp_server.py`.
- **Not verified**: the hatch `include` stanza (Task 12 checks it); everything in the page JS beyond what the spike page already proved on the Pixel (getUserMedia constraints, canvas→JPEG→fetch, wake lock, torch *capability*); torch *toggling*, `enumerateDevices` lens list, and wake-lock re-acquire are untested on device until Task 10.
- An agent can do Tasks 0-9 and 11-13 alone. Task 10 needs the Pixel connected over adb (it was during planning) and a human to prop/point it; adb can drive taps.
