"""Self-contained plugin entrypoint: STDIO bridge to a shared local camera service."""

import contextlib
import json
import logging
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import tempfile
import time

import anyio
import httpx2
import platformdirs

STARTUP_SECONDS = 25
IDLE_SECONDS = 120


def data_dir() -> Path:
    path = Path(platformdirs.user_data_dir("multicam-mcp-server")) / "plugin"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextlib.contextmanager
def service_lock(path: Path):
    """An OS lock is released even when a service crashes."""
    with path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        try:
            yield True
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def read_state(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text())
        port = state["port"]
        token = state["token"]
        if (
            type(port) is int
            and 0 < port < 65536
            and isinstance(token, str)
            and len(token) == 64
            and state["protocol"] == 2
            and isinstance(state["certificate"], str)
        ):
            tls_context(state)
            return state
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def tls_context(state: dict) -> ssl.SSLContext:
    # Trust only this service instance, never system roots or environment overrides.
    # TLS authenticates every connection before HTTP can disclose the bearer token.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=state["certificate"])
    return context


def client_for(state: dict) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        verify=tls_context(state),
        headers={"Authorization": f"Bearer {state['token']}"},
        trust_env=False,
        timeout=httpx2.Timeout(30, connect=2),
    )


def service_url(state: dict) -> str:
    return f"https://127.0.0.1:{state['port']}"


async def healthy(state: dict | None) -> bool:
    if state is None:
        return False
    try:
        async with client_for(state) as client:
            response = await client.get(service_url(state) + "/health", timeout=2)
            return response.status_code == 200 and response.json() == {
                "service": "multicam-plugin-v1"
            }
    except (httpx2.HTTPError, OSError, ValueError, KeyError):
        return False


async def ensure_service() -> dict:
    folder = data_dir()
    state_path = folder / "service.json"
    state = read_state(state_path)
    if await healthy(state):
        return state
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).resolve()))
    command.append("--service")
    options = (
        {"start_new_session": True}
        if sys.platform != "win32"
        else {
            "creationflags": subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
        }
    )
    with (folder / "service.log").open("ab") as log:
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options
        )
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        state = read_state(state_path)
        if await healthy(state):
            return state
        await anyio.sleep(0.2)
    raise RuntimeError(
        f"Multicam could not start. Diagnostic log: {folder / 'service.log'}"
    )


async def bridge():
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.stdio import stdio_server

    state = await ensure_service()
    async with stdio_server() as (local_read, local_write):
        async with client_for(state) as client:
            async with streamable_http_client(
                service_url(state) + "/mcp", http_client=client
            ) as remote:
                async with anyio.create_task_group() as group:

                    async def forward(source, destination):
                        try:
                            async for message in source:
                                if isinstance(message, Exception):
                                    raise message
                                await destination.send(message)
                        finally:
                            group.cancel_scope.cancel()

                    async def heartbeat():
                        while True:
                            await anyio.sleep(20)
                            response = await client.get(
                                service_url(state) + "/health", timeout=3
                            )
                            response.raise_for_status()

                    group.start_soon(forward, local_read, remote[1])
                    group.start_soon(forward, remote[0], local_write)
                    group.start_soon(heartbeat)


def serve():
    import asyncio
    import hmac
    import socket

    import uvicorn
    from starlette.responses import JSONResponse, Response

    import multicam_mcp_server as server
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    from multicam_mcp_phone import _ensure_cert, _write_private

    folder = data_dir()
    with (
        service_lock(folder / "service.lock") as acquired,
        tempfile.TemporaryDirectory(prefix="tls-", dir=folder) as tls_dir,
    ):
        if not acquired:
            return
        pem_path = _ensure_cert(Path(tls_dir), "127.0.0.1")
        certificate = (
            x509.load_pem_x509_certificate(pem_path.read_bytes())
            .public_bytes(Encoding.PEM)
            .decode("ascii")
        )
        server.ENABLE_FRAMEGRAB_PHONE_CAMERAS = True
        # No automatic camera discovery or RTSP network scanning in the consumer plugin.
        server.ENABLE_FRAMEGRAB_AUTO_DISCOVERY = False
        app = server.mcp.streamable_http_app()
        token = secrets.token_hex(32)
        last_contact = time.monotonic()

        async def authenticated(scope, receive, send):
            nonlocal last_contact
            if scope["type"] == "lifespan":
                try:
                    await app(scope, receive, send)
                finally:
                    # Uvicorn re-raises termination signals after lifespan shutdown;
                    # remove discovery state before it restores the signal handler.
                    (folder / "service.json").unlink(missing_ok=True)
                return
            if scope["type"] != "http":
                return await app(scope, receive, send)
            headers = dict(scope["headers"])
            if not hmac.compare_digest(
                headers.get(b"authorization", b""), f"Bearer {token}".encode()
            ):
                return await Response(status_code=401)(scope, receive, send)
            last_contact = time.monotonic()
            if scope["path"] == "/health":
                return await JSONResponse({"service": "multicam-plugin-v1"})(
                    scope, receive, send
                )
            await app(scope, receive, send)

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            runner = uvicorn.Server(
                uvicorn.Config(
                    authenticated,
                    log_config=None,
                    access_log=False,
                    timeout_graceful_shutdown=5,
                    ssl_certfile=str(pem_path),
                )
            )

            async def run():
                async def expire():
                    while not runner.should_exit:
                        await asyncio.sleep(5)
                        if time.monotonic() - last_contact > IDLE_SECONDS:
                            runner.should_exit = True

                task = asyncio.create_task(expire())
                try:
                    await runner.serve(sockets=[sock])
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

            _write_private(
                folder / "service.json",
                json.dumps(
                    {
                        "protocol": 2,
                        "port": port,
                        "token": token,
                        "pid": os.getpid(),
                        "certificate": certificate,
                    }
                ).encode(),
            )
            try:
                asyncio.run(run())
            finally:
                (folder / "service.json").unlink(missing_ok=True)


def main():
    import multiprocessing

    multiprocessing.freeze_support()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if "--service" in sys.argv:
        serve()
    else:
        anyio.run(bridge)


if __name__ == "__main__":
    main()
