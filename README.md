# Onshape MCP Server

An [MCP](https://modelcontextprotocol.io/) server for programmatic CAD work in Onshape. The **default profile advertises 20 domain-oriented tools**; `ONSHAPE_MCP_PROFILE=advanced` adds two reviewed OpenAPI gateway tools. Tool results include a structured `ok`/`data`/`diagnostics` envelope.

## Install

Requirements: Python 3.10+, an Onshape account, and Onshape API access and secret keys.

```bash
git clone https://github.com/borgius/onshape-mcp.git
cd onshape-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Set credentials in the environment or a local `.env` file (which is gitignored):

```dotenv
ONSHAPE_ACCESS_KEY=your_access_key
ONSHAPE_SECRET_KEY=your_secret_key
```

Create API keys in the [Onshape Developer Portal](https://dev-portal.onshape.com/). Never commit keys or put them in a public URL.

## Run

**Local MCP clients (stdio):**

```bash
.venv/bin/python -m onshape_mcp.server
```

For example, configure a stdio MCP client with command `/absolute/path/to/onshape-mcp/.venv/bin/python`, args `["-m", "onshape_mcp.server"]`, and working directory `/absolute/path/to/onshape-mcp`. The server loads `.env` from the project root, or you can supply credentials in the client environment.

**Remote MCP clients (Streamable HTTP):**

```bash
# Set MCP_AUTH_TOKEN in .env before exposing the server to a network.
.venv/bin/python -m onshape_mcp.server --http --port 3000
```

The MCP endpoint is `/mcp`. The server binds to `127.0.0.1`; use an HTTPS reverse proxy for remote access. Set `MCP_AUTH_TOKEN` to require an `Authorization: Bearer` header (or a `?token=` query parameter for URL-only connectors; treat such URLs as secrets). **Without a token, the endpoint is open.** `--sse` starts the SSE transport at `/sse` instead. `MCP_PORT` can override the port.

## Tools

These are the **20 tools advertised by the default profile** (`ONSHAPE_MCP_PROFILE=default`). See the [detailed tool reference](docs/TOOLS.md) for inputs, actions, examples, and prerequisites. Input names use camelCase. For tools targeting an element, supply `documentId`, `elementId`, and **exactly one** of `workspaceId`, `versionId`, or `microversionId`. Edits require a workspace.

| Tool | Purpose |
| --- | --- |
| `search_onshape` | Search documents or list workspaces, elements, and Part Studios. |
| `get_onshape_context` | Retrieve a document's workspaces and elements in one normalized response. |
| `inspect_part_studio` | Inspect features, parts, and/or body details. |
| `inspect_assembly` | Inspect definition, features, instance positions, and/or interference. |
| `inspect_geometry` | Inspect body geometry and optionally capture a screenshot. |
| `get_translation` | Read or poll a translation's status and download references. |
| `search_featurescript_docs` | Search indexed FeatureScript documentation excerpts. |
| `test_featurescript` | Evaluate FeatureScript, or compile/regenerate via reviewed operations, with optional geometry inspection. |
| `create_onshape_object` | Create a document, workspace, version, Part Studio, assembly, or Feature Studio; optionally use a reviewed creation operation. |
| `edit_sketch` | Create or update a sketch with multiple rectangles, circles, lines, or arcs and constraints. |
| `edit_part_feature` | Create or update a Part Studio feature from an Onshape `featureData` definition. |
| `edit_variables` | Set multiple variables in a **Variable Studio** (not a Part Studio) by its `elementId`. |
| `edit_assembly_instance` | Add, transform, position, or delete an assembly instance; other actions require reviewed operations. |
| `edit_assembly_mate` | Create, inspect, update, suppress, or delete assembly features/mates. |
| `delete_onshape_object` | Plan or execute an allowlisted destructive operation using a short-lived plan token. |
| `get_feature_schema` | Fetch a reviewed operation's feature-definition schema. |
| `read_featurescript` | Read Feature Studio source using a reviewed operation. |
| `write_featurescript` | Write Feature Studio source using a reviewed operation and optional expected revision. |
| `start_translation` | Start a Part Studio/assembly translation or a reviewed translation operation. |
| `edit_onshape_object` | Run a reviewed non-destructive object-lifecycle operation. |

**Advanced profile only** (`ONSHAPE_MCP_PROFILE=advanced`):

| Tool | Purpose |
| --- | --- |
| `query_onshape_operation` | Run an allowlisted read-only OpenAPI operation by `operationId`. |
| `mutate_onshape_operation` | Run an allowlisted write operation; deletes require a plan token. |

The reviewed-operation tools require a configured registry (`ONSHAPE_OPENAPI_PATH=/path/to/reviewed-openapi.json`); without one, an unknown `operationId` is rejected. For advanced writes, configure the appropriate `ONSHAPE_SCOPES` as well. See the [tool reference](docs/TOOLS.md) for operation requirements. A `variableStudio` value currently appears in the `create_onshape_object` input schema but is **not implemented** by its object-creation service; do not rely on it.

### Example MCP calls

The following are MCP tool names and JSON arguments, **not** Python functions:

```text
search_onshape({"resourceKind":"documents","query":"robot arm","limit":5})
get_onshape_context({"documentId":"<document-id>"})
create_onshape_object({"kind":"document","name":"CAD Test"})
edit_sketch({
  "documentId":"<document-id>", "workspaceId":"<workspace-id>",
  "elementId":"<part-studio-id>", "plane":"Front", "name":"Base",
  "entities":[{"type":"rectangle","corner1":[0,0],"corner2":[2,1]}]
})
```

Use the returned sketch feature ID to construct `featureData` for `edit_part_feature`. For payload examples, see [the sketch builder](onshape_mcp/builders/sketch.py), [the extrude builder](onshape_mcp/builders/extrude.py), and the [tool reference](docs/TOOLS.md).

## Development

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

The public tool schemas live in [`onshape_mcp/tools/consolidated.py`](onshape_mcp/tools/consolidated.py); MCP registration and transports live in [`onshape_mcp/server.py`](onshape_mcp/server.py). Tests and more configuration details are in [`docs/TESTING.md`](docs/TESTING.md) and [`docs/QUICK_START.md`](docs/QUICK_START.md). Use MCP `tools/list` for machine-readable schemas.

Contributions: open a PR against **`develop`**, not `main` (releases only).
