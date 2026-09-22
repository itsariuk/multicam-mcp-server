"""Field-validation soak (PRD phase 6): hold the MCP server over stdio, as a client would,
and call grab_frame on one phone camera every 10 minutes for 2 hours.

    uv run python .claude/PRPs/plans/assets/soak.py <repo> "<camera name>"

Starts its own server (so nothing else may hold port 8443), prints the join PIN, waits
for the phone to join under <camera name>, then starts the clock. Log: soak.log next to
this file; every grabbed image is saved as soak_NN.jpg.
"""

import asyncio
import base64
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).parent
LOG = HERE / "soak.log"
REPO, NAME = sys.argv[1], sys.argv[2]
INTERVAL_S, DURATION_S = 600, 2 * 3600


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, file=sys.stderr)
    with LOG.open("a") as f:
        f.write(line + "\n")


async def main():
    LOG.write_text("")
    params = StdioServerParameters(
        command="uv",
        args=["run", "--project", REPO, "multicam-mcp-server"],
        env={**os.environ, "ENABLE_FRAMEGRAB_PHONE_CAMERAS": "true"},
    )
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("add_phone_camera", {"open_browser": True})
        if res.isError:
            log("CANNOT START: " + res.content[0].text)
            return
        log("JOIN: " + res.content[0].text)
        while True:
            names = await s.call_tool("list_framegrabbers", {})
            if NAME in [c.text for c in names.content]:  # one text block per name
                break
            await asyncio.sleep(2)
        log(f"'{NAME}' joined; soaking for {DURATION_S // 60} min, one grab every {INTERVAL_S // 60} min")
        t_end = time.monotonic() + DURATION_S
        n = 0
        while time.monotonic() < t_end:
            t0 = time.monotonic()
            res = await s.call_tool("grab_frame", {"framegrabber_name": NAME, "format": "jpg"})
            ms = (time.monotonic() - t0) * 1000
            n += 1
            if res.isError:
                log(f"{n:02d} FAIL {ms:.0f}ms {res.content[0].text}")
            else:
                data = base64.b64decode(res.content[0].data)
                (HERE / f"soak_{n:02d}.jpg").write_bytes(data)
                log(f"{n:02d} ok {ms:.0f}ms {len(data)}B")
            await asyncio.sleep(INTERVAL_S)
        log("DONE")


asyncio.run(main())
