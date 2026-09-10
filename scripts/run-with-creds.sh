#!/usr/bin/env bash
# Launch the Onshape MCP server with credentials decrypted from a gpg file at
# start-up, for editor/IDE integrations where environment variables are awkward
# to set. Expects the decrypted plaintext to look like:
#
#   ONSHAPE_ACCESS_KEY=xxxxxxxx
#   ONSHAPE_SECRET_KEY=yyyyyyyy
#
# Configuration (all optional):
#   ONSHAPE_CREDS_GPG  path to the encrypted file (default: ~/.credentials/onshape.env.asc)
#   ONSHAPE_MCP_PYTHON interpreter to use (default: $VIRTUAL_ENV/bin/python if a venv is
#                      active, else the .venv next to this script, else python3 on PATH)
set -euo pipefail

CREDS_FILE="${ONSHAPE_CREDS_GPG:-$HOME/.credentials/onshape.env.asc}"
if [[ ! -f "$CREDS_FILE" ]]; then
  echo "onshape-mcp: credentials file not found: $CREDS_FILE" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${ONSHAPE_MCP_PYTHON:-}" ]]; then
  PYTHON="$ONSHAPE_MCP_PYTHON"
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
  PYTHON="$VIRTUAL_ENV/bin/python"
elif [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
  PYTHON="$REPO_DIR/.venv/bin/python"
else
  PYTHON="python3"
fi

set -a
# shellcheck disable=SC1090
eval "$(gpg --quiet --batch --decrypt "$CREDS_FILE")"
set +a

cd "$REPO_DIR"
exec "$PYTHON" -m onshape_mcp.server "$@"
