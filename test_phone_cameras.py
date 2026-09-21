import ssl
import sys
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
    return registry, TestClient(server.app), server


def join(client, server, name) -> dict:
    """Register with the PIN; return the headers that authorise frame posts."""
    r = client.post("/api/cameras", json={"name": name, "pin": server.pin})
    assert r.status_code in (200, 201), r.text
    return {"authorization": "Bearer " + r.json()["token"]}


def wrong_pin(server) -> str:
    return "000000" if server.pin != "000000" else "111111"


def test_register_post_grab_roundtrip(ctx):
    registry, client, server = ctx
    body = {"name": "left bench", "pin": server.pin}
    assert client.post("/api/cameras", json=body).status_code == 201
    assert client.post("/api/cameras", json=body).status_code == 200
    headers = join(client, server, "left bench")
    r = client.post("/api/cameras/left bench/frame", content=jpeg(), headers=headers)
    assert r.status_code == 200 and r.json() == {"hires_requested": False}
    assert registry["left bench"].grab().shape == (48, 64, 3)
    assert registry["left bench"].config["input_type"] == "phone_browser"


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


def test_frame_rejections(ctx, monkeypatch):
    _, client, server = ctx
    cam = join(client, server, "cam")

    def post(body):
        return client.post("/api/cameras/cam/frame", content=body, headers=cam)

    assert post(b"GIF89a").status_code == 400
    monkeypatch.setattr(phone, "MAX_FRAME_BYTES", 10)
    assert post(jpeg()).status_code == 413


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


def test_grab_errors_are_actionable(ctx, monkeypatch):
    registry, client, server = ctx
    cam = join(client, server, "cam")
    with pytest.raises(RuntimeError, match="has not sent a frame"):
        registry["cam"].grab()
    client.post("/api/cameras/cam/frame", content=jpeg(), headers=cam)
    monkeypatch.setattr(phone, "STALE_AFTER_S", -1)
    with pytest.raises(RuntimeError, match="Is the page still open"):
        registry["cam"].grab()


def test_release_stops_phone_until_it_rejoins(ctx):
    registry, client, _ = ctx
    cam = join(client, ctx[2], "cam")
    registry.pop("cam").release()  # what release_framegrabber does
    assert (
        client.post("/api/cameras/cam/frame", content=jpeg(), headers=cam).status_code
        == 410
    )
    # Rejoining needs no PIN: the phone still holds its token.
    assert (
        client.post("/api/cameras", json={"name": "cam"}, headers=cam).status_code
        == 201
    )
    assert (
        client.post("/api/cameras/cam/frame", content=jpeg(), headers=cam).status_code
        == 200
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


def test_frames_need_the_token_for_that_name(ctx):
    _, client, server = ctx
    cam, other = join(client, server, "cam"), join(client, server, "other")

    def post(name, headers=None):
        url = f"/api/cameras/{name}/frame"
        return client.post(url, content=jpeg(), headers=headers).status_code

    assert post("cam") == 401
    assert post("cam", headers=other) == 401
    assert post("cam", headers={"authorization": "Bearer é".encode("latin-1")}) == 401
    assert post("ghost") == 401  # same answer as a real name: no way to list cameras
    assert post("cam", headers=cam) == 200


def test_token_rejoins_without_pin_even_after_restart(tmp_path):
    first = phone.PhoneCameraServer({}, cert_dir=tmp_path)
    headers = join(TestClient(first.app), first, "cam")
    # A restart: new PIN, same token key on disk.
    restarted = TestClient(phone.PhoneCameraServer({}, cert_dir=tmp_path).app)
    assert (
        restarted.post(
            "/api/cameras", json={"name": "cam"}, headers=headers
        ).status_code
        == 201
    )
    assert (
        restarted.post(
            "/api/cameras/cam/frame", content=jpeg(), headers=headers
        ).status_code
        == 200
    )


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


def test_token_key_is_persisted_private_and_self_healing(tmp_path):
    first = phone._load_secret(tmp_path)
    assert phone._load_secret(tmp_path) == first and len(first) == 32
    assert (tmp_path / "token.key").stat().st_mode & 0o777 == 0o600
    (tmp_path / "token.key").write_bytes(b"short")
    assert phone._load_secret(tmp_path) != first


def test_qr_and_join_page_carry_the_link_and_pin(ctx):
    _, _, server = ctx
    server.url = "https://192.0.2.7:8443/"
    png = cv2.imdecode(np.frombuffer(server.qr_png(), np.uint8), cv2.IMREAD_GRAYSCALE)
    decoded = cv2.QRCodeDetector().detectAndDecode(png)[0]
    assert decoded == f"https://192.0.2.7:8443/#pin={server.pin}"
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


def test_grab_frame_tool_serves_phone_camera(tmp_path):
    import multicam_mcp_server as srv

    server = phone.PhoneCameraServer(srv._grabber_cache, cert_dir=tmp_path)
    client = TestClient(server.app)
    try:
        cam = join(client, server, "tool-test")
        client.post("/api/cameras/tool-test/frame", content=jpeg(), headers=cam)
        assert "tool-test" in srv.list_framegrabbers()
        assert srv.grab_frame("tool-test", "jpg").data[:2] == b"\xff\xd8"
        assert srv.release_framegrabber("tool-test") is True
        r = client.post("/api/cameras/tool-test/frame", content=jpeg(), headers=cam)
        assert r.status_code == 410
    finally:
        srv._grabber_cache.pop("tool-test", None)


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
