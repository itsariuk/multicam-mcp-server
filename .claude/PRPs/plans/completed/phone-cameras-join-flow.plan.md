# Plan: Phone Cameras — Join Flow (PIN, QR, join tool)

## Summary
Close the unauthenticated hole left by phases 2-3 and make joining fast. A new MCP tool, `add_phone_camera`, gives the agent the link, a 6-digit PIN and a QR code, and opens a join page with the QR on the computer's screen. The phone scans it, the PIN arrives in the URL fragment, and after the first join the phone holds a token so reconnects (including after the MCP server restarts) need no PIN.

## User Story
As someone setting up a bench camera, I want to say "add a phone camera", scan a code, and tap Start, so that joining takes seconds and nobody else on the network can add or hijack cameras.

## Problem → Solution
Anyone on the LAN can register or overwrite a phone camera, and the user has to find and type `https://192.168.x.x:8443/` by hand → joining requires a PIN that only the person at the computer can see, and the URL + PIN travel by QR code.

## Metadata
- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/phone-browser-cameras.prd.md`
- **PRD Phase**: 4 — Join flow
- **Estimated Files**: 5 updated, 0 new (the join page is generated, not a source file)

---

## UX Design

### Before
```
find the PC's IP → type https://192.168.2.150:8443/ on the phone → cert warning
→ name → Start                       (anyone on the LAN can do the same)
```

### After
```
"add a phone camera" → agent calls add_phone_camera
   → browser tab opens on the PC: big QR + PIN + 3 steps
   → agent can also say: "open <url>, the PIN is 4 8 2 9 1 3"
phone: scan QR → cert warning (first time) → PIN already filled in → name → Start
later: server restarts / page reloads → phone rejoins with its token, no PIN
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| MCP tools | 6 tools | + `add_phone_camera(open_browser=True)` | Returns text (URL + PIN, speakable) **and** a QR image. Works even if the client can't show images, because the join page opens on the PC |
| Phone page | name + Start | + PIN field, prefilled from `#pin=` and then removed from the address bar | PIN only needed the first time a name is used from that phone |
| `POST /api/cameras` | open | needs a valid token for that name, or the current PIN → returns `token` | 401 wrong/missing, 429 after 5 wrong PINs in 60 s from one address |
| `POST …/frame` | open | needs `Authorization: Bearer <token>` for that name | 401 otherwise; page halts and asks for the PIN |
| Feature disabled / port taken | — | tool raises an error telling the agent how to enable it | |
| Same-name takeover | any phone | only a phone that knows the PIN | closes the phase 2-3 risk |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `multicam_mcp_phone.py` | 158-218 | `PhoneCameraServer.__init__`, `_register`, `_frame` — where auth goes; note the `ponytail:` comment and the `_released` tombstones |
| P0 | `multicam_mcp_phone.py` | 109-155 | `_ensure_cert`: the tolerant-load + temp-file + `os.replace` + `0o600` pattern to copy for the token secret and the join page |
| P0 | `multicam_mcp_server.py` | 30-68 | Env constants, `_grabber_cache` global, lifespan local `phone_server` (must become reachable from a tool) |
| P0 | `multicam_mcp_server.py` | 114-150 | `grab_frame`: how a tool is declared and returns an `Image` |
| P1 | `multicam_mcp_phone.html` | 49-53, 113-152, 154-190 | `load/store`, `register()`, `start()`, `loop()` and `halt()` — the places that gain PIN/token handling |
| P1 | `test_phone_cameras.py` | 18-23, 141-156 | `ctx` fixture and the tool-integration test — both change shape |
| P2 | `.claude/PRPs/reports/phone-cameras-ingest-and-page-report.md` | addenda | Device findings; adb/CDP technique for on-device checks |

## External Documentation

No web research needed. Verified locally (Python 3.13.15, locked deps):

```
KEY_INSIGHT: OpenCV can encode QR codes: cv2.QRCodeEncoder.create().encode(str) → 33×33 uint8 {0,255}.
APPLIES_TO: QR generation
GOTCHA: Output is 1 px per module with no quiet zone. Scale with INTER_NEAREST (×8) and add a 4-module white border (cv2.copyMakeBorder) or phones won't lock on. Round-trip verified with cv2.QRCodeDetector. No new dependency.

KEY_INSIGHT: FastMCP 1.6 converts a tool result of [str, Image] into [TextContent, ImageContent].
APPLIES_TO: add_phone_camera return value
GOTCHA: Return a plain list; don't wrap in a dict (that would be JSON-stringified).

KEY_INSIGHT: stdlib webbrowser's generic opener (xdg-open is registered as BackgroundBrowser on this machine) runs subprocess.Popen(cmdline) with stdout INHERITED.
APPLIES_TO: opening the join page
GOTCHA: stdout is the MCP stdio channel; a chatty browser would corrupt the protocol. Do NOT use webbrowser. Spawn the opener ourselves with stdin/stdout/stderr = DEVNULL and start_new_session=True (os.startfile on Windows).

KEY_INSIGHT: The stdio server is restarted whenever the MCP client restarts; phones currently rejoin untouched (verified on device).
APPLIES_TO: token design
GOTCHA: In-memory tokens would break that. Tokens must be verifiable after a restart → stateless HMAC over the camera name with a secret persisted next to server.pem. The PIN can stay per-start and in memory.

KEY_INSIGHT: URL fragments are never sent to the server and don't appear in server logs.
APPLIES_TO: carrying the PIN in the QR (https://ip:8443/#pin=123456)
GOTCHA: It does stay in the phone's address bar/history → page must read it then history.replaceState() it away.
```

---

## Patterns to Mirror

### PERSISTED_SECRET_FILE (tolerant load, atomic private write)
```python
# SOURCE: multicam_mcp_phone.py:116-127, 148-155
    if pem_path.exists():
        try:
            cert = x509.load_pem_x509_certificate(pem_path.read_bytes())
            ...
        except (ValueError, x509.ExtensionNotFound) as e:
            logger.warning(f"Stored certificate is unusable, replacing it: {e}")
    ...
    tmp_path = pem_path.with_suffix(".tmp")
    tmp_path.touch(mode=0o600)
    tmp_path.chmod(0o600)  # touch() does not change the mode of a leftover file
    tmp_path.write_bytes(...)
    os.replace(tmp_path, pem_path)
```
Extract the last five lines into `_write_private(path: Path, data: bytes)` and use it for `server.pem`, `token.key`, and `join.html` — three callers justifies the helper.

### HTTP_ERRORS
```python
# SOURCE: multicam_mcp_phone.py:81-82, 207-208
def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)
...
        if name in self._released:
            return _error(410, "This camera was released by the agent.")
```
Messages are for the person holding the phone; the page shows `body.error` verbatim.

### TOOL_DECLARATION + ACTIONABLE_ERRORS
```python
# SOURCE: multicam_mcp_server.py:114-127
@mcp.tool(
    name="grab_frame",
    description="Grab a frame from the specified framegrabber and return it as an image in the specified format.",
)
def grab_frame(
    framegrabber_name: str, format: Literal["png", "jpg", "webp"] = "webp"
) -> Image:
    grabber: FrameGrabber = _grabber_cache.get(framegrabber_name)
    if not grabber:
        raise ValueError(
            f"Framegrabber with name {framegrabber_name} not found. Options are: {list(_grabber_cache.keys())}."
        )
```

### MODULE_GLOBAL_STATE
```python
# SOURCE: multicam_mcp_server.py:36-37
# Cache to store created FrameGrabbers, maps name to FrameGrabber
_grabber_cache = {}
```
The running phone server becomes a second module global, `_phone_server`, set in `app_lifespan` via `global`.

### PAGE: one exit, storage that may fail
```js
// SOURCE: multicam_mcp_phone.html:49-51, 68-74
const load = key => { try { return localStorage.getItem(key); } catch (e) { return null; } };
const store = (key, value) => { try { localStorage.setItem(key, value); } catch (e) {} };
function halt(message) { running = false; stopAll(); startBtn.disabled = false; nameInput.disabled = false; status(message); }
```
Every new failure path ends in `halt(message)`. Add a `forget(key)` sibling (`removeItem` in try/catch).

### TEST_STRUCTURE
```python
# SOURCE: test_phone_cameras.py:18-23
@pytest.fixture
def ctx():
    registry = {}
    server = phone.PhoneCameraServer(registry)
    return registry, TestClient(server.app)
```
Plain pytest functions, `TestClient`, `monkeypatch.setattr(phone, "CONSTANT", …)` for limits. Run with `uv run pytest -q`.

---

## Auth design (the part that must be exactly right)

```python
PIN_MAX_FAILURES = 5
PIN_WINDOW_S = 60.0

# in PhoneCameraServer.__init__
self.pin = f"{secrets.randbelow(10**6):06d}"          # per start, memory only
self._secret = _load_secret(self._cert_dir)           # 32 bytes, persisted, survives restarts
self._pin_failures: dict[str, list[float]] = {}       # client address -> [count, window_start]
self.url: str | None = None                           # set by start()

def _token(self, name: str) -> str:
    return hmac.new(self._secret, name.encode(), "sha256").hexdigest()

def _has_token(self, request: Request, name: str) -> bool:
    sent = request.headers.get("authorization", "").removeprefix("Bearer ")
    return hmac.compare_digest(sent, self._token(name))
```
`_register` order: parse JSON → validate name (422) → **if `_has_token`: authorised** → else: locked out? (429 "Too many wrong PINs. Wait a minute.") → `pin` from body is a `str` and `hmac.compare_digest(pin, self.pin)`? if not, count the failure and 401 "Wrong PIN." (missing PIN: 401 "Enter the PIN shown on the computer.", **not** counted) → then the existing conflict/cap/create logic. Every success response gains `"token": self._token(name)`.

`_frame`: first line after reading `name`: `if not self._has_token(request, name): return _error(401, "Not authorised. Enter the PIN and tap Start.")`. Keep it **before** the 410/404 checks so an unauthenticated caller learns nothing about which names exist.

Failure counting: fixed window per `request.client.host if request.client else "unknown"`. Window expired → reset. If the dict ever exceeds 1000 entries, clear it (`# ponytail: crude memory bound; a LAN will never get here`).

Why this shape: the PIN proves "I can see the computer's screen"; the token proves "I joined this name before". Token = HMAC(secret, name), so nothing to store or expire, it survives restarts, and a token for `left bench` is useless for `right bench`. Deleting the data directory revokes every token.

---

## Files to Change

| File | Action | Responsibility of the change |
|---|---|---|
| `multicam_mcp_phone.py` | UPDATE | `_write_private`, `_load_secret`, PIN + token auth in `_register`/`_frame`, lockout, `self.url`, `join_url`, `qr_png()`, `write_join_page()`, `open_join_page()` |
| `multicam_mcp_server.py` | UPDATE | `_phone_server` global set/cleared in lifespan; `add_phone_camera` tool |
| `multicam_mcp_phone.html` | UPDATE | PIN field, `#pin=` handling, token storage, `Authorization` header, 401 handling |
| `test_phone_cameras.py` | UPDATE | Fixture gives a `join()` helper; existing tests authenticate; new auth/QR/join-page/tool tests |
| `README.md` | UPDATE | Joining steps via the tool + QR; Security section rewritten; tools table; roadmap |

## NOT Building

- User accounts, per-phone PINs, PIN rotation on a timer, token expiry, a revoke tool (delete the data directory to revoke).
- Serving the join page over HTTP(S). It contains the PIN, so it is a private local file, never a route.
- Dropping silent cameras from `list_framegrabbers` (noted in the last report; still not needed).
- On-demand hi-res, preview downscaling (phase 5). `--no-tls` (PRD "Could").
- A config knob for PIN length, lockout numbers, or disabling auth.
- iOS-specific work (debt D1 stands).

---

## Step-by-Step Tasks

### Task 0: Branch
- **ACTION**: `git checkout -b feat/phone-join-flow` from `main` (clean apart from untracked `.claude/`).

### Task 1: Make the fixture authenticate — tests go red
- **ACTION**: In `test_phone_cameras.py` change the fixture and add a helper; update every existing test that registers or posts frames.
- **IMPLEMENT**:
  ```python
  @pytest.fixture
  def ctx(tmp_path):
      registry = {}
      server = phone.PhoneCameraServer(registry, cert_dir=tmp_path)
      return registry, TestClient(server.app), server


  def join(client, server, name) -> dict:
      """Register with the PIN; return the headers that authorise frame posts."""
      r = client.post("/api/cameras", json={"name": name, "pin": server.pin})
      assert r.status_code in (200, 201), r.text
      return {"authorization": "Bearer " + r.json()["token"]}
  ```
  Existing tests unpack three values; registrations go through `join(...)` or add `"pin": server.pin`; frame posts pass `headers=`. In `test_bad_names_rejected` / `test_bad_json_rejected` nothing changes (422 comes before auth). In `test_name_owned_by_other_grabber_conflicts` send the PIN so the 409 is reached; the frame post to `usb0` now expects **401** (no token exists for it) — update the assertion and say why in a comment. `test_grab_frame_tool_serves_phone_camera` takes `tmp_path` and passes `cert_dir=tmp_path`.
- **GOTCHA**: `cert_dir=tmp_path` everywhere — after this phase the constructor writes `token.key`, and tests must never touch the real user data directory.
- **VALIDATE**: `uv run pytest -q` → failures mentioning `pin` / `token` / `KeyError: 'token'`.

### Task 2: `_write_private` + `_load_secret`
- **ACTION**: Add both to `multicam_mcp_phone.py`; make `_ensure_cert` use `_write_private`.
- **IMPLEMENT**:
  ```python
  def _write_private(path: Path, data: bytes) -> None:
      """Write a file only this user can read, atomically."""
      path.parent.mkdir(parents=True, exist_ok=True)
      tmp_path = path.with_suffix(path.suffix + ".tmp")
      tmp_path.touch(mode=0o600)
      tmp_path.chmod(0o600)  # touch() does not change the mode of a leftover file
      tmp_path.write_bytes(data)
      os.replace(tmp_path, path)


  def _load_secret(cert_dir: Path) -> bytes:
      """The key that signs join tokens. Persisted so phones can rejoin after a restart."""
      path = cert_dir / "token.key"
      try:
          secret = path.read_bytes()
          if len(secret) == 32:
              return secret
          logger.warning("Stored token key is unusable, replacing it. Phones must re-enter the PIN.")
      except FileNotFoundError:
          pass
      secret = secrets.token_bytes(32)
      _write_private(path, secret)
      return secret
  ```
- **IMPORTS**: `hmac`, `secrets`.
- **GOTCHA**: Use `with_suffix(path.suffix + ".tmp")` (→ `server.pem.tmp`, `token.key.tmp`, `join.html.tmp`) so each file has its own temp name. Existing `test_unusable_stored_cert_is_replaced` must stay green after the refactor.
- **VALIDATE**: new tests
  ```python
  def test_token_key_is_persisted_private_and_self_healing(tmp_path):
      first = phone._load_secret(tmp_path)
      assert phone._load_secret(tmp_path) == first and len(first) == 32
      assert (tmp_path / "token.key").stat().st_mode & 0o777 == 0o600
      (tmp_path / "token.key").write_bytes(b"short")
      assert phone._load_secret(tmp_path) != first
  ```

### Task 3: PIN + token auth — make Task 1 green, then add the auth tests
- **ACTION**: Implement the "Auth design" section exactly. Then add:
  ```python
  def test_joining_needs_the_pin(ctx):
      _, client, server = ctx
      assert client.post("/api/cameras", json={"name": "cam"}).status_code == 401
      wrong = "000000" if server.pin != "000000" else "111111"
      assert client.post("/api/cameras", json={"name": "cam", "pin": wrong}).status_code == 401
      assert client.post("/api/cameras", json={"name": "cam", "pin": 123456}).status_code == 401  # not a str
      assert client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code == 201


  def test_frames_need_the_token_for_that_name(ctx):
      _, client, server = ctx
      cam, other = join(client, server, "cam"), join(client, server, "other")
      assert client.post("/api/cameras/cam/frame", content=jpeg()).status_code == 401
      assert client.post("/api/cameras/cam/frame", content=jpeg(), headers=other).status_code == 401
      assert client.post("/api/cameras/ghost/frame", content=jpeg()).status_code == 401  # no name oracle
      assert client.post("/api/cameras/cam/frame", content=jpeg(), headers=cam).status_code == 200


  def test_token_rejoins_without_pin_even_after_restart(tmp_path):
      first = phone.PhoneCameraServer({}, cert_dir=tmp_path)
      headers = join(TestClient(first.app), first, "cam")
      restarted = TestClient(phone.PhoneCameraServer({}, cert_dir=tmp_path).app)  # new PIN, same key
      assert restarted.post("/api/cameras", json={"name": "cam"}, headers=headers).status_code == 201
      assert restarted.post("/api/cameras/cam/frame", content=jpeg(), headers=headers).status_code == 200


  def test_wrong_pins_lock_out_then_recover(ctx, monkeypatch):
      _, client, server = ctx
      wrong = "000000" if server.pin != "000000" else "111111"
      for _ in range(phone.PIN_MAX_FAILURES):
          assert client.post("/api/cameras", json={"name": "cam", "pin": wrong}).status_code == 401
      assert client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code == 429
      monkeypatch.setattr(phone, "PIN_WINDOW_S", 0)
      assert client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code == 201
  ```
- **MIRROR**: HTTP_ERRORS.
- **GOTCHA**: `hmac.compare_digest` raises `TypeError` on non-ASCII `str` → check `isinstance(pin, str) and pin.isascii()` first; the Authorization header is already ASCII-safe via Starlette (latin-1) — guard with `sent.isascii()` too. A missing PIN is not a failed guess (a phone reloading with a stale token must not lock its owner out).
- **VALIDATE**: `uv run pytest -q` all green.

### Task 4: QR code + join page
- **ACTION**: Add to `PhoneCameraServer`; `start()` sets `self.url` (and `stop()`/failed start leave it `None`).
- **IMPLEMENT**:
  ```python
  @property
  def join_url(self) -> str:
      return f"{self.url}#pin={self.pin}"   # fragment: never sent to the server or logged

  def qr_png(self) -> bytes:
      modules = cv2.QRCodeEncoder.create().encode(self.join_url)
      image = cv2.resize(modules, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
      image = cv2.copyMakeBorder(image, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)  # quiet zone
      ok, png = cv2.imencode(".png", image)
      if not ok:
          raise RuntimeError("Failed to encode the QR code.")
      return png.tobytes()

  def write_join_page(self) -> Path:
      """A local file, not a route: it shows the PIN, so the network must not be able to fetch it."""
      path = self._cert_dir / "join.html"
      _write_private(path, JOIN_PAGE.format(
          url=html.escape(self.url), pin=self.pin,
          qr=base64.b64encode(self.qr_png()).decode()).encode())
      return path
  ```
  `JOIN_PAGE`: a module-level `str` template, one screen, no scripts: `<h1>Add a phone camera</h1>`, `<img alt="QR code that opens the phone camera page" src="data:image/png;base64,{qr}">` at 328 px, the PIN in large spaced digits, then three numbered steps: scan / "Your phone will warn that the connection is not private. This is expected — the computer uses its own certificate. Choose Advanced, then Proceed." / name it and tap Start. Show `{url}` as text for phones without a scanner. `color-scheme: light dark`, but keep the QR on a white background in both.
- **IMPORTS**: `base64`, `html`.
- **GOTCHA**: Literal `{`/`}` in the template's CSS must be doubled (`{{ }}`) because of `str.format`. `self.url` is `None` until `start()` — `join_url` on an unstarted server is a bug in the caller, not something to paper over.
- **VALIDATE**:
  ```python
  def test_qr_and_join_page_carry_the_link_and_pin(ctx, tmp_path):
      _, _, server = ctx
      server.url = "https://192.0.2.7:8443/"
      png = cv2.imdecode(np.frombuffer(server.qr_png(), np.uint8), cv2.IMREAD_GRAYSCALE)
      assert cv2.QRCodeDetector().detectAndDecode(png)[0] == f"https://192.0.2.7:8443/#pin={server.pin}"
      page = server.write_join_page()
      assert server.pin in page.read_text() and page.stat().st_mode & 0o777 == 0o600
  ```

### Task 5: Open the join page without touching stdout
- **ACTION**: Add `open_join_page()`.
- **IMPLEMENT**:
  ```python
  def open_join_page(self) -> None:
      path = self.write_join_page()
      try:
          if sys.platform == "win32":
              os.startfile(path)  # noqa: S606
          else:
              # Not the webbrowser module: it lets the browser inherit stdout, which is
              # the MCP stdio channel.
              subprocess.Popen(
                  ["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, start_new_session=True,
              )
      except OSError as e:
          logger.warning(f"Could not open the join page ({path}): {e}")
  ```
- **IMPORTS**: `subprocess`, `sys`.
- **VALIDATE**:
  ```python
  def test_join_page_opener_never_inherits_stdout(ctx, monkeypatch):
      _, _, server = ctx
      server.url = "https://192.0.2.7:8443/"
      calls = []
      monkeypatch.setattr(phone.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
      server.open_join_page()
      (cmd, kw), = calls
      assert cmd[-1].endswith("join.html") and kw["stdout"] == phone.subprocess.DEVNULL
  ```
  (Linux/macOS path; skip on `win32` with `pytest.mark.skipif`.)

### Task 6: The `add_phone_camera` tool
- **ACTION**: In `multicam_mcp_server.py`: add `_phone_server: PhoneCameraServer | None = None` under `_grabber_cache` with a comment; in `app_lifespan` declare `global _phone_server`, assign it **only if `start()` returned a URL**, and set it back to `None` after `stop()`. Replace the local `phone_server` variable. Add the tool after `release_grabber`.
- **IMPLEMENT**:
  ```python
  @mcp.tool(
      name="add_phone_camera",
      description="""Get what a person needs to connect a phone as a camera: a link, a PIN, and a QR code that carries both.
  By default it also opens a page with the QR code in this computer's browser. Tell the user the link and the PIN.
  Once the phone has joined, its name appears in list_framegrabbers and works with grab_frame.""",
  )
  def add_phone_camera(open_browser: bool = True) -> list:
      if _phone_server is None:
          raise ValueError(
              "Phone cameras are not running. Set ENABLE_FRAMEGRAB_PHONE_CAMERAS=true in this MCP server's "
              f"environment and restart it. If it is already set, port {FRAMEGRAB_PHONE_CAMERAS_PORT} may be in "
              "use by another copy of this server; set FRAMEGRAB_PHONE_CAMERAS_PORT to a free port."
          )
      if open_browser:
          _phone_server.open_join_page()
      return [
          f"On the phone, open {_phone_server.url} (same network as this computer) or scan the QR code. "
          f"PIN: {_phone_server.pin}. The phone will show a certificate warning the first time; that is expected.",
          Image(data=_phone_server.qr_png(), format="png"),
      ]
  ```
- **MIRROR**: TOOL_DECLARATION + ACTIONABLE_ERRORS, MODULE_GLOBAL_STATE.
- **VALIDATE**:
  ```python
  def test_add_phone_camera_tool(tmp_path, monkeypatch):
      import multicam_mcp_server as srv

      monkeypatch.setattr(srv, "_phone_server", None)
      with pytest.raises(ValueError, match="ENABLE_FRAMEGRAB_PHONE_CAMERAS"):
          srv.add_phone_camera()
      server = phone.PhoneCameraServer({}, cert_dir=tmp_path)
      server.url = "https://192.0.2.7:8443/"
      opened = []
      monkeypatch.setattr(server, "open_join_page", lambda: opened.append(1))
      monkeypatch.setattr(srv, "_phone_server", server)
      text, image = srv.add_phone_camera(open_browser=False)
      assert server.pin in text and server.url in text and image.data[:4] == b"\x89PNG" and not opened
      srv.add_phone_camera()
      assert opened == [1]
  ```
  Then the real thing: `ENABLE_FRAMEGRAB_PHONE_CAMERAS=true timeout 8 uv run multicam-mcp-server </dev/null 2>/dev/null | wc -c` → `0`.

### Task 7: Phone page
- **ACTION**: Update `multicam_mcp_phone.html`.
- **IMPLEMENT**:
  - Markup, after the name field: `<label for="pin">PIN <small>(shown on the computer; only needed the first time)</small></label><input id="pin" inputmode="numeric" pattern="[0-9]*" maxlength="6" autocomplete="one-time-code">`.
  - On load: `const m = location.hash.match(/pin=(\d{6})/); if (m) { pinInput.value = m[1]; history.replaceState(null, '', location.pathname); }`.
  - `forget = key => { try { localStorage.removeItem(key); } catch (e) {} }`.
  - `const authHeaders = () => { const t = load('token:' + name); return t ? {authorization: 'Bearer ' + t} : {}; };`
  - `register()`: headers `{'content-type': 'application/json', ...authHeaders()}`, body `{name, pin: pinInput.value.trim() || undefined}`. On success `store('token:' + name, body.token)`. On 401 `forget('token:' + name)` and return the server's `error`. Read the JSON body once for both paths.
  - `loop()`: frame POST gets `headers: authHeaders()`. New branch **before** the 404 branch: `if (res.status === 401) { forget('token:' + name); halt('Not authorised. Enter the PIN and tap Start.'); return; }`.
  - `start()` and `halt()`: disable/enable `pinInput` together with `nameInput`.
- **MIRROR**: PAGE pattern — every failure ends in `halt()`.
- **GOTCHA**: Send the stored token **and** the typed PIN in the same request: if the token key was regenerated, the stale token fails, the PIN succeeds, and the user isn't stuck. `pin: undefined` is dropped by `JSON.stringify`, which is what makes a missing PIN "missing" rather than `""` (an empty string would count as a wrong guess).
- **VALIDATE**: `uv run pytest -q` (page still served) then Task 8.

### Task 8: On-device validation (Pixel over adb + a real stdio MCP session)
- **ACTION**: Reuse the scratch driver from the last phase (add an `add` command that calls `add_phone_camera` and saves the returned PNG) and the CDP helper. `adb forward tcp:9222 localabstract:chrome_devtools_remote`; remove it afterwards.
- **VALIDATE** (record pass/fail in the report):
  - [ ] `add_phone_camera` over stdio returns text + image; a browser tab opens on the PC showing QR + PIN; stdout stays clean (the session keeps working afterwards — call `list_framegrabbers`)
  - [ ] Decode the returned PNG with `cv2.QRCodeDetector` → equals `https://<ip>:8443/#pin=<pin>`
  - [ ] `adb shell am start -a android.intent.action.VIEW -d '<join_url>'` (what a QR scanner does): PIN field is prefilled and the address bar no longer shows `#pin=` (screenshot)
  - [ ] Start → live; `grab_frame` works
  - [ ] Open the plain URL in a new tab, clear the PIN, different name, Start → "Enter the PIN shown on the computer."
  - [ ] Restart the server (new PIN): the first tab rejoins untouched; `grab_frame` works
  - [ ] `curl -k` a frame POST with no token → 401
  - [ ] Timing, lower bound: from `am start` to first frame, adb-driven taps
  - [ ] **Human stopwatch** (PRD metric, debt D3): user scans the on-screen QR with the phone's camera app → first frame. Target < 60 s including the certificate warning. Needs the user; if not done, leave D3 open and say so
- **GOTCHA**: To make the run "cold", delete `server.pem` before starting so the phone shows the certificate warning again. `token.key` deletion makes the stored token stale — a good extra check of the "token + PIN in one request" path.

### Task 9: README
- **ACTION**: Rewrite the numbered steps in "Phone cameras" to start with: ask the agent to add a phone camera (or call `add_phone_camera`); a page with a QR code opens on the computer; scan it. Keep the manual URL as the fallback and say the PIN is on that page / spoken by the agent. Replace the Security section: joining needs the PIN shown on the computer; after that the phone keeps a token for its camera name, so reloads and server restarts don't ask again; five wrong PINs from one address pause that address for a minute; deleting the data directory signs every phone out; traffic is encrypted but the certificate is self-signed, so the warning is expected. Drop "There is no authentication yet". Add `add_phone_camera` to the tools table. Remove the PIN/QR line from the roadmap. Keep "Tested with: Chrome on Android".
- **VALIDATE**: no sentence claims iOS support; every tool name matches `READY tools=` from the driver log.

### Task 10: Format, lint, build, final run
- **ACTION**: `uv run ruff format multicam_mcp_phone.py multicam_mcp_server.py test_phone_cameras.py && uv run ruff check multicam_mcp_phone.py test_phone_cameras.py && uv run pytest -q`, then build the wheel and list it (3 `multicam_mcp_*` files).
- **VALIDATE**: clean; all tests green; `git diff --stat` touches only the five files.

---

## Testing Strategy

### Unit / integration tests

| Test | Input | Expected | Edge? |
|---|---|---|---|
| joining needs the PIN | none / wrong / int / right | 401 / 401 / 401 / 201 | yes |
| frames need the token for that name | none / other name's / unknown name / right | 401 / 401 / 401 / 200 | yes |
| token rejoins after restart | token from server A on server B (same dir) | 201 without PIN, frame 200 | yes |
| lockout and recovery | 5 wrong, then right; then window = 0 | 429, then 201 | yes |
| token key file | load ×2, mode, corrupt | same, 0600, regenerated | yes |
| QR + join page | fixed URL | decodes to URL + `#pin=`; page has PIN; 0600 | |
| opener | Popen patched | stdout = DEVNULL, path = join.html | yes |
| tool | disabled / enabled / `open_browser=False` | ValueError naming the env var / [text, PNG] / opener not called | yes |
| all 21 existing tests | now authenticated | unchanged outcomes, except `usb0` frame → 401 | |

### Edge Cases Checklist
- [x] Missing / wrong-type / non-ASCII PIN
- [x] Token for a different name; token after key regeneration (device check)
- [x] Brute force: 5 per minute per address → a 6-digit PIN lasts ~139 days on average, and changes every start
- [x] Unauthenticated probe cannot tell which camera names exist
- [x] Feature disabled or port taken → tool explains
- [x] No `xdg-open` / headless machine → warning logged, tool still returns link + PIN + QR
- [ ] Someone who can see the computer's screen can join — by design; that is the trust boundary

---

## Validation Commands

```bash
uv run ruff format --check multicam_mcp_phone.py multicam_mcp_server.py test_phone_cameras.py
uv run ruff check multicam_mcp_phone.py test_phone_cameras.py
uv run pytest -q
ENABLE_FRAMEGRAB_PHONE_CAMERAS=true timeout 8 uv run multicam-mcp-server </dev/null 2>/dev/null | wc -c   # 0
uv build --no-sources --wheel -o "$SCRATCH/dist" && python3 -m zipfile -l "$SCRATCH"/dist/*.whl
```
Plus the Task 8 device checklist.

---

## Acceptance Criteria
- [ ] No endpoint except `GET /` answers without a valid PIN or token
- [ ] A phone that joined once rejoins after a server restart with no PIN (verified on the Pixel)
- [ ] `add_phone_camera` works over a real stdio session and leaves the session healthy
- [ ] The join page is a `0600` local file and is never served over the network
- [ ] All tests pass; ruff clean on new code; stdout purity 0 bytes; wheel contents unchanged in shape
- [ ] README no longer says there is no authentication, and says what the PIN and token do
- [ ] PRD: phase 4 status, Open Questions 2 (QR/PIN placement) and 4 (port conflict) marked resolved; D3 updated with whatever timing was actually measured

## Completion Checklist
- [ ] `multicam_mcp_phone.py` still imports nothing from `multicam_mcp_server`
- [ ] Constant-time comparisons for PIN and token; PIN never logged
- [ ] No `webbrowser` import anywhere
- [ ] Every new page failure path ends in `halt()`
- [ ] No hi-res, no cleanup-of-silent-cameras, no config knobs snuck in

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Codex voice mode doesn't display image tool results | M (unverified) | QR invisible in the client | Join page opens on the PC regardless; text result carries link + PIN for the agent to read out |
| Opening a browser from a stdio MCP subprocess fails (no `DISPLAY`/Wayland env inherited from the client) | M | No join page appears | Logged warning; tool result still has everything; verify in Task 8 under the real driver |
| PIN in the QR means anyone who photographs the screen can join | L | Unwanted camera on the LAN | Accepted trust boundary; PIN changes each start |
| Existing phones lose access on upgrade (no token yet) | Certain, once | One PIN entry per phone | Page says exactly what to do (401 → halt message) |
| `token.key` leaks (backup, shared home) | L | Holder can forge tokens for any name | `0600`; documented "delete the data directory to sign everyone out" |
| `request.client.host` is the same for all phones behind some Wi-Fi extenders/NAT | L | One person's typos lock others out for a minute | Short window; missing PINs don't count |

## Notes
- Input to `/prp-plan` was blank; planned the PRD's next pending phase (4) on the assumption that is what was meant.
- **Decisions made here** (veto-able): PIN carried in the QR's URL fragment; stateless HMAC tokens with a persisted key rather than a token table; join page as a private local file rather than a route; auth always on (no opt-out); tool is registered even when phone cameras are disabled, so the agent can tell the user how to enable them.
- PRD Open Question 4 (port conflict) was already settled in code during phases 2-3: fixed port, log and disable on conflict. This plan only surfaces it through the tool's error text.
- An agent can do Tasks 0-7 and 9-10 alone. Task 8 needs the Pixel on USB; its last item needs the user and a stopwatch.
