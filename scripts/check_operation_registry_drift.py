"""Compare a reviewed registry with a current OpenAPI document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from onshape_mcp.api.registry import OperationRegistry, registry_diff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reviewed", type=Path, help="Generated reviewed registry JSON")
    parser.add_argument("current_openapi", type=Path, help="Current OpenAPI JSON document")
    args = parser.parse_args()

    reviewed = OperationRegistry.from_json_file(args.reviewed, reviewed_only=True)
    current = OperationRegistry.from_json_file(args.current_openapi)
    report = registry_diff(reviewed, current)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if any(report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
