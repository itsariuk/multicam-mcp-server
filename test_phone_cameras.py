import ssl
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
    registry, client = ctx
    registry["usb0"] = object()
    assert client.post("/api/cameras", json={"name": "usb0"}).status_code == 409
    assert client.post("/api/cameras/usb0/frame", content=jpeg()).status_code == 404


def test_frame_rejections(ctx, monkeypatch):
    _, client = ctx
    assert client.post("/api/cameras/ghost/frame", content=jpeg()).status_code == 404
    client.post("/api/cameras", json={"name": "cam"})
    assert client.post("/api/cameras/cam/frame", content=b"GIF89a").status_code == 400
    monkeypatch.setattr(phone, "MAX_FRAME_BYTES", 10)
    assert client.post("/api/cameras/cam/frame", content=jpeg()).status_code == 413


def test_camera_cap(ctx, monkeypatch):
    monkeypatch.setattr(phone, "MAX_PHONE_CAMERAS", 1)
    assert ctx[1].post("/api/cameras", json={"name": "a"}).status_code == 201
    assert ctx[1].post("/api/cameras", json={"name": "b"}).status_code == 429


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


def test_tls_server_starts_serves_and_stops(tmp_path):
    server = phone.PhoneCameraServer({}, cert_dir=tmp_path)
    url = server.start(host="127.0.0.1", port=0)
    try:
        assert url.startswith("https://")
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        # A verifying client: proves the generated cert's SAN is right.
        tls = ssl.create_default_context(cafile=str(tmp_path / "server.pem"))
        body = urllib.request.urlopen(
            f"https://127.0.0.1:{port}/", context=tls, timeout=5
        ).read()
        assert b"<!doctype html>" in body.lower()
        # Port already taken -> feature disabled, not a crash.
        other = phone.PhoneCameraServer({}, cert_dir=tmp_path)
        assert other.start(host="127.0.0.1", port=port) is None
    finally:
        server.stop()


def test_unusable_stored_cert_is_replaced(tmp_path):
    (tmp_path / "server.pem").write_bytes(b"-----BEGIN CERTIFICATE-----\ntruncat")
    pem = phone._ensure_cert(tmp_path, "192.0.2.7")
    tls = ssl.create_default_context(cafile=str(pem))  # parses, so it was regenerated
    assert tls.cert_store_stats()["x509"] == 1
    before = pem.read_bytes()
    assert phone._ensure_cert(tmp_path, "192.0.2.7").read_bytes() == before  # reused
    assert phone._ensure_cert(tmp_path, "192.0.2.8").read_bytes() != before  # new IP


def test_start_reports_failure_when_https_cannot_come_up(tmp_path):
    pem = phone._ensure_cert(tmp_path, phone._lan_ip())
    cert_only = pem.read_bytes().split(b"-----BEGIN PRIVATE KEY-----")[0]
    pem.write_bytes(cert_only)  # valid cert, key gone: uvicorn cannot load it
    server = phone.PhoneCameraServer({}, cert_dir=tmp_path)
    assert server.start(host="127.0.0.1", port=0) is None


@pytest.mark.parametrize("port", ["", "not-a-port"])
def test_bad_port_setting_does_not_break_the_mcp_server(monkeypatch, port):
    import importlib

    import multicam_mcp_server as srv

    monkeypatch.setenv("FRAMEGRAB_PHONE_CAMERAS_PORT", port)
    importlib.reload(srv)  # the setting is read at import time
    monkeypatch.delenv("FRAMEGRAB_PHONE_CAMERAS_PORT")
    importlib.reload(srv)


def test_grab_frame_tool_serves_phone_camera():
    import multicam_mcp_server as srv

    client = TestClient(phone.PhoneCameraServer(srv._grabber_cache).app)
    try:
        client.post("/api/cameras", json={"name": "tool-test"})
        client.post("/api/cameras/tool-test/frame", content=jpeg())
        assert "tool-test" in srv.list_framegrabbers()
        assert srv.grab_frame("tool-test", "jpg").data[:2] == b"\xff\xd8"
        assert srv.release_framegrabber("tool-test") is True
        assert (
            client.post("/api/cameras/tool-test/frame", content=jpeg()).status_code
            == 410
        )
    finally:
        srv._grabber_cache.pop("tool-test", None)
