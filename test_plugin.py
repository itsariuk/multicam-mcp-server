"""Exercise the public MCP protocol across two independent plugin processes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import multicam_plugin as plugin


def test_state_rejects_invalid_endpoint(tmp_path):
    path = tmp_path / "state.json"
    for text in ("{}", "null", "[]", '{"port": true, "token": "x"}', "not json"):
        path.write_text(text)
        assert plugin.read_state(path) is None


def test_service_lock(tmp_path):
    with plugin.service_lock(tmp_path / "lock") as first:
        assert first
        with plugin.service_lock(tmp_path / "lock") as second:
            assert not second
    with plugin.service_lock(tmp_path / "lock") as again:
        assert again


def test_pin_preserves_leading_zero(monkeypatch):
    from types import SimpleNamespace
    import multicam_mcp_server as server

    monkeypatch.setattr(
        server,
        "_phone_server",
        SimpleNamespace(
            url="https://192.0.2.1:8443/",
            join_url="https://192.0.2.1:8443/",
            pairing_info=lambda: {"pin": "012345", "pin_spoken": "0 1 2 3 4 5"},
        ),
    )
    info = server.get_phone_connection_info()
    assert info["pin"] == "012345"
    assert info["pin_spoken"] == "0 1 2 3 4 5"


def test_two_clients_share_service(tmp_path, monkeypatch):
    # Isolate pairing keys/state and start a service whose lifecycle the test owns.
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        phone_port = sock.getsockname()[1]
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("FRAMEGRAB_PHONE_CAMERAS_PORT", str(phone_port))
    command = os.environ.get("MULTICAM_TEST_BINARY")
    args = [] if command else [str(Path(plugin.__file__).resolve())]
    command = command or sys.executable
    env = dict(os.environ)
    log = (tmp_path / "test-service.log").open("wb")
    service = subprocess.Popen(
        [command, *args, "--service"], env=env, stdout=log, stderr=log
    )

    async def run():
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if await plugin.healthy(
                plugin.read_state(plugin.data_dir() / "service.json")
            ):
                break
            assert service.poll() is None, (tmp_path / "test-service.log").read_text()
            await anyio.sleep(0.2)
        else:
            raise AssertionError("Service did not start")
        state = plugin.read_state(plugin.data_dir() / "service.json")
        async with plugin.httpx2.AsyncClient(
            trust_env=False, verify=plugin.tls_context(state)
        ) as client:
            assert (
                await client.get(plugin.service_url(state) + "/health")
            ).status_code == 401
        params = StdioServerParameters(command=command, args=args, env=env)
        async with stdio_client(params) as (read1, write1):
            async with ClientSession(read1, write1) as one:
                init = await one.initialize()
                assert "snapshot" in init.instructions
                first = await one.call_tool("get_phone_connection_info", {})
                assert not first.is_error
                async with stdio_client(params) as (read2, write2):
                    async with ClientSession(read2, write2) as two:
                        await two.initialize()
                        second = await two.call_tool("get_phone_connection_info", {})
                        assert first.content == second.content
                        qr = await two.call_tool(
                            "add_phone_camera", {"open_browser": False}
                        )
                        assert not qr.is_error
                        assert any(block.type == "image" for block in qr.content)
                        first = await two.call_tool("get_phone_connection_info", {})
                # Closing a conversation must not terminate the other one's service.
                again = await one.call_tool("get_phone_connection_info", {})
                assert (
                    json.loads(first.content[0].text)["pin"]
                    == json.loads(again.content[0].text)["pin"]
                )

    try:
        anyio.run(run)
    finally:
        service.terminate()
        try:
            service.wait(timeout=10)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait()
        log.close()


def test_plugin_cold_start(tmp_path, monkeypatch):
    """A user needs only the plugin executable; no prestarted server."""
    import signal
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("FRAMEGRAB_PHONE_CAMERAS_PORT", str(port))
    command = os.environ.get("MULTICAM_TEST_BINARY")
    args = [] if command else [str(Path(plugin.__file__).resolve())]
    params = StdioServerParameters(
        command=command or sys.executable, args=args, env=dict(os.environ)
    )
    state_path = plugin.data_dir() / "service.json"

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                info = await session.call_tool("get_phone_connection_info", {})
                assert not info.is_error
                assert state_path.exists()

    try:
        anyio.run(run)
    finally:
        state = plugin.read_state(state_path)
        if state:
            os.kill(state["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 10
            while state_path.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not state_path.exists(), "Service did not shut down cleanly"


def test_stale_port_impostor_cannot_receive_credentials(tmp_path):
    """An impostor with the expected health JSON cannot pass server authentication."""
    import contextlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import ssl
    import threading

    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    import pytest
    from multicam_mcp_phone import _ensure_cert

    real_pem = _ensure_cert(tmp_path / "real", "127.0.0.1")
    impostor_pem = _ensure_cert(tmp_path / "impostor", "127.0.0.1")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            body = b'{"service":"multicam-plugin-v1"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    @contextlib.contextmanager
    def endpoint(pem, port=0):
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        if pem:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(pem)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    async def run():
        with endpoint(real_pem) as port:
            state = {
                "protocol": 2,
                "port": port,
                "token": "a" * 64,
                "certificate": x509.load_pem_x509_certificate(real_pem.read_bytes())
                .public_bytes(Encoding.PEM)
                .decode("ascii"),
            }
            path = tmp_path / "state.json"
            path.write_text(json.dumps(state))
            assert plugin.read_state(path) == state
            assert await plugin.healthy(state)
        assert requests == [("/health", "Bearer " + state["token"])]
        requests.clear()
        # Take over the exact port after the real service disappears. Both a TLS
        # impostor and the old plaintext health endpoint must fail closed.
        for pem in (impostor_pem, None):
            with endpoint(pem, port):
                assert not await plugin.healthy(state)
                async with plugin.client_for(state) as client:
                    with pytest.raises(plugin.httpx2.HTTPError):
                        await client.get(plugin.service_url(state) + "/mcp")
            assert requests == []
        # An old state without authenticated server identity cannot downgrade TLS.
        del state["certificate"]
        path.write_text(json.dumps(state))
        assert plugin.read_state(path) is None

    anyio.run(run)
