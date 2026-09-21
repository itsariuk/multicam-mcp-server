"""Lets phone browsers on the LAN act as cameras.

A phone opens the page served here, picks a name, and POSTs JPEG frames. Each named
phone is stored in the registry dict passed in (the MCP server's framegrabber cache),
so the existing MCP tools work on it without knowing it is a phone.
"""

import base64
import datetime
import hmac
import html
import ipaddress
import logging
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
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

MAX_FRAME_BYTES = 20 * 1024 * 1024
MAX_PHONE_CAMERAS = 16
STALE_AFTER_S = 10.0
PIN_MAX_FAILURES = 5  # wrong PINs allowed per client address ...
PIN_WINDOW_S = 60.0  # ... within this many seconds
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
<ol>
  <li>Put the phone on the same Wi-Fi as this computer and scan the code with its camera.
      No scanner? Open <code>{url}</code> and type the PIN.</li>
  <li>The phone will warn that the connection is not private. This is expected: this
      computer uses its own certificate. Choose <b>Advanced</b>, then <b>Proceed</b>.</li>
  <li>Allow the camera, give it a name such as <b>left bench</b>, and tap <b>Start</b>.</li>
</ol>
<p>The PIN changes each time the server starts. A phone that has joined keeps working
   without it.</p>
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
        # One tuple, swapped atomically, so put() and grab() need no lock.
        self._latest: tuple[bytes, float] | None = None

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
    # UDP connect sends no packets; it asks the kernel which interface it would use.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


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
        logger.warning(
            "Stored token key is unusable, replacing it. Phones must re-enter the PIN."
        )
    except FileNotFoundError:
        pass
    secret = secrets.token_bytes(32)
    _write_private(path, secret)
    return secret


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


class PhoneCameraServer:
    """HTTPS server that phones join. Runs uvicorn in its own thread so frame ingest
    never waits behind a (synchronous) MCP tool call.

    Joining a camera name needs the PIN, which only someone at the computer can see.
    The phone then gets a token for that name: an HMAC of the name under a key kept on
    disk, so there is nothing to store or expire and it still works after a restart.
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
        self.pin = f"{secrets.randbelow(10**6):06d}"  # new on every start, memory only
        self._secret = _load_secret(self._cert_dir)
        self._pin_failures: dict[
            str, list[float]
        ] = {}  # address -> [count, window start]
        self.app = Starlette(
            routes=[
                Route("/", self._page),
                Route("/api/cameras", self._register, methods=["POST"]),
                Route("/api/cameras/{name}/frame", self._frame, methods=["POST"]),
            ]
        )

    async def _page(self, request: Request) -> Response:
        return Response(PAGE, media_type="text/html")

    def _token(self, name: str) -> str:
        return hmac.new(self._secret, name.encode(), "sha256").hexdigest()

    def _has_token(self, request: Request, name: str) -> bool:
        sent = request.headers.get("authorization", "").removeprefix("Bearer ")
        # compare_digest rejects non-ASCII str with a TypeError.
        return sent.isascii() and hmac.compare_digest(sent, self._token(name))

    def _check_pin(self, request: Request, pin: object) -> JSONResponse | None:
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
            and hmac.compare_digest(pin, self.pin)
        ):
            return None
        if len(self._pin_failures) > 1000:
            # ponytail: crude memory bound; a LAN will never get here.
            self._pin_failures.clear()
        self._pin_failures[address] = [count + 1, since]
        logger.warning(f"Wrong phone camera PIN from {address}.")
        return _error(401, "Wrong PIN.")

    async def _register(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            name = body["name"]
        except (ValueError, KeyError, TypeError):  # bad JSON, no "name", not an object
            return _error(422, 'Expected a JSON body like {"name": "left bench"}.')
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            return _error(422, "Name must be 1-40 letters, digits, spaces, _ or -.")
        if not self._has_token(request, name):
            refused = self._check_pin(request, body.get("pin"))
            if refused:
                return refused
        joined = {"name": name, "token": self._token(name)}

        # ponytail: relies on the GIL + single event loop (no await between the check
        # and the set); add a lock if handlers ever await mid-update.
        existing = self.registry.get(name)
        if isinstance(existing, PhoneCamera):
            self._released.discard(name)
            return JSONResponse(joined)
        if existing is not None:
            return _error(409, f"'{name}' is already used by another camera.")
        phones = sum(isinstance(g, PhoneCamera) for g in list(self.registry.values()))
        if phones >= MAX_PHONE_CAMERAS:
            return _error(429, "Too many phone cameras are connected.")
        self.registry[name] = PhoneCamera(name, on_release=self._released.add)
        self._released.discard(name)
        logger.info(f"Phone camera registered: {name}")
        return JSONResponse(joined, status_code=201)

    async def _frame(self, request: Request) -> JSONResponse:
        name = request.path_params["name"]
        # First, so that a stranger cannot learn which camera names exist.
        if not self._has_token(request, name):
            return _error(401, "Not authorised. Enter the PIN and tap Start.")
        if name in self._released:
            return _error(410, "This camera was released by the agent.")
        camera = self.registry.get(name)
        if not isinstance(camera, PhoneCamera):
            return _error(404, "Unknown phone camera. Register it first.")
        body = await _read_capped(request, MAX_FRAME_BYTES)
        if body is None:
            return _error(413, "Frame is too large.")
        if body[:2] != b"\xff\xd8":
            return _error(400, "Frame must be a JPEG image.")
        camera.put(body)
        return JSONResponse({"hires_requested": False})

    def start(self, host: str = "0.0.0.0", port: int = 8443) -> str | None:
        """Start serving. Returns the URL for phones, or None if the port is taken."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # Bind here rather than in uvicorn, which calls sys.exit(1) on failure.
            sock.bind((host, port))
        except OSError as e:
            sock.close()
            logger.error(f"Phone cameras disabled: cannot listen on port {port}: {e}")
            return None
        ip = _lan_ip()
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
        # The PIN rides in the fragment, which browsers never send to a server.
        return f"{self.url}#pin={self.pin}"

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
            pin=self.pin,
            qr=base64.b64encode(self.qr_png()).decode(),
        )
        _write_private(path, page.encode())
        return path

    def open_join_page(self) -> None:
        path = self.write_join_page()
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                # Not the webbrowser module: it lets the browser inherit stdout, which
                # is the MCP stdio channel.
                subprocess.Popen(
                    ["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as e:
            logger.warning(f"Could not open the join page ({path}): {e}")
