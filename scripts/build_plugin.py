"""Maintainer build: produce a native, self-contained plugin folder and ZIP."""

import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = f"{sys.platform}-{platform.machine().lower()}"
    staging = ROOT / "dist" / target
    plugin = staging / "multicam"
    if plugin.exists():
        shutil.rmtree(plugin)
    shutil.copytree(ROOT / "plugins" / "multicam", plugin)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name",
            "multicam-runtime",
            "--distpath",
            str(plugin / "bin"),
            "--workpath",
            str(ROOT / "build" / target),
            "--specpath",
            str(ROOT / "build"),
            "--add-data",
            f"{ROOT / 'multicam_mcp_phone.html'}:.",
            "--collect-all",
            "framegrab",
            "--collect-all",
            "mcp",
            "--copy-metadata",
            "multicam-mcp-server",
            str(ROOT / "multicam_plugin.py"),
        ],
        check=True,
        cwd=ROOT,
    )
    suffix = ".exe" if sys.platform == "win32" else ""
    config = {
        "mcpServers": {
            "multicam": {
                "command": "${CLAUDE_PLUGIN_ROOT}/bin/multicam-runtime/multicam-runtime"
                + suffix,
                "args": [],
            }
        }
    }
    (plugin / ".mcp.json").write_text(json.dumps(config, indent=2) + "\n")
    shutil.copy2(ROOT / "LICENSE", plugin / "LICENSE")
    shutil.copy2(ROOT / "NOTICE", plugin / "NOTICE")
    archive = shutil.make_archive(
        str(ROOT / "dist" / f"multicam-{target}"), "zip", staging, "multicam"
    )
    print(f"Built {archive}")


if __name__ == "__main__":
    main()
