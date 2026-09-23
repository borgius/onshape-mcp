# Refactoring Release Notes (Breaking MCP Surface Change)

## Consolidated MCP surface

- Default profile exposes 20 reviewed domain tools; the `advanced` profile adds reviewed OpenAPI query and mutation tools.
- Target references require exactly one workspace, version, or microversion selector.
- Tool results use a stable envelope with summaries, state, diagnostics, warnings, next actions, HTTP status, request IDs, and retryability.
- MCP read/write/destructive annotations are explicit, and operation schemas are available through `onshape://schemas/{operationId}` resources.

## API and safety

- API managers resolve version families through centralized client configuration instead of embedding `/api/vN` paths.
- OpenAPI operation parameters and request bodies are validated against the reviewed registry.
- Gateway writes require reviewed operations and configured scopes; destructive operations require a short-lived, target- and payload-bound plan token.
- Audit records contain request hashes and redacted identity fingerprints, never credentials or source payloads.

## FeatureScript and translations

- FeatureScript source testing supports reviewed compile/regenerate operations, structured diagnostics, and optional geometry inspection.
- FeatureScript documentation search returns bounded excerpts with source and version context.
- Translation status supports bounded polling, terminal failure diagnostics, and download references without fetching binary content automatically.
- Oversized gateway responses are written to a local 0600 resource file and returned as a resource reference.

## Configuration

- `ONSHAPE_MCP_PROFILE=default|advanced`
- `ONSHAPE_SCOPES=scope.one,scope.two`
- `ONSHAPE_API_VERSIONS_JSON='{"translations":"v11"}'`
- `ONSHAPE_OPENAPI_PATH=/path/to/reviewed-openapi.json`
