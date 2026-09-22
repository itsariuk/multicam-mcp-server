"""Scratch driver: spawn the MCP server over stdio (as Codex would) and run tool calls
read from cmd.txt (one per line: list | grab NAME | release NAME | config NAME | quit).
Results go to drive.log; grabbed images to grab_N.<fmt>."""

import asyncio
import base64
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).parent
CMD, LOG = HERE / "cmd.txt", HERE / "drive.log"
REPO = sys.argv[1]


def log(msg):
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


async def main():
    CMD.write_text("")
    LOG.write_text("")
    params = StdioServerParameters(
        command="uv",
        args=["run", "--project", REPO, "multicam-mcp-server"],
        env={**os.environ, "ENABLE_FRAMEGRAB_PHONE_CAMERAS": "true"},
    )
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        log("READY tools=" + ",".join(t.name for t in (await s.list_tools()).tools))
        done, n = 0, 0
        while True:
            lines = CMD.read_text().splitlines()
            for line in lines[done:]:
                done += 1
                op, _, arg = line.strip().partition(" ")
                if op == "quit":
                    log("BYE")
                    return
                tool, args = {
                    "list": ("list_framegrabbers", {}),
                    "grab": ("grab_frame", {"framegrabber_name": arg, "format": "jpg"}),
                    "release": ("release_grabber", {"framegrabber_name": arg}),
                    "config": ("get_framegrabber_config", {"framegrabber_name": arg}),
                    "add": ("add_phone_camera", {"open_browser": arg != "quiet"}),
                }[op]
                t0 = time.monotonic()
                res = await s.call_tool(tool, args)
                ms = (time.monotonic() - t0) * 1000
                out = []
                for c in res.content:
                    if c.type == "image":
                        n += 1
                        path = HERE / (f"qr_{n}.png" if op == "add" else f"grab_{n}.jpg")
                        path.write_bytes(base64.b64decode(c.data))
                        out.append(f"<image {path.name} {path.stat().st_size}B>")
                    else:
                        out.append(c.text)
                log(f"{line!r} -> error={res.isError} {ms:.0f}ms {' | '.join(out)}")
            await asyncio.sleep(0.3)


asyncio.run(main())
