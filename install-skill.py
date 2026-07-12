#!/usr/bin/env python3
"""Install the Claude Code integrations for this machine: COPY, not symlink
(symlinks need admin/developer mode on Windows and are often blocked on
enterprise machines).

1. Copies skill/ to the user's Claude skills directory, stamping this repo's
   absolute path in; Claude Code can then be launched from ANY directory.
2. Generates .mcp.json (gitignored, machine-specific) pointing at this venv's
   chartwright-mcp with the correct per-OS path, only if the mcp extra is installed.

Usage:  python install-skill.py          (or .venv\\Scripts\\python install-skill.py)
Re-run after moving the repo or pulling skill changes.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
SKILL_SRC = REPO / "skill" / "SKILL.md"
DEST_DIR = Path.home() / ".claude" / "skills" / "superset-dashboard"


def main() -> None:
    if not SKILL_SRC.exists():
        sys.exit(f"skill source not found at {SKILL_SRC}")
    bin_dir = REPO / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_cli = bin_dir / ("chartwright.exe" if os.name == "nt" else "chartwright")
    if not venv_cli.exists():
        print(f"note: {venv_cli} not found yet; create the venv first "
              f"(python -m venv .venv && pip install -e .); installing anyway.")

    content = SKILL_SRC.read_text(encoding="utf-8")
    stamped = content.replace("{{CHARTWRIGHT_HOME}}", str(REPO))

    # is_symlink() FIRST: exists() follows links, so a dangling legacy symlink
    # (repo moved) would dodge both branches and break mkdir below.
    if DEST_DIR.is_symlink():
        DEST_DIR.unlink()
    elif DEST_DIR.exists():
        shutil.rmtree(DEST_DIR)
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    (DEST_DIR / "SKILL.md").write_text(stamped, encoding="utf-8")
    print(f"skill installed: {DEST_DIR / 'SKILL.md'}")
    print(f"project root stamped as: {REPO}")

    # MCP server registration: machine-specific absolute command, only when
    # the mcp extra is actually installed (pip install -e ".[mcp]").
    mcp_cli = bin_dir / ("chartwright-mcp.exe" if os.name == "nt" else "chartwright-mcp")
    mcp_importable = (REPO / ".venv").exists() and _mcp_installed(bin_dir)
    if mcp_cli.exists() and mcp_importable:
        (REPO / ".mcp.json").write_text(json.dumps({
            "mcpServers": {
                "chartwright": {"command": str(mcp_cli), "args": [], "env": {}}
            }
        }, indent=2) + "\n", encoding="utf-8")
        print(f".mcp.json generated -> {mcp_cli}")
    else:
        print("mcp server not registered (install with: pip install -e \".[mcp]\" then re-run)")
    print("re-run this script if you move the repo.")


def _mcp_installed(bin_dir: Path) -> bool:
    py = bin_dir / ("python.exe" if os.name == "nt" else "python")
    if not py.exists():
        return False
    import subprocess

    return subprocess.run([str(py), "-c", "import mcp"], capture_output=True).returncode == 0


if __name__ == "__main__":
    main()
