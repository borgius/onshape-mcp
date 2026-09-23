"""Generate and review an operation registry from an OpenAPI JSON document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from onshape_mcp.api.registry import OperationRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("openapi", type=Path, help="OpenAPI JSON document")
    parser.add_argument("output", type=Path, help="Output registry JSON path")
    parser.add_argument("--include-deprecated", action="store_true")
    parser.add_argument("--include-internal", action="store_true")
    parser.add_argument("--reviewed-only", action="store_true")
    args = parser.parse_args()

    registry = OperationRegistry.from_json_file(
        args.openapi,
        include_deprecated=args.include_deprecated,
        public_only=not args.include_internal,
        reviewed_only=args.reviewed_only,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(registry.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Generated {len(registry)} operations at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
