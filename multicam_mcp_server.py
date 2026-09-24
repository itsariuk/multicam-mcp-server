import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

import cv2
from framegrab import FrameGrabber
from framegrab.config import (
    BaslerFrameGrabberConfig,
    FileStreamFrameGrabberConfig,
    GenericUSBFrameGrabberConfig,
    HttpLiveStreamingFrameGrabberConfig,
    RealSenseFrameGrabberConfig,
    RTSPFrameGrabberConfig,
    YouTubeLiveFrameGrabberConfig,
)
from mcp.server.mcpserver import MCPServer, Image

from multicam_mcp_phone import PhoneCameraServer

logger = logging.getLogger(__name__)

ENABLE_FRAMEGRAB_AUTO_DISCOVERY = (
    os.getenv("ENABLE_FRAMEGRAB_AUTO_DISCOVERY", "false").lower() == "true"
)
FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE = os.getenv(
    "FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE",
    "off",  # "off", "ip_only", "light", "complete_fast", "complete_slow"
)
ENABLE_FRAMEGRAB_PHONE_CAMERAS = (
    os.getenv("ENABLE_FRAMEGRAB_PHONE_CAMERAS", "false").lower() == "true"
)
# Parsed only when phone cameras start, so a bad value cannot stop the MCP server.
FRAMEGRAB_PHONE_CAMERAS_PORT = os.getenv("FRAMEGRAB_PHONE_CAMERAS_PORT") or "8443"

# Cache to store created FrameGrabbers, maps name to FrameGrabber
_grabber_cache = {}

# The phone camera server while it is listening, else None. Set by app_lifespan.
_phone_server: PhoneCameraServer | None = None
_phone_start_error: str | None = None


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[Any]:
    if ENABLE_FRAMEGRAB_AUTO_DISCOVERY:
        logger.info("Autodiscovering generic_usb and basler framegrabbers...")
        try:
            grabbers: dict[str, FrameGrabber] = FrameGrabber.autodiscover(
                rtsp_discover_mode=FRAMEGRAB_RTSP_AUTO_DISCOVERY_MODE
            )
            logger.info(f"Autodiscovered {len(grabbers)} framegrabbers.")
            _grabber_cache.update(grabbers)
        except Exception:
            logger.error("Error autodiscovering framegrabbers.", exc_info=True)

    global _phone_server, _phone_start_error
    _phone_start_error = None
    if ENABLE_FRAMEGRAB_PHONE_CAMERAS:
        try:
            phone_server = PhoneCameraServer(_grabber_cache)
            if phone_server.start(port=int(FRAMEGRAB_PHONE_CAMERAS_PORT)):
                _phone_server = phone_server
        except Exception as exc:
            _phone_start_error = str(exc)
            logger.error("Error starting phone camera server.", exc_info=True)

    logger.info("Multicam MCP server has started, listening for requests...")

    yield {}

    logger.info("Multicam MCP server is stopping, releasing framegrabbers...")
    if _phone_server:
        _phone_server.stop()
        _phone_server = None
    for _, grabber in _grabber_cache.items():
        try:
            grabber.release()
        except Exception as e:
            logger.error(f"Error closing framegrabber {grabber}: {e}")
    logger.info("Done.")


mcp = MCPServer(
    "multicam",
    instructions=(
        "Multicam supplies on-demand camera snapshots, not continuous vision. "
        "add_phone_camera opens a 60-second pairing window; QR contains only the URL, PIN is separate. "
        "Explain local-network exposure and PIN/token protection. Expired joining needs a user-requested reopen. "
        "For PIN/status use get_phone_connection_info; it does not reopen joining. "
        "List cameras and capture a fresh frame before describing the current scene. "
        "Keep voice replies short. Never guess unreadable details or claim a journal was saved "
        "without using a separate storage tool."
    ),
    dependencies=[
        "framegrab>=0.11.0",
        "opencv-python",
        "numpy",
        "pypylon",
    ],
    lifespan=app_lifespan,
)


@mcp.tool(
    name="create_framegrabber",
    description="""Create a new framegrabber from a configuration object.
Framegrabbers can be used to capture images from a webcam, a USB camera, an RTSP stream, a youtube live stream, or any other video source supported by the framegrab library.
Returns the name of the created framegrabber.""",
)
def create_framegrabber(
    config: YouTubeLiveFrameGrabberConfig
    | RTSPFrameGrabberConfig
    | GenericUSBFrameGrabberConfig
    | FileStreamFrameGrabberConfig
    | HttpLiveStreamingFrameGrabberConfig
    | RealSenseFrameGrabberConfig
    | BaslerFrameGrabberConfig,
) -> str:
    try:
        # Create the new grabber
        grabber = FrameGrabber.create_grabber(config)
        _grabber_cache[config.name] = grabber
        logger.info(f"Created new framegrabber: {config.name}")
        return config.name
    except Exception as e:
        logger.error(f"Error creating framegrabber: {e}")
        raise ValueError(f"Failed to create framegrabber: {str(e)}")


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

    frame = grabber.grab()
    if format not in ["png", "jpg", "webp"]:
        raise ValueError("Format must be one of: png, jpg, webp")

    # Convert ndarray to bytes in specified format
    if format == "jpg":
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, 80]
        success, buffer = cv2.imencode(f".{format}", frame, encode_params)
    elif format == "png":
        # Use compression level 9 (highest) for PNG to reduce size
        encode_params = [cv2.IMWRITE_PNG_COMPRESSION, 9]
        success, buffer = cv2.imencode(f".{format}", frame, encode_params)
    elif format == "webp":
        # Use quality 80 for WebP to balance size and quality
        encode_params = [cv2.IMWRITE_WEBP_QUALITY, 80]
        success, buffer = cv2.imencode(f".{format}", frame, encode_params)
    else:
        success, buffer = cv2.imencode(f".{format}", frame)

    if not success:
        raise RuntimeError(f"Failed to encode image as {format.upper()}.")

    # Create MCP Image object from the encoded bytes
    return Image(data=buffer.tobytes(), format=format)


@mcp.tool(
    name="list_framegrabbers",
    description="List all available framegrabbers by name, sorted alphanumerically.",
)
def list_framegrabbers() -> list[str]:
    return sorted(list(_grabber_cache.keys()))


@mcp.tool(
    name="get_framegrabber_config",
    description="Retrieve the configuration of a specific framegrabber.",
)
def get_framegrabber_config(framegrabber_name: str) -> dict:
    grabber: FrameGrabber = _grabber_cache.get(framegrabber_name)
    if not grabber:
        raise ValueError(
            f"Framegrabber with name {framegrabber_name} not found. Options are: {list(_grabber_cache.keys())}."
        )
    return grabber.config


@mcp.tool(
    name="set_config",
    description="Update the configuration options for a specific framegrabber.",
)
def set_framegrabber_config(framegrabber_name: str, options: dict) -> dict:
    grabber: FrameGrabber = _grabber_cache.get(framegrabber_name)
    if not grabber:
        raise ValueError(
            f"Framegrabber with name {framegrabber_name} not found. Options are: {list(_grabber_cache.keys())}."
        )

    try:
        # Update the framegrabber's configuration with the new options
        grabber.apply_options(options)
        logger.info(f"Updated configuration for framegrabber '{framegrabber_name}'")
        return grabber.config
    except Exception as e:
        logger.error(f"Error applying options to {framegrabber_name}: {e}")
        raise ValueError(f"Failed to apply options to framegrabber: {str(e)}")


@mcp.tool(
    name="release_grabber",
    description="Release a framegrabber and remove it from the available grabbers.",
)
def release_framegrabber(framegrabber_name: str) -> bool:
    """
    Release a framegrabber's resources and remove it from the available grabbers.

    Returns True if successful, raises an exception otherwise.
    """
    grabber: FrameGrabber = _grabber_cache.get(framegrabber_name)
    if not grabber:
        raise ValueError(
            f"Framegrabber with name {framegrabber_name} not found. Options are: {list(_grabber_cache.keys())}."
        )

    try:
        grabber.release()
        del _grabber_cache[framegrabber_name]
        logger.info(f"Released framegrabber: {framegrabber_name}")
        return True
    except Exception as e:
        logger.error(f"Error releasing framegrabber {framegrabber_name}: {e}")
        raise ValueError(f"Failed to release framegrabber: {str(e)}")


@mcp.tool(
    name="get_phone_connection_info",
    description="Get the phone URL, pairing-window status, and current PIN while joining is open. Does not reopen joining. If closed, ask the user to request a new 60-second window via add_phone_camera. Keep the PIN private.",
)
def get_phone_connection_info() -> dict:
    if _phone_server is None:
        raise ValueError(
            (_phone_start_error + " " if _phone_start_error else "")
            + "Phone cameras are not running. If address detection failed, ask the user for this computer's "
            "Wi-Fi IPv4 address from Network Settings, then call add_phone_camera with computer_ip."
        )
    return {
        "url": _phone_server.url,
        "join_url": _phone_server.join_url,
        **_phone_server.pairing_info(),
    }


@mcp.tool(
    name="add_phone_camera",
    description="""Get what a person needs to connect a phone as a camera: a link, a separate PIN, and a QR code containing only the link.
Opens or reopens joining for 60 seconds and rotates the PIN. Use when the user requests connecting a phone or reopening joining, not merely to check the PIN. Explain that the phone page is reachable on the local network but joining requires the PIN and a live pairing window; uploads require an authorized camera token.
By default it also opens a page with the QR code in this computer's browser. Tell the user the link and the PIN.
If address detection fails, ask for the computer's Wi-Fi IPv4 address from Network Settings and pass it as computer_ip. Never guess or use localhost. Changing computer_ip briefly restarts the phone listener.
Once the phone has joined, its name appears in list_framegrabbers and works with grab_frame.""",
)
def add_phone_camera(open_browser: bool = True, computer_ip: str | None = None) -> list:
    """computer_ip is the computer's user-provided LAN IPv4 address when detection fails."""
    global _phone_server, _phone_start_error
    if computer_ip is not None and ENABLE_FRAMEGRAB_PHONE_CAMERAS:
        from multicam_mcp_phone import _phone_host_ip

        ip = _phone_host_ip(computer_ip)
        if _phone_server is not None:
            # Rebuild the HTTPS certificate and QR when the user corrects an address.
            # Keep the existing registry, PIN, and phone token key.
            _phone_server.stop()
            phone_server = _phone_server
            _phone_server = None
        else:
            phone_server = PhoneCameraServer(_grabber_cache)
        try:
            if phone_server.start(
                port=int(FRAMEGRAB_PHONE_CAMERAS_PORT), computer_ip=ip
            ):
                _phone_server = phone_server
                _phone_start_error = None
        except Exception as exc:
            _phone_start_error = str(exc)
            raise
    if _phone_server is None:
        raise ValueError(
            (
                _phone_start_error
                + " Ask the user for the computer's Wi-Fi IPv4 address from Network Settings, "
                "then retry add_phone_camera with computer_ip. Never substitute 127.0.0.1. "
                if _phone_start_error
                else ""
            )
            + "Phone cameras are not running. Set ENABLE_FRAMEGRAB_PHONE_CAMERAS=true in this MCP server's "
            f"environment and restart it. If it is already set, port {FRAMEGRAB_PHONE_CAMERAS_PORT} may be "
            "in use by another copy of this server; set FRAMEGRAB_PHONE_CAMERAS_PORT to a free port."
        )
    _phone_server.open_join_window()
    browser_status = ""
    if open_browser:
        if _phone_server.open_join_page():
            browser_status = " The joining page was sent to the desktop browser."
        else:
            browser_status = " The desktop browser could not be opened; show the returned QR code instead."
    return [
        f"On the phone, open {_phone_server.url} (same network as this computer) or scan the QR code. "
        f"PIN: {_phone_server.pin}. The phone will show a certificate warning the first time; that is expected."
        " Joining is open for 60 seconds. After it closes, ask Codex to open joining again for a new PIN."
        " This page is reachable on the local network, but viewing it does not grant access:"
        " new phones need the PIN while joining is open; frame uploads require an authorized camera token."
        + browser_status,
        Image(data=_phone_server.qr_png(), format="png"),
    ]


@mcp.resource(
    uri="fg://framegrabbers",
    name="framegrabbers",
    description="Lists all available framegrabbers by name, sorted alphanumerically.",
    mime_type="application/json",
)
def framegrabbers() -> list[str]:
    return list_framegrabbers()


def main():
    transport = os.getenv("MULTICAM_MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=os.getenv("MULTICAM_MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("MULTICAM_MCP_PORT", "8000")),
        )
    else:
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
