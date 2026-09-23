"""Check the reviewed consolidated MCP surface before release."""

from __future__ import annotations

import asyncio
import json
import sys

from onshape_mcp.server import list_tools
from onshape_mcp.verification import verification_report


async def _run() -> int:
    report = verification_report(await list_tools())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
