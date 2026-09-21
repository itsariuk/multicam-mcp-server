"""Lets phone browsers on the LAN act as cameras.

A phone opens the page served here, picks a name, and POSTs JPEG frames. Each named
phone is stored in the registry dict passed in (the MCP server's framegrabber cache),
so the existing MCP tools work on it without knowing it is a phone.
"""

import datetime
import ipaddress
import logging
import os
import re
import socket
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
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")
PAGE = Path(__file__).with_suffix(".html").read_bytes()


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
    cert_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = pem_path.with_suffix(".tmp")
    tmp_path.touch(mode=0o600)
    tmp_path.chmod(0o600)  # touch() does not change the mode of a leftover file
    tmp_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + key_pem)
    os.replace(tmp_path, pem_path)
    return pem_path


class PhoneCameraServer:
    """HTTPS server that phones join. Runs uvicorn in its own thread so frame ingest
    never waits behind a (synchronous) MCP tool call."""

    def __init__(self, registry: dict, cert_dir: Path | None = None):
        self.registry = registry
        self._released: set[str] = set()
        self._cert_dir = cert_dir or Path(
            platformdirs.user_data_dir("multicam-mcp-server")
        )
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.app = Starlette(
            routes=[
                Route("/", self._page),
                Route("/api/cameras", self._register, methods=["POST"]),
                Route("/api/cameras/{name}/frame", self._frame, methods=["POST"]),
            ]
        )

    async def _page(self, request: Request) -> Response:
        return Response(PAGE, media_type="text/html")

    async def _register(self, request: Request) -> JSONResponse:
        try:
            name = (await request.json())["name"]
        except (ValueError, KeyError, TypeError):  # bad JSON, no "name", not an object
            return _error(422, 'Expected a JSON body like {"name": "left bench"}.')
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            return _error(422, "Name must be 1-40 letters, digits, spaces, _ or -.")

        # ponytail: relies on the GIL + single event loop (no await between the check
        # and the set); add a lock if handlers ever await mid-update.
        existing = self.registry.get(name)
        if isinstance(existing, PhoneCamera):
            self._released.discard(name)
            return JSONResponse({"name": name})
        if existing is not None:
            return _error(409, f"'{name}' is already used by another camera.")
        phones = sum(isinstance(g, PhoneCamera) for g in list(self.registry.values()))
        if phones >= MAX_PHONE_CAMERAS:
            return _error(429, "Too many phone cameras are connected.")
        self.registry[name] = PhoneCamera(name, on_release=self._released.add)
        self._released.discard(name)
        logger.info(f"Phone camera registered: {name}")
        return JSONResponse({"name": name}, status_code=201)

    async def _frame(self, request: Request) -> JSONResponse:
        name = request.path_params["name"]
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
        url = f"https://{ip}:{sock.getsockname()[1]}/"
        logger.info(f"Phone cameras: open {url} on a phone on the same network.")
        return url

    def stop(self) -> None:
        if self._server and self._thread:
            self._server.should_exit = True
            self._thread.join(timeout=5)
