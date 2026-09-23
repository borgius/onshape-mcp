# MCP tool reference

This guide describes the tools advertised by [`onshape_mcp/tools/consolidated.py`](../onshape_mcp/tools/consolidated.py). The **default** profile exposes 20 tools; set `ONSHAPE_MCP_PROFILE=advanced` before starting the server to expose 2 more. After changing profiles, restart the server and refresh your MCP client's tool list.

## Conventions

- Parameters use **camelCase**. A target for an element has `documentId` and `elementId`, plus **exactly one** of `workspaceId`, `versionId`, or `microversionId`. Most domain implementations require `workspaceId` even though the shared input schema also permits a version or microversion; use a workspace for the calls below. Discovery and operation-ID calls have their own requirements.
- Each domain call normally returns an MCP text item containing JSON with `ok`, `summary`, `target`, `data`, `state`, `warnings`, `diagnostics`, `nextActions`, `httpStatus`, `requestId`, and `retryable`. **Inspect `ok`**: a rejected request can return `ok: false` without an MCP transport error. An upstream exception may instead be an MCP tool error.
- Tool names below are MCP calls, not Python functions. Examples show JSON argument objects; replace placeholder IDs with values returned by discovery calls.
- **Reviewed operations** are identified by `operationId` from an `ONSHAPE_OPENAPI_PATH` registry. The default registry is empty. Without a configured, reviewed operation, operation-ID tools reject unknown IDs. Supply `ONSHAPE_SCOPES` for operations that declare required scopes; setting that variable does not itself grant permissions to an Onshape API key.
- Mutating operations affect the indicated workspace. Use a disposable document for experiments. `readOnlyHint`/`destructiveHint` are MCP annotations, not a substitute for checking arguments.

## Find and inspect

### `search_onshape`

Find documents or resources within a document. `resourceKind` is `documents` (default), `workspaces`, `elements`, or `partstudios`; `limit` defaults to 20 (1–100). For documents, supply optional `query` for name/description search; without it, list documents, optionally using `filterType`. The other resource kinds require both `documentId` **and** `workspaceId` (including `workspaces`). `partstudios` filters the workspace's elements. Returns resource references with names, IDs, target IDs, and metadata.

```json
{"resourceKind":"documents","query":"cabinet","limit":5}
```

### `get_onshape_context`

Requires `documentId`. Returns document metadata, workspaces, and each workspace's elements (`data.document`, `data.workspaces`, `data.workspaceDetails`). Use it to obtain IDs before calling an element tool.

```json
{"documentId":"<document-id>"}
```

### `inspect_part_studio`

Requires a Part Studio target. `sections` can contain `features`, `parts`, and `bodyDetails`; omitting it requests **all three**. Returns the selected sections under `data`. `bodyDetails` contains bodies, faces, edges, and their IDs; large models can yield large responses. Currently requires a workspace target.

```json
{"documentId":"<document-id>","workspaceId":"<workspace-id>","elementId":"<part-studio-id>","sections":["features","parts"]}
```

### `inspect_assembly`

Requires an Assembly target. `sections` defaults to `definition` and `features`. Set `includePositions: true` for an instance position/bounds report and `includeInterference: true` for a bounding-box interference check. These options make additional reads and add `positions` and `interference` to `data`. A bounding-box overlap is not a full geometric collision proof. Currently requires a workspace target.

### `inspect_geometry`

Requires a Part Studio target. Always returns `bodyDetails` and `parts`; `includeScreenshot: true` adds a visual capture, with optional `screenshotOptions` passed to the capture service. This is a potentially large response. Currently requires a workspace target.

### `search_featurescript_docs`

Requires `query`; `limit` defaults to 5 (1–20). Searches a **small indexed collection of excerpts**, not the live Onshape documentation website. Returns bounded matches with `title`, `content` (up to 4,000 characters), `source`, and `version`. An empty match list does not prove a FeatureScript API does not exist.

### `get_feature_schema`

Requires a registered `operationId`. Returns the reviewed operation's HTTP method, path, classification, parameters, request body, responses, scopes, and reviewed status. It does **not** discover arbitrary operation IDs or fetch Onshape's entire API schema. The same schema can be read through the MCP resource template `onshape://schemas/{operationId}`. Without a registry, unknown IDs fail.

## Create and modify CAD

### `create_onshape_object`

Requires `kind` and `name` for the built-in path. Implemented kinds: `document`, `workspace`, `version`, `partStudio`, `assembly`, and `featureStudio`. `workspace`/`version` require `documentId`; `partStudio`/`assembly`/`featureStudio` require `documentId` and `workspaceId`. `description` and `isPublic` apply where supported (notably documents; `isPublic` defaults to `true`). Returns creation metadata in `data` and `state.created`. Creating private documents can fail depending on the Onshape account/plan.

```json
{"kind":"partStudio","name":"Base","documentId":"<document-id>","workspaceId":"<workspace-id>"}
```

**Known mismatch:** the input schema also lists `variableStudio`, but the built-in creation service currently rejects it. Alternatively, passing `operationId` with `body` uses a **reviewed write operation** through the gateway rather than the built-in kind path; a configured registry is required.

### `edit_sketch`

Requires a Part Studio workspace target. `action` defaults to `create`. For creation, `plane` defaults to `Front` (`Front`, `Top`, `Right`); optional `planeId` overrides automatic plane lookup, and `name` defaults to `Sketch`. `entities` accepts objects of these forms (lengths are in **inches**):

| `type` | Required entity fields |
| --- | --- |
| `rectangle` | `corner1: [x,y]`, `corner2: [x,y]` |
| `circle` | `center: [x,y]`, `radius` |
| `line` | `start: [x,y]`, `end: [x,y]` |
| `arc` | `center: [x,y]`, `radius`, `startAngle`, `endAngle` |

Optional `constraints` is an array of Onshape constraint dictionaries, each with `constraintType`; this interface does not invent constraint references for you. The response contains the new sketch feature and its `featureId`. For `action: "update"`, provide **both** `featureId` and a complete Onshape `featureData` definition; the entity-building arguments are not applied to an update.

```json
{"documentId":"<document-id>","workspaceId":"<workspace-id>","elementId":"<part-studio-id>","name":"Base","entities":[{"type":"rectangle","corner1":[0,0],"corner2":[2,1]}]}
```

### `edit_part_feature`

Requires a Part Studio workspace target and Onshape-format `featureData` (a full feature definition, **not** a high-level `depth`/`radius` shortcut). `action` is `create` (default) or `update`; updates additionally require `featureId`. Can submit extrudes, revolves, fillets, etc. **only when you supply a valid Onshape feature definition**. See [`builders/extrude.py`](../onshape_mcp/builders/extrude.py) for an example payload. Returns the API feature response and created/updated state; inspect feature status afterward.

### `edit_variables`

Requires a **Variable Studio** workspace target and `variables: [{"name":"width","expression":"2 in"}, ...]`. Optional `description` may be provided per variable. Processes entries one at a time, preserving unrelated variables. Returns a result per variable and `state.updated`. A Part Studio ID is **not** a Variable Studio ID; that target can return a 404. Variable Studio creation is not currently available through the built-in `create_onshape_object` kind.

### `edit_assembly_instance`

Requires an Assembly workspace target, `action`, and `payload`:

| `action` | Payload / behavior |
| --- | --- |
| `add` | `partStudioElementId`, optional `partId` and `isAssembly` (default `false`). Adds the selected part or subassembly. |
| `transform` | `occurrences` in the Onshape transform request format; relative transformation. |
| `position` | `occurrences` in the Onshape transform request format; absolute transformation. |
| `delete` | `instanceId` in `payload`; deletes that instance. |
| `align`, `fix`, `unfix`, `suppress`, `unsuppress` | No built-in path; require a suitable reviewed `operationId`. |

If `operationId` is supplied, the tool instead sends `payload` through the reviewed mutation gateway (`pathParams`/`queryParams` as needed). Get instance IDs using `inspect_assembly`. Add a reference part before constructing motion mates; see the [assembly workflow guide](../knowledge_base/assembly_workflow_guide.md).

### `edit_assembly_mate`

Requires an Assembly workspace target and `action`: `create` with an Onshape `featureData` definition; `update` with `featureId` and `featureData`; `inspect` with optional `featureId`; or `delete`, `suppress`, `unsuppress` with `featureId`. A mate connector is also an Assembly feature. This tool does **not** accept simple face IDs and automatically build a mate for you: construct the full feature definition (see [`builders/mate.py`](../onshape_mcp/builders/mate.py)). For `suppress`/`unsuppress`, the service submits a feature update; verify the feature state after the call. If `operationId` is supplied, it routes `featureData` through the reviewed mutation gateway instead.

### `edit_onshape_object`

Requires `operationId` for a **reviewed non-destructive** lifecycle write; supply operation-specific `pathParams`, `queryParams`, and optional `body`. This is not an arbitrary URL executor. Read the operation schema before composing the request, and configure the registry and scopes. A delete-classified operation routed here is rejected without the destructive plan parameters; use `delete_onshape_object` instead.

## FeatureScript and exports

### `test_featurescript`

Requires a Part Studio workspace target and **one of**:

- `script`: a FeatureScript lambda/expression to evaluate without editing source. Returns evaluation output and notices. With `inspectGeometry: true`, also reads a bounding box and body details.
- `compileOperationId` plus `pathParams`: run a configured reviewed compile operation with optional `compileBody`. Optionally use `regenerateOperationId`, `regeneratePathParams`, and `regenerateBody` to run a second operation after successful compilation. `inspectGeometry` can inspect the result. This path needs a reviewed operation registry; compilation alone does not write Feature Studio source.

```json
{"documentId":"<document-id>","workspaceId":"<workspace-id>","elementId":"<part-studio-id>","script":"function(context is Context, queries is map) { return 42; }"}
```

### `read_featurescript`

Requires a Feature Studio target, registered read `operationId`, and the operation's `pathParams` (and optional `queryParams`). Reads source via a reviewed OpenAPI operation and returns the upstream data; it does not derive an operation automatically from `elementId`. Without the operation registry, it cannot read source.

### `write_featurescript`

Requires a Feature Studio **workspace** target, registered write `operationId`, its `pathParams`, and operation-specific `body`. Optional `expectedRevision` is inserted in the body for optimistic concurrency where the upstream operation supports it. Check diagnostics in the result; inspect or compile the source afterward. This is a live write, not a local source-file edit.

### `start_translation`

Requires a Part Studio or Assembly workspace target and either:

- `resourceKind: "partstudio"` or `"assembly"`; `formatName` defaults to `STL`, with optional `partId` for a Part Studio export. Returns a translation ID in `state.translationId` where available, plus `nextActions: ["get_translation"]`.
- `operationId` and operation-specific `body` (with optional path/query params) to start a **reviewed** alternative translation via the mutation gateway. `drawing` and `blob` are accepted by the input schema but **not** by the built-in translation path; they require a reviewed operation.

Actual supported output formats depend on the target and Onshape export API. This call starts an asynchronous job; it does not download the binary.

### `get_translation`

Requires `translationId` (from `start_translation` or another translation request). With `poll: true`, waits for a terminal state; `timeoutSeconds` defaults to 30 (1–600) and `intervalSeconds` to 1 (0.1–60). With `poll: false` it makes one status request. `data` contains the upstream status and any available download reference; `state.requestState` summarizes the state. A timeout is retryable; a failed/cancelled translation reports diagnostics. This tool does **not** download the exported file.

## Destructive and advanced operations

### `delete_onshape_object`

Requires a registered **reviewed delete-classified** `operationId`, an allowlisted `resourceKind`, `target` containing `documentId` and the appropriate resource ID, and a nonempty `identity`. Two-step workflow:

1. Call with `planOnly: true`, optional `ttlSeconds` (1–600; default 120), and the exact `requestBody` to be used. This produces a short-lived token; it **does not delete** anything.
2. Call again with `planToken` and the **same** `operationId`, `resourceKind`, `target`, `requestBody`, and `identity`, plus any operation-specific `pathParams`/`queryParams`. The plan is single-use and bound to those inputs. Verify the target before executing.

Allowed resource kinds include `document`, `workspace`, `version`, `microversion`, `element`, `partstudio`, `assembly`, `featurestudio`, `variablestudio`, `feature`, `instance`, and `mate`. The `target` must carry the corresponding ID (`elementId`, `featureId`, `instanceId`, etc.). Deleting an Assembly feature via `edit_assembly_mate` or an instance via `edit_assembly_instance` uses a **different direct path** and does not go through this plan gate.

### `query_onshape_operation` — advanced profile only

Requires a registered **read-classified** `operationId`; optional `pathParams` and `queryParams` must match the reviewed operation's schema. Enforces declared scopes and response-size limits. A write/delete operation is rejected. Oversized responses are written to a local resource file and returned as a reference (which might not be directly accessible from a remote MCP client).

### `mutate_onshape_operation` — advanced profile only

Requires a registered **reviewed write** `operationId`; supply operation-specific `pathParams`, `queryParams`, and `body`. Enforces declared scopes and schema validation. A read-classified operation is rejected. For a delete-classified operation, also supply `resourceKind`, `target`, `planToken`, and `identity` from a prior `delete_onshape_object` plan. Treat this as a privileged tool; it is not an unrestricted REST proxy.

## Typical workflow

1. `search_onshape` or `get_onshape_context` → obtain document/workspace/element IDs.
2. `edit_sketch` → obtain sketch `featureId`; `edit_part_feature` → create geometry; `inspect_part_studio`/`inspect_geometry` → verify feature state and body IDs.
3. `edit_assembly_instance` → add parts; `inspect_assembly` → check positions/features; then build mates with `edit_assembly_mate` if needed.
4. `start_translation` → `get_translation` (optionally with polling) → retrieve the resulting file through an appropriate authenticated download workflow.

For exact machine-readable input schemas, request MCP `tools/list`; for the reviewed registry operation schemas use `get_feature_schema` or `onshape://schemas/{operationId}`.
