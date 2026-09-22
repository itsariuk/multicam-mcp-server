"""Planning prototype: uvicorn-in-a-thread + own socket + generated cert. Not shipped."""

import datetime
import ipaddress
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route


def ensure_cert(cert_dir: Path, lan_ip: str) -> tuple[Path, Path]:
    cert_path, key_path = cert_dir / "cert.pem", cert_dir / "key.pem"
    now = datetime.datetime.now(datetime.timezone.utc)
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        ips = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
            x509.IPAddress
        )
        if ipaddress.ip_address(lan_ip) in ips and cert.not_valid_after_utc > now + datetime.timedelta(days=1):
            return cert_path, key_path
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "framegrab-mcp-server")])
    san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in {"127.0.0.1", lan_ip}]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=800))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_dir.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def main():
    tmp = Path(tempfile.mkdtemp())
    cert, key = ensure_cert(tmp, "192.168.2.150")
    mtime = cert.stat().st_mtime_ns
    ensure_cert(tmp, "192.168.2.150")
    assert cert.stat().st_mtime_ns == mtime, "cert should be reused when SAN matches"
    ensure_cert(tmp, "10.0.0.9")
    assert cert.stat().st_mtime_ns != mtime, "cert should regenerate when LAN IP changes"

    app = Starlette(routes=[Route("/", lambda r: PlainTextResponse("hi"))])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, ssl_certfile=str(cert), ssl_keyfile=str(key), log_config=None, access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"

    ctx = ssl.create_default_context(cafile=str(cert))  # verifying client: proves SAN is right
    body = urllib.request.urlopen(f"https://127.0.0.1:{port}/", context=ctx, timeout=5).read()
    assert body == b"hi", body

    # second bind on same port must fail cleanly in OUR code, not via uvicorn's sys.exit(1)
    s2 = socket.socket()
    try:
        s2.bind(("127.0.0.1", port))
        raise AssertionError("expected bind failure")
    except OSError as e:
        print("port conflict detected as OSError:", e.errno, file=sys.stderr)

    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive(), "uvicorn thread did not stop"
    print("PROTO OK", file=sys.stderr)


if __name__ == "__main__":
    main()
