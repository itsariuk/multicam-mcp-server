from contextlib import contextmanager
import ssl
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

import multicam_mcp_phone as phone


def jpeg(w=64, h=48) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 127, np.uint8))
    assert ok
    return buf.tobytes()


@pytest.fixture
def ctx(tmp_path):
    registry = {}
    server = phone.PhoneCameraServer(registry, cert_dir=tmp_path)
    server.open_join_window()
    return registry, TestClient(server.app), server


def join(client, server, name) -> dict:
    """Register with the PIN; return the headers that authorise frame posts."""
    r = client.post("/api/cameras", json={"name": name, "pin": server.pin})
    assert r.status_code in (200, 201), r.text
    return {"authorization": "Bearer " + r.json()["token"]}


def wrong_pin(server) -> str:
    return "000000" if server.pin != "000000" else "111111"


def test_page_is_served(ctx):
    r = ctx[1].get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("name", ["", " x", "a" * 41, "../etc", "na/me", 5, None])
def test_bad_names_rejected(ctx, name):
    assert ctx[1].post("/api/cameras", json={"name": name}).status_code == 422


def test_bad_json_rejected(ctx):
    assert ctx[1].post("/api/cameras", content=b"nope").status_code == 422
    assert ctx[1].post("/api/cameras", json=["name"]).status_code == 422


def test_name_owned_by_other_grabber_conflicts(ctx):
    registry, client, server = ctx
    registry["usb0"] = object()
    body = {"name": "usb0", "pin": server.pin}
    assert client.post("/api/cameras", json=body).status_code == 409
    # No token was ever issued for it, so a frame post is refused before any lookup.
    assert client.post("/api/cameras/usb0/frame", content=jpeg()).status_code == 401


def test_camera_cap(ctx, monkeypatch):
    _, client, server = ctx
    monkeypatch.setattr(phone, "MAX_PHONE_CAMERAS", 1)
    assert (
        client.post("/api/cameras", json={"name": "a", "pin": server.pin}).status_code
        == 201
    )
    assert (
        client.post("/api/cameras", json={"name": "b", "pin": server.pin}).status_code
        == 429
    )


def test_joining_needs_the_pin(ctx):
    _, client, server = ctx

    def post(body):
        return client.post("/api/cameras", json=body).status_code

    assert post({"name": "cam"}) == 401
    assert post({"name": "cam", "pin": wrong_pin(server)}) == 401
    assert post({"name": "cam", "pin": int(server.pin)}) == 401  # not a str
    assert post({"name": "cam", "pin": "12345١"}) == 401  # non-ASCII digits
    assert post({"name": "cam", "pin": server.pin}) == 201


def test_wrong_pins_lock_out_then_recover(ctx, monkeypatch):
    _, client, server = ctx
    for _ in range(phone.PIN_MAX_FAILURES):
        assert (
            client.post(
                "/api/cameras", json={"name": "cam", "pin": wrong_pin(server)}
            ).status_code
            == 401
        )
    assert (
        client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code
        == 429
    )
    monkeypatch.setattr(phone, "PIN_WINDOW_S", 0)
    assert (
        client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code
        == 201
    )


def test_missing_pin_is_not_counted_as_a_wrong_guess(ctx):
    _, client, server = ctx
    for _ in range(phone.PIN_MAX_FAILURES + 1):
        assert client.post("/api/cameras", json={"name": "cam"}).status_code == 401
    assert (
        client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code
        == 201
    )


def test_qr_contains_only_url_and_pin_stays_on_private_join_page(ctx):
    _, _, server = ctx
    server.url = "https://192.0.2.7:8443/"
    png = cv2.imdecode(np.frombuffer(server.qr_png(), np.uint8), cv2.IMREAD_GRAYSCALE)
    decoded = cv2.QRCodeDetector().detectAndDecode(png)[0]
    assert decoded == "https://192.0.2.7:8443/"
    assert "#" not in decoded and "?" not in decoded
    assert ctx[1].post("/api/cameras", json={"name": "visitor"}).status_code == 401
    assert server.pin not in ctx[1].get("/").text
    page = server.write_join_page()
    assert server.pin in page.read_text() and "192.0.2.7" in page.read_text()
    assert page.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses os.startfile")
def test_join_page_opener_never_inherits_stdout(ctx, monkeypatch):
    _, _, server = ctx
    server.url = "https://192.0.2.7:8443/"
    calls = []
    monkeypatch.setattr(
        phone.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw))
    )
    server.open_join_page()
    ((cmd, kw),) = calls
    assert cmd[-1].endswith("join.html")
    assert kw["stdout"] == kw["stderr"] == kw["stdin"] == phone.subprocess.DEVNULL


def test_tls_server_starts_serves_and_stops(tmp_path):
    server = phone.PhoneCameraServer({}, cert_dir=tmp_path)
    assert server.url is None
    url = server.start(host="127.0.0.1", port=0)
    try:
        assert url.startswith("https://") and server.url == url
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        # A verifying client: proves the generated cert's SAN is right.
        tls = ssl.create_default_context(cafile=str(tmp_path / "server.pem"))
        body = urllib.request.urlopen(
            f"https://127.0.0.1:{port}/", context=tls, timeout=5
        ).read()
        assert b"<!doctype html>" in body.lower()
        # Port already taken -> feature disabled, not a crash.
        other = phone.PhoneCameraServer({}, cert_dir=tmp_path)
        assert other.start(host="127.0.0.1", port=port) is None and other.url is None
    finally:
        server.stop()


def test_unusable_stored_cert_is_replaced(tmp_path):
    (tmp_path / "server.pem").write_bytes(b"-----BEGIN CERTIFICATE-----\ntruncat")
    pem = phone._ensure_cert(tmp_path, "192.0.2.7")
    tls = ssl.create_default_context(cafile=str(pem))  # parses, so it was regenerated
    assert tls.cert_store_stats()["x509"] == 1
    assert pem.stat().st_mode & 0o777 == 0o600
    before = pem.read_bytes()
    assert phone._ensure_cert(tmp_path, "192.0.2.7").read_bytes() == before  # reused
    assert phone._ensure_cert(tmp_path, "192.0.2.8").read_bytes() != before  # new IP


def test_start_reports_failure_when_https_cannot_come_up(tmp_path):
    pem = phone._ensure_cert(tmp_path, phone._lan_ip())
    cert_only = pem.read_bytes().split(b"-----BEGIN PRIVATE KEY-----")[0]
    pem.write_bytes(cert_only)  # valid cert, key gone: uvicorn cannot load it
    server = phone.PhoneCameraServer({}, cert_dir=tmp_path)
    assert server.start(host="127.0.0.1", port=0) is None and server.url is None


@pytest.mark.parametrize("port", ["", "not-a-port"])
def test_bad_port_setting_does_not_break_the_mcp_server(monkeypatch, port):
    import importlib

    import multicam_mcp_server as srv

    monkeypatch.setenv("FRAMEGRAB_PHONE_CAMERAS_PORT", port)
    importlib.reload(srv)  # the setting is read at import time
    monkeypatch.delenv("FRAMEGRAB_PHONE_CAMERAS_PORT")
    importlib.reload(srv)


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
    assert server.pin in text and server.url in text
    assert image.data[:4] == b"\x89PNG" and not opened
    srv.add_phone_camera()
    assert opened == [1]


def test_main_defaults_to_stdio(monkeypatch):
    import multicam_mcp_server as srv

    calls = []
    monkeypatch.delenv("MULTICAM_MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(srv.mcp, "run", lambda **kwargs: calls.append(kwargs))
    srv.main()
    assert calls == [{"transport": "stdio"}]


def test_main_can_serve_streamable_http(monkeypatch):
    import multicam_mcp_server as srv

    calls = []
    monkeypatch.setenv("MULTICAM_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MULTICAM_MCP_HOST", "127.0.0.7")
    monkeypatch.setenv("MULTICAM_MCP_PORT", "8765")
    monkeypatch.setattr(srv.mcp, "run", lambda **kwargs: calls.append(kwargs))
    srv.main()
    assert calls == [
        {"transport": "streamable-http", "host": "127.0.0.7", "port": 8765}
    ]


@pytest.mark.parametrize("probe_result", ["blocked", "127.0.0.1", "10.8.0.2"])
def test_lan_address_uses_wifi_when_route_probe_is_unusable(monkeypatch, probe_result):
    from types import SimpleNamespace

    def address(ip):
        return SimpleNamespace(family=phone.socket.AF_INET, address=ip)

    monkeypatch.setattr(
        phone.psutil,
        "net_if_addrs",
        lambda: {
            "lo": [address("127.0.0.1")],
            "wlan0": [address("192.168.2.150")],
            "docker0": [address("172.17.0.1")],
            "tun0": [address("10.8.0.2")],
            "eth0": [address("192.168.1.10")],
        },
    )
    monkeypatch.setattr(
        phone.psutil,
        "net_if_stats",
        lambda: {
            name: SimpleNamespace(isup=name != "eth0")
            for name in ["lo", "wlan0", "docker0", "tun0", "eth0"]
        },
    )

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def connect(self, target):
            if probe_result == "blocked":
                raise PermissionError("route lookup blocked")

        def getsockname(self):
            return probe_result, 1234

    monkeypatch.setattr(phone.socket, "socket", lambda *args: Probe())
    assert phone._lan_ip() == "192.168.2.150"


def test_no_lan_address_never_advertises_loopback(monkeypatch):
    monkeypatch.setattr(phone.psutil, "net_if_addrs", lambda: {})
    monkeypatch.setattr(phone.psutil, "net_if_stats", lambda: {})
    with pytest.raises(RuntimeError, match="No usable local-network"):
        phone._lan_ip()


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "0.0.0.0",
        "localhost",
        "https://192.168.1.2:8443/",
        "::1",
        "224.0.0.1",
    ],
)
def test_manual_phone_address_rejects_unusable_values(value):
    with pytest.raises(ValueError):
        phone._phone_host_ip(value)


def test_blocked_interface_detection_asks_for_help(monkeypatch):
    def blocked():
        raise PermissionError("blocked")

    monkeypatch.setattr(phone.psutil, "net_if_stats", blocked)
    with pytest.raises(RuntimeError, match="Ask the user"):
        phone._lan_ip()


def test_manual_address_recovers_detection_failure(ctx, monkeypatch):
    import multicam_mcp_server as server

    registry, _, phone_server = ctx

    def no_detection():
        raise RuntimeError("No usable local-network address")

    monkeypatch.setattr(phone, "_lan_ip", no_detection)
    monkeypatch.setattr(server, "ENABLE_FRAMEGRAB_PHONE_CAMERAS", True)
    monkeypatch.setattr(server, "_phone_server", None)
    monkeypatch.setattr(server, "_phone_start_error", "No usable local-network address")
    monkeypatch.setattr(server, "FRAMEGRAB_PHONE_CAMERAS_PORT", "0")
    monkeypatch.setattr(server, "PhoneCameraServer", lambda cache: phone_server)
    original_start = phone_server.start
    monkeypatch.setattr(
        phone_server,
        "start",
        lambda **kwargs: original_start(host="127.0.0.1", **kwargs),
    )
    try:
        result = server.add_phone_camera(
            open_browser=False, computer_ip="192.168.2.150"
        )
        assert "https://192.168.2.150:" in result[0]
        assert server.get_phone_connection_info()["url"].startswith(
            "https://192.168.2.150:"
        )
        assert server._phone_start_error is None
        png = cv2.imdecode(
            np.frombuffer(phone_server.qr_png(), np.uint8), cv2.IMREAD_GRAYSCALE
        )
        assert cv2.QRCodeDetector().detectAndDecode(png)[0] == phone_server.join_url
    finally:
        phone_server.stop()


@contextmanager
def capture_request(ctx, name="cam", action=None):
    registry, client, server = ctx
    headers = {"authorization": "Bearer " + server._token(name)}
    assert (
        client.get(f"/api/cameras/{name}/request", headers=headers).status_code == 200
    )
    outcome = {}

    def capture():
        try:
            outcome["frame"] = (action or registry[name].grab)()
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=capture)
    thread.start()
    try:
        for _ in range(100):
            request_id = client.get(
                f"/api/cameras/{name}/request", headers=headers
            ).json()["request_id"]
            if request_id:
                break
            time.sleep(0.005)
        else:
            pytest.fail(f"No capture request: {outcome}")
        yield {**headers, "x-capture-request": request_id}, outcome, thread
    finally:
        thread.join(timeout=0.2)
        if thread.is_alive():
            registry[name].release()
            thread.join(timeout=2)
        assert not thread.is_alive()


def test_pairing_closed_by_default_and_at_60_seconds(tmp_path, monkeypatch):
    from types import SimpleNamespace

    now = [100.0]
    monkeypatch.setattr(phone, "time", SimpleNamespace(monotonic=lambda: now[0]))
    server = phone.PhoneCameraServer({}, cert_dir=tmp_path)
    client = TestClient(server.app)
    assert (
        client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code
        == 403
    )
    server.open_join_window()
    old_pin = server.pin
    now[0] = 159.999
    headers = join(client, server, "cam")
    now[0] = 160.0
    assert not server.pairing_info()["joining_open"]
    assert server.pairing_info()["pin"] == ""
    assert (
        client.post("/api/cameras", json={"name": "late", "pin": old_pin}).status_code
        == 403
    )
    # Even a previously authorized token cannot register/re-register outside the window.
    assert (
        client.post("/api/cameras", json={"name": "cam"}, headers=headers).status_code
        == 403
    )
    # Existing authorized cameras can still poll for requested snapshots.
    assert client.get("/api/cameras/cam/request", headers=headers).status_code == 200
    server.open_join_window()
    assert server.pin != old_pin
    assert (
        client.post("/api/cameras", json={"name": "late", "pin": old_pin}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/cameras", json={"name": "late", "pin": server.pin}
        ).status_code
        == 201
    )


def test_requests_and_uploads_require_camera_token(ctx):
    _, client, server = ctx
    cam = join(client, server, "cam")
    other = join(client, server, "other")
    for headers in (
        None,
        other,
        {"authorization": "Bearer forged"},
        {"authorization": "Bearer é".encode("latin-1")},
    ):
        assert (
            client.get("/api/cameras/cam/request", headers=headers).status_code == 401
        )
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=jpeg()
            ).status_code
            == 401
        )
    assert client.get("/api/cameras/unknown/request").status_code == 401
    assert client.get("/api/cameras/cam/request", headers=cam).json() == {
        "request_id": None
    }
    assert (
        client.post("/api/cameras/cam/frame", headers=cam, content=jpeg()).status_code
        == 409
    )


def test_solicited_photo_roundtrip_and_replay_rejection(ctx):
    _, client, server = ctx
    join(client, server, "cam")
    with capture_request(ctx) as (headers, result, thread):
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=jpeg(640, 480)
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=jpeg()
            ).status_code
            == 409
        )
        thread.join(timeout=2)
        assert result["frame"].shape == (480, 640, 3)
    assert client.get("/api/cameras/cam/request", headers=headers).json() == {
        "request_id": None
    }


def test_unsolicited_body_is_rejected_before_reading(ctx, monkeypatch):
    _, client, server = ctx
    headers = join(client, server, "cam")

    async def forbidden(*args):
        pytest.fail("Unsolicited image body was read")

    monkeypatch.setattr(phone, "_read_capped", forbidden)
    assert (
        client.post(
            "/api/cameras/cam/frame", headers=headers, content=jpeg()
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/cameras/cam/frame",
            headers={**headers, "x-capture-request": "guessed"},
            content=jpeg(),
        ).status_code
        == 409
    )


def test_wrong_nonce_and_cross_camera_nonce_are_rejected(ctx):
    _, client, server = ctx
    join(client, server, "cam")
    other = join(client, server, "other")
    with capture_request(ctx) as (headers, result, thread):
        wrong = {**headers, "x-capture-request": "wrong"}
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=wrong, content=jpeg()
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/cameras/other/frame",
                headers={**other, "x-capture-request": headers["x-capture-request"]},
                content=jpeg(),
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=jpeg()
            ).status_code
            == 200
        )
        thread.join(timeout=2)
        assert "frame" in result


def test_capture_timeout_has_no_cached_preview_fallback(ctx, monkeypatch):
    registry, client, server = ctx
    headers = join(client, server, "cam")
    client.get("/api/cameras/cam/request", headers=headers)
    monkeypatch.setattr(phone, "HIRES_TIMEOUT_S", 0.05)
    with pytest.raises(RuntimeError, match="did not answer"):
        registry["cam"].grab()
    assert client.get("/api/cameras/cam/request", headers=headers).json() == {
        "request_id": None
    }
    assert (
        client.post(
            "/api/cameras/cam/frame", headers=headers, content=jpeg()
        ).status_code
        == 409
    )


def test_late_photo_rejected(ctx, monkeypatch):
    _, client, server = ctx
    join(client, server, "cam")
    monkeypatch.setattr(phone, "HIRES_TIMEOUT_S", 0.1)
    with capture_request(ctx) as (headers, result, thread):
        thread.join(timeout=1)
        assert "error" in result
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=jpeg()
            ).status_code
            == 409
        )


@pytest.mark.parametrize(
    "body,limit,status", [(b"GIF89a", 100, 400), (jpeg(), 10, 413)]
)
def test_requested_upload_validation(ctx, monkeypatch, body, limit, status):
    _, client, server = ctx
    join(client, server, "cam")
    monkeypatch.setattr(phone, "MAX_FRAME_BYTES", limit)
    with capture_request(ctx) as (headers, _, __):
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=body
            ).status_code
            == status
        )
        # A malformed response consumes its request too, preventing an upload flood.
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=body
            ).status_code
            == 409
        )


def test_undecodable_response_fails_without_returning_old_image(ctx):
    _, client, server = ctx
    join(client, server, "cam")
    with capture_request(ctx) as (headers, result, thread):
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=b"\xff\xd8bad"
            ).status_code
            == 200
        )
        thread.join(timeout=2)
        assert "undecodable" in str(result["error"])


def test_release_revokes_pending_capture(ctx):
    registry, client, server = ctx
    join(client, server, "cam")
    with capture_request(ctx) as (headers, result, thread):
        registry["cam"].release()
        assert (
            client.get("/api/cameras/cam/request", headers=headers).status_code == 401
        )
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=jpeg()
            ).status_code
            == 401
        )
        thread.join(timeout=2)
        assert "error" in result


def test_restart_invalidates_old_tokens_and_requires_new_pin(tmp_path):
    server = phone.PhoneCameraServer({}, cert_dir=tmp_path)
    server.open_join_window()
    headers = join(TestClient(server.app), server, "cam")
    restarted = phone.PhoneCameraServer({}, cert_dir=tmp_path)
    client = TestClient(restarted.app)
    assert (
        client.post("/api/cameras", json={"name": "cam"}, headers=headers).status_code
        == 403
    )
    restarted.open_join_window()
    assert (
        client.post("/api/cameras", json={"name": "cam"}, headers=headers).status_code
        == 401
    )
    new_headers = join(client, restarted, "cam")
    assert new_headers != headers
    assert client.get("/api/cameras/cam/request", headers=headers).status_code == 401
    assert (
        client.get("/api/cameras/cam/request", headers=new_headers).status_code == 200
    )


def test_mcp_grab_returns_only_solicited_image(ctx, monkeypatch):
    import multicam_mcp_server as srv

    registry, client, server = ctx
    monkeypatch.setattr(srv, "_grabber_cache", registry)
    join(client, server, "cam")
    with capture_request(ctx, action=lambda: srv.grab_frame("cam", "jpg")) as (
        headers,
        result,
        thread,
    ):
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=jpeg()
            ).status_code
            == 200
        )
        thread.join(timeout=2)
        assert result["frame"].data[:2] == b"\xff\xd8"


def test_not_ready_phone_fails_without_capture(ctx):
    registry, client, server = ctx
    join(client, server, "cam")
    with pytest.raises(RuntimeError, match="Is the page still open"):
        registry["cam"].grab()


def test_concurrent_replay_only_one_body_is_accepted(ctx):
    from concurrent.futures import ThreadPoolExecutor

    _, client, server = ctx
    join(client, server, "cam")
    with capture_request(ctx) as (headers, result, thread):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda _: client.post(
                        "/api/cameras/cam/frame", headers=headers, content=jpeg()
                    ).status_code,
                    range(2),
                )
            )
        assert sorted(responses) == [200, 409]
        thread.join(timeout=2)
        assert "frame" in result


def test_response_expiring_during_upload_is_rejected(ctx, monkeypatch):
    import anyio

    _, client, server = ctx
    join(client, server, "cam")
    monkeypatch.setattr(phone, "HIRES_TIMEOUT_S", 0.1)

    async def slow_body(request, limit):
        await anyio.sleep(0.2)
        return jpeg()

    monkeypatch.setattr(phone, "_read_capped", slow_body)
    with capture_request(ctx) as (headers, result, thread):
        assert (
            client.post(
                "/api/cameras/cam/frame", headers=headers, content=jpeg()
            ).status_code
            == 409
        )
        thread.join(timeout=1)
        assert "error" in result


def test_pin_lookup_does_not_reopen_joining(ctx, monkeypatch):
    import multicam_mcp_server as srv

    _, _, server = ctx
    server.url = "https://192.168.2.150:8443/"
    server._pairing = (server.pin, 0.0)
    monkeypatch.setattr(srv, "_phone_server", server)
    info = srv.get_phone_connection_info()
    assert not info["joining_open"] and info["pin"] == ""
    assert server._pairing[1] == 0.0


def test_no_image_can_be_fetched_from_phone_listener(ctx):
    _, client, server = ctx
    join(client, server, "cam")
    assert client.get("/api/cameras").status_code == 405
    assert client.get("/api/cameras/cam/frame").status_code == 405
    assert client.get("/api/cameras/cam/request").status_code == 401


def test_cross_origin_and_unexpected_host_rejected(ctx):
    _, client, server = ctx
    server._allowed_hosts = {"testserver"}
    assert client.get("/", headers={"host": "attacker.example"}).status_code == 403
    assert (
        client.post(
            "/api/cameras",
            json={"name": "cam", "pin": server.pin},
            headers={"origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/cameras",
            json={"name": "cam", "pin": server.pin},
            headers={"origin": "http://testserver"},
        ).status_code
        == 201
    )


def test_security_headers_and_script_hash(ctx):
    import hashlib
    import base64
    import re

    response = ctx[1].get("/")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "microphone=()" in response.headers["permissions-policy"]
    script = re.search(rb"<script>(.*?)</script>", response.content, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
    assert "'sha256-" + digest + "'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]


def test_disconnect_revokes_token_and_name_can_pair_with_new_token(ctx):
    _, client, server = ctx
    headers = join(client, server, "cam")
    assert client.delete("/api/cameras/cam", headers=headers).status_code == 200
    assert client.get("/api/cameras/cam/request", headers=headers).status_code == 401
    assert (
        client.post("/api/cameras", headers=headers, json={"name": "cam"}).status_code
        == 401
    )
    new_headers = join(client, server, "cam")
    assert new_headers != headers
    assert (
        client.post(
            "/api/cameras/cam/frame", headers=headers, content=jpeg()
        ).status_code
        == 401
    )


def test_pin_does_not_allow_takeover_of_existing_camera(ctx):
    _, client, server = ctx
    original = join(client, server, "cam")
    assert (
        client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code
        == 409
    )
    assert client.get("/api/cameras/cam/request", headers=original).status_code == 200


def test_distributed_pin_guesses_close_pairing(ctx):
    _, _, server = ctx
    for i in range(phone.PIN_GLOBAL_MAX_FAILURES):
        client = TestClient(server.app, client=(f"192.0.2.{i + 1}", 1234))
        result = client.post(
            "/api/cameras", json={"name": "cam", "pin": wrong_pin(server)}
        )
        assert result.status_code == (
            429 if i == phone.PIN_GLOBAL_MAX_FAILURES - 1 else 401
        )
    assert not server.pairing_info()["joining_open"]
    assert (
        client.post("/api/cameras", json={"name": "cam", "pin": server.pin}).status_code
        == 403
    )


def test_registration_body_is_bounded(ctx):
    assert ctx[1].post("/api/cameras", content=b"x" * 3000).status_code == 413
