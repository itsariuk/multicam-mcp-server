"""Lets phone browsers on the LAN act as cameras.

A phone opens the page, pairs during a timed window, and polls for capture requests.
It uploads one JPEG only in response to an authenticated single-use request. Each named
phone is stored in the registry dict passed in (the MCP server's framegrabber cache),
so the existing MCP tools work on it without knowing it is a phone.
"""

import base64
import datetime
import hmac
import hashlib
import html
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import platformdirs
import psutil
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

MAX_FRAME_BYTES = 20 * 1024 * 1024
MAX_PHONE_CAMERAS = 16
STALE_AFTER_S = 10.0
HIRES_TIMEOUT_S = (
    8.0  # includes polling, native capture, and upload; no preview fallback
)
PIN_MAX_FAILURES = 5  # wrong PINs allowed per client address ...
PIN_WINDOW_S = 60.0  # ... within this many seconds
JOIN_WINDOW_S = 60.0
PIN_GLOBAL_MAX_FAILURES = 10
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")
PAGE = Path(__file__).with_suffix(".html").read_bytes()

# Shown on the computer by add_phone_camera. Written to a private local file and never
# served over the network, because it contains the PIN. Braces are doubled for str.format.
JOIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Add a phone camera</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 18px/1.5 system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 20px; }}
  img {{ display: block; width: 328px; height: 328px; background: #fff; border-radius: 8px; }}
  .pin {{ font: 700 48px/1.2 ui-monospace, monospace; letter-spacing: 0.2em; }}
  code {{ font-size: 0.9em; }}
</style>
</head>
<body>
<h1>Add a phone camera</h1>
<img alt="QR code that opens the phone camera page" src="data:image/png;base64,{qr}">
<p>PIN</p>
<p class="pin">{pin}</p>
<p><b>Joining expires 60 seconds after Codex opens it.</b> If it expires, ask Codex to open joining again and use the new PIN.</p>
<p>This computer's camera page is reachable on the local network. Opening the page does not authorize a phone: registration requires the PIN during the pairing window, and uploads require the camera's authorized token.</p>
<ol>
  <li>Put the phone on the same Wi-Fi as this computer and scan the code with its camera.
      No scanner? Open <a href="{join_url}">{url}</a> on your phone.</li>
  <li>The phone will warn that the connection is not private. This is expected: this
      computer uses its own certificate. Choose <b>Advanced</b>, then <b>Proceed</b>.</li>
  <li>Enter the PIN shown above. The QR code contains only the address, not the PIN.</li>
  <li>Allow the camera, give it a name such as <b>left bench</b>, and tap <b>Start</b>.</li>
</ol>
<p>Each pairing window has a new PIN. Already connected cameras keep sending after joining closes.</p>
</body>
</html>
"""


class PhoneCamera:
    """A phone browser posting JPEG frames. Duck-types the parts of
    framegrab.FrameGrabber that the MCP tools use: grab, config, apply_options, release.
    """

    def __init__(self, name: str, on_release):
        self.name = name
        self._on_release = on_release
        self._capture_lock = threading.Lock()
        self._photo_lock = threading.Lock()
        self._photo_ready = threading.Event()
        self._last_seen: float | None = None
        self._request_id: str | None = None
        self._deadline = 0.0
        self._claimed = False
        self._photo: bytes | None = None
        self._is_released = False

    def poll_request(self) -> dict:
        """A heartbeat carries metadata only; it never uploads or captures an image."""
        with self._photo_lock:
            self._last_seen = time.monotonic()
            active = (
                self._request_id is not None
                and self._last_seen < self._deadline
                and not self._claimed
            )
            return {"request_id": self._request_id if active else None}

    def claim_upload(self, request_id: str) -> bool:
        """Reserve a single response before accepting its body (including concurrent POSTs)."""
        with self._photo_lock:
            if (
                self._is_released
                or self._claimed
                or not self._request_id
                or time.monotonic() >= self._deadline
                or not request_id.isascii()
                or not hmac.compare_digest(request_id, self._request_id)
            ):
                return False
            self._claimed = True
            return True

    def accept_photo(self, request_id: str, jpeg: bytes) -> bool:
        with self._photo_lock:
            if (
                self._is_released
                or not self._claimed
                or request_id != self._request_id
                or time.monotonic() >= self._deadline
            ):
                return False
            self._photo = jpeg
            self._request_id = None
            self._photo_ready.set()
            return True

    def grab(self) -> np.ndarray:
        # Serialize independent MCP callers: each capture gets its own nonce and image.
        with self._capture_lock:
            with self._photo_lock:
                if self._is_released:
                    raise RuntimeError(f"Phone camera '{self.name}' was released.")
                if (
                    self._last_seen is None
                    or time.monotonic() - self._last_seen > STALE_AFTER_S
                ):
                    raise RuntimeError(
                        f"Phone camera '{self.name}' is not ready. Is the page still open on the phone?"
                    )
                self._photo = None
                self._photo_ready.clear()
                self._claimed = False
                self._request_id = secrets.token_urlsafe(24)
                self._deadline = time.monotonic() + HIRES_TIMEOUT_S
            arrived = self._photo_ready.wait(HIRES_TIMEOUT_S)
            with self._photo_lock:
                photo = self._photo
                self._photo = None
                self._request_id = None
                self._deadline = 0.0
            if not arrived or photo is None:
                raise RuntimeError(
                    f"Phone camera '{self.name}' did not answer the capture request. Keep its page visible and try again."
                )
            return _decode(self.name, photo)

    @property
    def config(self) -> dict:
        return {"name": self.name, "input_type": "phone_browser"}

    def apply_options(self, options: dict) -> None:
        raise ValueError("Phone cameras have no configurable options.")

    def release(self) -> None:
        with self._photo_lock:
            self._is_released = True
            self._request_id = None
            self._photo = None
            self._photo_ready.set()
        self._on_release(self.name)


def _decode(name: str, jpeg: bytes) -> np.ndarray:
    frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"Phone camera '{name}' sent an undecodable image.")
    return frame


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """Read the request body, or return None if it is larger than limit."""
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


def _lan_ip() -> str:
    """Select an active LAN interface, even when outbound route probes are blocked."""
    candidates = []
    try:
        stats = psutil.net_if_stats()
        interfaces = psutil.net_if_addrs()
    except OSError as exc:
        raise RuntimeError(
            "This desktop environment blocked network-interface detection. Ask the user "
            "for this computer's Wi-Fi IPv4 address from Network Settings."
        ) from exc
    for name, addresses in interfaces.items():
        status = stats.get(name)
        if status is None or not status.isup:
            continue
        label = name.lower()
        if label.startswith(
            (
                "lo",
                "docker",
                "veth",
                "br-",
                "virbr",
                "tun",
                "tap",
                "utun",
                "wg",
                "tailscale",
            )
        ):
            continue
        if any(word in label for word in ("virtual", "vmware", "vpn", "vethernet")):
            continue
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(address.address)
            if not (
                ip.is_loopback
                or ip.is_unspecified
                or ip.is_link_local
                or ip.is_multicast
            ):
                if str(ip) not in candidates:
                    candidates.append(str(ip))
    # Prefer the routed interface when it is among the usable physical interfaces.
    # UDP connect sends no packets, but a sandbox can still deny the operation.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            routed = s.getsockname()[0]
            if routed in candidates:
                return routed
    except OSError:
        pass
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError(
            "No usable local-network IPv4 address was found. Connect this computer to Wi-Fi "
            "or Ethernet and allow local-network access, then reconnect Multicam. "
            "A phone cannot connect to a 127.0.0.1 address on this computer."
        )
    raise RuntimeError(
        "Several local-network addresses were found, but the active route could not be determined: "
        + ", ".join(candidates)
        + ". Connect through the same network as your phone and reconnect Multicam."
    )


def _phone_host_ip(value: str) -> str:
    """Validate a user-supplied address without pretending to verify reachability."""
    try:
        ip = ipaddress.IPv4Address(value.strip())
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            "Enter the computer's Wi-Fi IPv4 address, for example 192.168.1.20, without https or a port."
        ) from exc
    if (
        ip.is_loopback
        or ip.is_unspecified
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
    ):
        raise ValueError(
            "Use the computer's Wi-Fi or Ethernet IPv4 address, not localhost or a loopback address."
        )
    return str(ip)


def _write_private(path: Path, data: bytes) -> None:
    """Write a file only this user can read, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.touch(mode=0o600)
    tmp_path.chmod(0o600)  # touch() does not change the mode of a leftover file
    tmp_path.write_bytes(data)
    os.replace(tmp_path, path)


def _ensure_cert(cert_dir: Path, lan_ip: str) -> Path:
    """Return the path of a PEM file holding a self-signed cert valid for lan_ip and its
    key, reusing the stored one when possible so phones keep their accepted-certificate
    exception. Cert and key share one file, replaced atomically, so they cannot mismatch.
    """
    pem_path = cert_dir / "server.pem"
    now = datetime.datetime.now(datetime.timezone.utc)
    if pem_path.exists():
        try:
            cert = x509.load_pem_x509_certificate(pem_path.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            ips = san.value.get_values_for_type(x509.IPAddress)
            expires_soon = cert.not_valid_after_utc < now + datetime.timedelta(days=1)
            if ipaddress.ip_address(lan_ip) in ips and not expires_soon:
                return pem_path
        except (ValueError, x509.ExtensionNotFound) as e:
            logger.warning(f"Stored certificate is unusable, replacing it: {e}")
    logger.info(f"Generating self-signed certificate for {lan_ip} in {cert_dir}.")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "multicam-mcp-server")])
    ips = sorted({"127.0.0.1", lan_ip})
    alt_names = [x509.DNSName("localhost")] + [
        x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips
    ]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=800))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_private(pem_path, cert.public_bytes(serialization.Encoding.PEM) + key_pem)
    return pem_path


class PhoneSecurity:
    """Restrict browser access and keep camera credentials/responses out of caches."""

    def __init__(self, app, server):
        self.app, self.server = app, server
        script = re.search(rb"<script>(.*?)</script>", PAGE, re.S).group(1)
        style = re.search(rb"<style>(.*?)</style>", PAGE, re.S).group(1)

        def digest(value):
            return base64.b64encode(hashlib.sha256(value).digest()).decode()

        self.headers = [
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"permissions-policy", b"camera=(self), microphone=(), geolocation=()"),
            (
                b"content-security-policy",
                (
                    "default-src 'none'; script-src 'sha256-"
                    + digest(script)
                    + "'; style-src 'sha256-"
                    + digest(style)
                    + "'; connect-src 'self'; media-src 'self' blob:; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                ).encode(),
            ),
        ]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {k for k, _ in self.headers}
                ]
                message = {**message, "headers": headers + self.headers}
            await send(message)

        hosts = self.server._allowed_hosts
        if hosts is not None and request.url.hostname not in hosts:
            return await _error(403, "Unrecognized host.")(scope, receive, secure_send)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return await _error(403, "Cross-origin camera access is not allowed.")(
                scope, receive, secure_send
            )
        await self.app(scope, receive, secure_send)


class PhoneCameraServer:
    """HTTPS server that phones join. Runs uvicorn in its own thread so frame ingest
    never waits behind a (synchronous) MCP tool call.

    Joining a camera name needs the PIN, which only someone at the computer can see.
    Each registration gets a random in-memory token, revoked on disconnect or restart.
    """

    def __init__(self, registry: dict, cert_dir: Path | None = None):
        self.registry = registry
        self._released: set[str] = set()
        self._cert_dir = cert_dir or Path(
            platformdirs.user_data_dir("multicam-mcp-server")
        )
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url: str | None = None  # set by start()
        # Replace atomically so a request sees one PIN and its matching deadline.
        self._pairing = (f"{secrets.randbelow(10**6):06d}", 0.0)
        self._tokens: dict[str, str] = {}
        self._pairing_lock = threading.RLock()
        self._window_failures = 0
        self._allowed_hosts: set[str] | None = None
        self._pin_failures: dict[
            str, list[float]
        ] = {}  # address -> [count, window start]
        self.app = Starlette(
            routes=[
                Route("/", self._page),
                Route("/api/cameras", self._register, methods=["POST"]),
                Route("/api/cameras/{name}/request", self._request, methods=["GET"]),
                Route("/api/cameras/{name}/frame", self._frame, methods=["POST"]),
                Route("/api/cameras/{name}", self._disconnect, methods=["DELETE"]),
            ],
            middleware=[Middleware(PhoneSecurity, server=self)],
        )

    async def _page(self, request: Request) -> Response:
        return Response(PAGE, media_type="text/html")

    @property
    def pin(self) -> str:
        return self._pairing[0]

    def open_join_window(self) -> None:
        # Exclude the previous PIN so reopening invalidates the old one.
        with self._pairing_lock:
            pin = (int(self.pin) + 1 + secrets.randbelow(999999)) % 1000000
            self._pairing = (f"{pin:06d}", time.monotonic() + JOIN_WINDOW_S)
            self._window_failures = 0

    def pairing_info(self) -> dict:
        pin, deadline = self._pairing
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        return {
            "joining_open": remaining > 0,
            "joining_seconds_remaining": remaining,
            "pin": pin if remaining else "",
            "pin_spoken": " ".join(pin) if remaining else "",
        }

    def _token(self, name: str) -> str:
        return self._tokens[name]

    def _has_token(self, request: Request, name: str) -> bool:
        sent = request.headers.get("authorization", "").removeprefix("Bearer ")
        # compare_digest rejects non-ASCII str with a TypeError.
        expected = self._tokens.get(name)
        return bool(expected) and sent.isascii() and hmac.compare_digest(sent, expected)

    def _check_pin(
        self, request: Request, pin: object, expected_pin: str
    ) -> JSONResponse | None:
        """None if the PIN is right, else the error response."""
        if pin is None:  # not a guess: e.g. a phone reloading with an outdated token
            return _error(401, "Enter the PIN shown on the computer.")
        address = request.client.host if request.client else "unknown"
        now = time.monotonic()
        count, since = self._pin_failures.get(address, (0, now))
        if now - since >= PIN_WINDOW_S:
            count, since = 0, now
        if count >= PIN_MAX_FAILURES:
            return _error(429, "Too many wrong PINs. Wait a minute and try again.")
        if (
            isinstance(pin, str)
            and pin.isascii()
            and hmac.compare_digest(pin, expected_pin)
        ):
            return None
        if len(self._pin_failures) > 1000:
            # ponytail: crude memory bound; a LAN will never get here.
            self._pin_failures.clear()
        self._pin_failures[address] = [count + 1, since]
        self._window_failures += 1
        if self._window_failures >= PIN_GLOBAL_MAX_FAILURES:
            self._pairing = (self.pin, 0.0)
            return _error(
                429,
                "Too many incorrect PIN attempts. Joining is closed; ask Codex to reopen it.",
            )
        logger.warning(f"Wrong phone camera PIN from {address}.")
        return _error(401, "Wrong PIN.")

    async def _register(self, request: Request) -> JSONResponse:
        if time.monotonic() >= self._pairing[1]:
            return _error(
                403,
                "Joining is closed. Ask Codex to open phone joining again for 60 seconds, then tap Start.",
            )
        try:
            raw = await _read_capped(request, 2048)
            if raw is None:
                return _error(413, "Registration request is too large.")
            body = json.loads(raw)
            name = body["name"]
        except (ValueError, KeyError, TypeError):  # bad JSON, no "name", not an object
            return _error(422, 'Expected a JSON body like {"name": "left bench"}.')
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            return _error(422, "Name must be 1-40 letters, digits, spaces, _ or -.")
        return self._register_camera(request, name, body.get("pin"))

    def _register_camera(
        self, request: Request, name: str, pin: object
    ) -> JSONResponse:
        with self._pairing_lock:
            expected_pin, deadline = self._pairing
            if time.monotonic() >= deadline:
                return _error(
                    403,
                    "Joining is closed. Ask Codex to open joining again for 60 seconds, then tap Start.",
                )
            authorized = self._has_token(request, name)
            if not authorized:
                refused = self._check_pin(request, pin, expected_pin)
                if refused is not None:
                    return refused
            existing = self.registry.get(name)
            if existing is not None:
                if isinstance(existing, PhoneCamera) and authorized:
                    return JSONResponse({"name": name, "token": self._token(name)})
                return _error(
                    409, "This camera name is already in use. Choose a different name."
                )
            phones = sum(
                isinstance(g, PhoneCamera) for g in list(self.registry.values())
            )
            if phones >= MAX_PHONE_CAMERAS:
                return _error(429, "Too many phone cameras are connected.")
            self._tokens[name] = secrets.token_urlsafe(32)
            self.registry[name] = PhoneCamera(name, on_release=self._revoke)
            self._released.discard(name)
            logger.info(f"Phone camera registered: {name}")
            return JSONResponse(
                {"name": name, "token": self._token(name)}, status_code=201
            )

    def _revoke(self, name: str) -> None:
        with self._pairing_lock:
            self._released.add(name)
            self._tokens.pop(name, None)

    async def _disconnect(self, request: Request) -> JSONResponse:
        camera, error = self._authorized_camera(request)
        if error is not None:
            return error
        camera.release()
        self.registry.pop(request.path_params["name"], None)
        return JSONResponse({"disconnected": True})

    def _authorized_camera(self, request: Request):
        name = request.path_params["name"]
        if not self._has_token(request, name):
            return None, _error(
                401, "Not authorised. Enter the PIN during an open pairing window."
            )
        if name in self._released:
            return None, _error(
                410,
                "This camera was released. Ask Codex to open joining before rejoining.",
            )
        camera = self.registry.get(name)
        if not isinstance(camera, PhoneCamera):
            return None, _error(
                404,
                "Unknown phone camera. Ask Codex to open joining and register first.",
            )
        return camera, None

    async def _request(self, request: Request) -> JSONResponse:
        camera, error = self._authorized_camera(request)
        if error is not None:
            return error
        return JSONResponse(
            camera.poll_request(), headers={"Cache-Control": "no-store"}
        )

    async def _frame(self, request: Request) -> JSONResponse:
        camera, error = self._authorized_camera(request)
        if error is not None:
            return error
        request_id = request.headers.get("x-capture-request", "")
        if not camera.claim_upload(request_id):
            return _error(
                409,
                "No matching unconsumed capture request. Unsolicited uploads are not accepted.",
            )
        body = await _read_capped(request, MAX_FRAME_BYTES)
        if body is None:
            return _error(413, "Frame is too large.")
        if body[:2] != b"\xff\xd8":
            return _error(400, "Frame must be a JPEG image.")
        if not camera.accept_photo(request_id, body):
            return _error(409, "Capture request expired or was cancelled.")
        return JSONResponse({"accepted": True})

    def start(
        self, host: str | None = None, port: int = 8443, computer_ip: str | None = None
    ) -> str | None:
        """Start serving. Returns the URL for phones, or None if the port is taken."""
        ip = _phone_host_ip(computer_ip) if computer_ip is not None else _lan_ip()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # Bind here rather than in uvicorn, which calls sys.exit(1) on failure.
            # Expose only the selected LAN interface, not every interface or VPN.
            sock.bind((host or ip, port))
        except OSError as e:
            sock.close()
            logger.error(f"Phone cameras disabled: cannot listen on port {port}: {e}")
            return None
        self._allowed_hosts = {ip, "127.0.0.1", "localhost"}
        config = uvicorn.Config(
            self.app,
            ssl_certfile=str(_ensure_cert(self._cert_dir, ip)),  # holds the key too
            # uvicorn's default config logs to stdout, which is the MCP stdio channel.
            log_config=None,
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 5
        while (
            not self._server.started
            and self._thread.is_alive()
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        if not self._server.started:
            # e.g. the stored certificate's key is damaged; uvicorn logged the reason.
            logger.error(
                f"Phone cameras disabled: HTTPS server failed to start on {port}."
            )
            self.stop()
            sock.close()
            return None
        self.url = f"https://{ip}:{sock.getsockname()[1]}/"
        logger.info(f"Phone cameras: open {self.url} on a phone on the same network.")
        return self.url

    def stop(self) -> None:
        self.url = None
        if self._server and self._thread:
            self._server.should_exit = True
            self._thread.join(timeout=5)

    @property
    def join_url(self) -> str:
        # Scanning the QR or knowing the LAN address must not grant pairing access.
        if self.url is None:
            raise RuntimeError("The phone camera listener is not running.")
        return self.url

    def qr_png(self) -> bytes:
        modules = cv2.QRCodeEncoder.create().encode(self.join_url)
        image = cv2.resize(modules, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
        # Scanners need the white quiet zone; the encoder does not add one.
        image = cv2.copyMakeBorder(
            image, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255
        )
        ok, png = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError("Failed to encode the QR code.")
        return png.tobytes()

    def write_join_page(self) -> Path:
        """A local file, not a route: it shows the PIN, so the network must not be able
        to fetch it."""
        path = self._cert_dir / "join.html"
        page = JOIN_PAGE.format(
            url=html.escape(self.url or ""),
            join_url=html.escape(self.join_url),
            pin=self.pin,
            qr=base64.b64encode(self.qr_png()).decode(),
        )
        _write_private(path, page.encode())
        return path

    def open_join_page(self) -> bool:
        path = self.write_join_page()
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                # Not the webbrowser module: it lets the browser inherit stdout, which
                # is the MCP stdio channel.
                env = os.environ.copy()
                # Frozen Linux runtimes adjust this for bundled libraries. System
                # browsers need the original library search path instead.
                if getattr(sys, "frozen", False) and sys.platform == "linux":
                    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
                    if original is None:
                        env.pop("LD_LIBRARY_PATH", None)
                    else:
                        env["LD_LIBRARY_PATH"] = original
                subprocess.Popen(
                    ["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                )
            return True
        except OSError as e:
            logger.warning(f"Could not open the join page ({path}): {e}")
            return False
