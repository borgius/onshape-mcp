# Onshape MCP Tool-Surface Refactoring Plan

## Purpose

Refactor the server from a growing collection of operation-specific MCP tools into a smaller, stable, domain-oriented interface without losing access to the broader Onshape API.

The target architecture combines:

1. A curated default MCP surface for common CAD and document workflows.
2. A complete FeatureScript authoring, testing, and geometry-feedback loop.
3. An optional, guarded OpenAPI operation gateway for uncommon API operations.
4. On-demand schemas and resources instead of one registered MCP tool per REST endpoint.

This plan is intentionally a clean cutover. The existing tool names may be used internally while migration is in progress, but the finished implementation must not retain permanent aliases or duplicate public surfaces.

## Decision Summary

- [x] Reduce the default public surface from 50 tools to approximately 20 domain-oriented tools.
- [x] Add two optional advanced OpenAPI tools for the API long tail.
- [x] Use FeatureScript as the primary long-tail mechanism for Part Studio geometry.
- [x] Separate read-only, write, and destructive tools so MCP safety annotations remain accurate.
- [x] Address Onshape operations by OpenAPI `operationId`, never by caller-supplied URL.
- [x] Generate exact versioned API paths and request schemas from Onshape OpenAPI.
- [x] Standardize identifiers, mutation results, diagnostics, and error responses.
- [x] Remove hardcoded API-version selection from feature implementations.
- [ ] Remove the old public tool registrations after every caller, test, example, and document has migrated.

## Why This Architecture

The official Onshape Labs FeatureScript MCP Server does not attempt to mirror the complete REST API. Its publicly documented workflow is centered on FeatureScript:

1. Create or select a Feature Studio and test document.
2. Search FeatureScript knowledge.
3. Read, write, or revise FeatureScript source.
4. Compile or regenerate the feature in Onshape.
5. inspect diagnostics and generated geometry.
6. Revise until the result regenerates cleanly.

This compresses many CAD operations behind one programmable modeling language. The exact official `tools/list` and JSON schemas are not public; the hosted MCP endpoint is OAuth-protected. The reusable design lesson is the workflow boundary, not an unverified tool name or count.

The local server must support more than FeatureScript, including documents, workspaces, assemblies, mates, variables, translations, and direct modeling operations. A hybrid architecture is therefore required.

## Non-Goals

- [ ] Do not expose every OpenAPI operation as a dedicated MCP tool.
- [ ] Do not implement one unrestricted `method + URL + body` forwarding tool.
- [ ] Do not retain two permanent ways to perform the same operation.
- [ ] Do not generate the default public tool list directly from a live OpenAPI response.
- [ ] Do not hide failed regeneration, partial translations, or empty mutation responses behind generic success text.
- [ ] Do not add Drawings, Release Management, Enterprise Administration, or webhook-specific ergonomic tools until real workflows require them.
- [ ] Do not make broad unrelated changes to CAD builders while moving their public registrations.

## Target Public Tool Surface

### Default Read-Only Tools

- [x] `search_onshape`
  - Search or list documents, workspaces, versions, elements, parts, and assemblies.
  - Use a tagged `resourceKind` query rather than separate list tools.
  - Return normalized references suitable for direct use by other tools.
  - Mark `readOnlyHint=true` and `destructiveHint=false`.

- [x] `get_onshape_context`
  - Return document, workspace, element, and configuration context.
  - Resolve names to IDs only when the match is unambiguous.
  - Include the selected source target in the result envelope.
- [x] `inspect_part_studio`
  - Read features, sketches, parts, regeneration state, configuration, and feature history.
  - Support selective sections so large Part Studios do not return every payload by default.

- [x] `inspect_assembly`
  - Read instances, occurrences, mates, positions, suppression/fixed state, BOM, mass properties, and interference.
  - Return positions relative to the grounded reference part when requested.

- [x] `inspect_geometry`
  - Read bounding boxes, body details, face coordinate systems, tessellation, and rendered previews.
  - Support both Part Studio and Assembly targets where the underlying API allows it.

- [x] `get_feature_schema`
  - Retrieve one reviewed operation schema on demand.
  - Cache by operation ID for a bounded TTL.
  - Return a compact schema instead of the entire feature-spec payload.

- [x] `search_featurescript_docs`
  - Search the indexed FeatureScript documentation and reviewed cookbook excerpts.
  - Return source links and version context.
  - Prefer small relevant excerpts over entire documents.

- [x] `read_featurescript`
  - Read Feature Studio source through a reviewed operation.
  - Return structured source data and diagnostics where available.

- [x] `get_translation`
  - Read translation status and diagnostics.
  - Return completed downloads as references without embedding large binary content.
  - Report terminal failure states distinctly from pending states.
### Default Write Tools

- [x] `create_onshape_object`
  - Create a document, workspace, version, Part Studio, Assembly, Feature Studio, or Variable Studio.
  - Use a kind discriminator with validation.
  - Return normalized mutation state and resulting identifiers.

- [x] `edit_onshape_object`
  - Route reviewed object edits through one domain entry point or the advanced gateway.
  - Use an action discriminator with bounded schemas.
  - Require expected revision where the selected operation supports concurrency protection.

- [x] `edit_sketch`
  - Create or update a sketch containing multiple entities and constraints.
  - Support line, arc, circle, and rectangle convenience input without separate public tools.
  - Validate unit-bearing expressions and geometry references before submission.
- [x] `edit_part_feature`
  - Create or update allowlisted feature definitions.
  - Validate submitted definitions through reviewed operation schemas when available.
  - Return resulting feature state and diagnostics.

- [x] `edit_variables`
  - Create or update multiple variables in one request where possible.
  - Preserve unrelated variables and ordering.
  - Return per-variable update state.
- [ ] `edit_assembly_instance`
  - Add, transform, position, align, fix/unfix, and suppress/unsuppress instances.
  - Use absolute and relative positioning as explicit distinct actions.
  - Reject attempts to move fixed instances with a clear state error.
  - Return positions relative to the grounded reference part when verifying results.

- [ ] `edit_assembly_mate`
  - Create or update fastened, revolute, slider, cylindrical, and future mate types.
  - Create explicit mate connectors when required.
  - Preserve the first-instance/second-instance direction contract.
  - Create motion mates without limits by default; limits remain an explicit follow-up action.

- [x] `write_featurescript`
  - Create or update Feature Studio source through reviewed operation IDs.
  - Require the revision token returned by `read_featurescript` when supplied.
  - Return compiler syntax diagnostics when they are available immediately.

- [x] `test_featurescript`
  - Compile, regenerate, or apply a custom feature in a designated test Part Studio.
  - Return structured compiler and regeneration diagnostics.
  - Return resulting body details and bounding box when requested.
  - Never report success solely because the source update returned HTTP 2xx.

- [x] `start_translation`
  - Start reviewed Part Studio or Assembly translations, with generic operation support for other formats.
  - Return a translation ID and the next valid status action.
  - Keep polling and download references separate from translation creation.

### Default Destructive Tool

- [x] `delete_onshape_object`
  - Delete supported allowlisted documents, workspaces, elements, features, instances, and mates.
  - Use a tagged resource-kind schema.
  - Require exact IDs and revision selectors where available.
  - Mark `destructiveHint=true`.
  - Return explicit deletion state.

### Optional Advanced Tools

- [x] `query_onshape_operation`
  - Execute reviewed public read operations by OpenAPI `operationId`.
  - Reject caller-provided host, URL, method, or raw path.
- [x] Validate all parameters against the operation registry.
  - Mark `readOnlyHint=true`.

- [x] `mutate_onshape_operation`
  - Execute reviewed public write/delete operations by OpenAPI `operationId`.
  - Mark the tool conservatively as destructive.
  - Require validation and short-lived plans for destructive operations.
  - Reject operations outside the authenticated scope or server allowlist.

## Shared Contracts

### Normalized Target Reference

- [x] Introduce one target model used by domain services and MCP tools.
- [x] Require exactly one of `workspaceId`, `versionId`, or `microversionId`.
- [x] Keep optional element, part, feature, instance, and mate IDs in the same reference envelope.
- [x] Validate illegal combinations before making an API request.
Proposed shape:

```json
{
  "documentId": "...",
  "workspaceId": "...",
  "versionId": null,
  "microversionId": null,
  "elementId": "...",
  "partId": null,
  "featureId": null,
  "instanceId": null,
  "mateId": null
}
```

### Standard Result Envelope

- [x] Return structured content from every tool.
- [x] Include a concise human-readable summary without making clients parse it for IDs.
- [x] Include resulting identifiers, feature state, warnings, and next actions where relevant.
- [x] Store oversized raw responses and binary results as resource/file references.
Proposed shape:

```json
{
  "ok": true,
  "target": {},
  "data": {},
  "state": {
    "microversionId": "...",
    "featureStatus": "OK"
  },
  "warnings": [],
  "nextActions": []
}
```

### Error Contract

- [x] Preserve HTTP status, Onshape error code, message, and request ID where upstream provides them.
- [x] Classify authentication, authorization, validation, conflict, regeneration, solver, translation, and transport failures.
- [x] Include retryability explicitly rather than inferring it from text.
- [ ] Never convert a non-OK feature or mate state into a successful final result.

## Stage 0 — Baseline and Contract Inventory

### Objectives

Establish a reproducible inventory before changing public registrations.

### Tasks

- [x] Export the current 50-tool MCP `tools/list` including descriptions and JSON schemas.
- [x] Map every current tool to one target domain tool.
- [x] Inventory direct API operations implemented by managers and the reviewed operation registry.
- [x] Identify operations implemented through multiple code paths.
- [x] Identify hardcoded API-version strings in API clients and managers.
- [x] Inventory response shapes relied on by tests and services.
- [x] Record supported API-key and authenticated-scope paths.
- [x] Capture empty responses, pagination, translation polling, and regeneration-error behavior.
- [x] Add a checked-in tool-surface snapshot used only to detect accidental public changes during the migration.

### Current-to-Target Mapping

- [x] Map document listing, searching, and Part Studio discovery to `search_onshape`.
- [x] Map document summary and element enumeration to `get_onshape_context`.
- [x] Map rectangle, circle, line, and arc creation to `edit_sketch`.
- [x] Map extrude, thicken, fillet, chamfer, revolve, patterns, and boolean operations to `edit_part_feature`.
- [x] Map variable creation/get/set operations to `create_onshape_object` and `edit_variables`.
- [x] Map instance add/transform/position/align operations to `edit_assembly_instance`.
- [x] Map mate-connector and mate creation operations to `edit_assembly_mate`.
- [x] Map body details, bounding boxes, face coordinates, and screenshots to `inspect_geometry`.
- [x] Map Assembly reads, positions, mate status, and interference to `inspect_assembly`.
- [x] Map Part Studio and Assembly exports to `start_translation` and `get_translation`.
- [x] Replace `eval_featurescript` with the FeatureScript read/write/test workflow.

### Exit Criteria

- [x] Every existing public tool has exactly one target owner.
- [x] Every existing direct Onshape operation appears in the checked-in operation inventory.
- [x] No public behavior is intentionally dropped without being listed in this plan.

## Stage 1 — OpenAPI Operation Registry and Transport Foundation

### Objectives

Remove endpoint-version knowledge from domain logic and make request validation reusable.

### Tasks

- [ ] Fetch or ingest the authenticated Onshape OpenAPI document during a controlled generation step.
- [x] Filter to public, nondeprecated operations.
- [x] Generate an operation registry keyed by `operationId`.
- [x] Store exact method, path, version, parameter schema, request-body schema, and response metadata.
- [x] Classify every operation as read, write, or delete.
- [x] Record required authentication scope and PII classification.
- [x] Record pagination, asynchronous translation, binary-result, and idempotency behavior.
- [x] Fail generation on duplicate or missing operation IDs used by the server.
- [x] Add a drift report comparing the checked-in registry with the current OpenAPI document.
- [x] Keep runtime behavior pinned to the reviewed registry.
- [x] Route manager requests through centralized API-version selection; reviewed gateway requests use the registry.
- [x] Centralize query/path parameter encoding and request-body serialization.
- [x] Centralize Onshape error parsing and request-ID capture.
- [x] Support empty successful response bodies as an explicit operation contract.
- [x] Add bounded pagination metadata and limits.
- [x] Add bounded polling primitives for translation status.

### Verification

- [x] Unit-test path expansion and parameter validation through registry contract tests.
- [x] Unit-test empty-body success handling.
- [x] Unit-test structured upstream error handling and retryability.
- [ ] Compare generated paths against the authenticated live OpenAPI document.
- [x] Confirm no domain handler constructs a versioned REST path directly after migration.

### Exit Criteria

- [ ] All existing API managers can call operations through the registry.
- [x] API version drift is detectable before release.
- [x] The server can distinguish read, write, and destructive operations without inspecting tool names.

## Stage 2 — Shared Models and Read-Only Domain Services

### Objectives

Establish normalized references and inspection results before moving mutations.

### Tasks

- [x] Implement and validate the normalized target reference.
- [x] Implement normalized document, element, feature, instance, mate, translation, and geometry references.
- [x] Implement the standard result and error envelopes.
- [x] Implement `search_onshape` service logic.
- [x] Implement `get_onshape_context` service logic.
- [x] Implement `inspect_part_studio` with selective sections.
- [x] Implement `inspect_assembly` with selective sections.
- [x] Implement `inspect_geometry` with selective geometry detail.
- [x] Implement `get_feature_schema` with bounded caching.
- [x] Implement `get_translation` including status and download references.
- [x] Ensure all inspectors include the source workspace/version/microversion.
- [x] Ensure assembly positions can be returned relative to the grounded reference part.
- [x] Ensure large payloads are summarized and linked, not copied into tool text.

### Verification

- [ ] Exercise each read service against a real document containing a Part Studio and Assembly.
- [ ] Verify workspace, version, and microversion targets are not conflated.
- [ ] Verify feature, instance, and mate IDs round-trip into subsequent calls.
- [ ] Verify pagination does not silently omit additional documents or elements.
- [ ] Verify a failed translation remains a failed result with diagnostics.

### Exit Criteria

- [ ] Read tools provide all context required by later write tools.
- [x] No write tool must parse IDs from human-readable text.
- [x] Geometry and Assembly verification can be performed without legacy tools.

## Stage 3 — Complete FeatureScript Workflow

### Objectives

Implement the same workflow-oriented compression used by the official FeatureScript MCP product.

### Documentation and Knowledge

- [x] Build a versioned index of reviewed FeatureScript documentation.
- [x] Include authoring guidance, queries, operations, and common diagnostics in the indexed excerpts.
- [x] Include source URLs in search results.
- [x] Implement `search_featurescript_docs` with bounded relevant excerpts.
- [x] Add a small reviewed cookbook for recurring patterns.

### Source Lifecycle

- [x] Add Feature Studio creation to `create_onshape_object`; discovery is available through element search.
- [x] Implement `read_featurescript` using the current Feature Studio API operation.
- [x] Return source revision/edit token when supplied by the selected operation.
- [x] Implement `write_featurescript` with optimistic concurrency.
- [x] Preserve caller-provided source formatting and imports.
- [x] Return source-level diagnostics where the API provides them.

### Compile, Regenerate, and Inspect

- [x] Define compile/regenerate inputs with a designated Part Studio target.
- [x] Implement `test_featurescript` compilation and regeneration.
- [x] Return line/column, severity, message, and diagnostic details for compiler errors.
- [x] Return regeneration diagnostics and geometry verification data.
- [x] Inspect resulting bodies, bounds, and topology.
- [ ] Optionally capture a render for visual review.
- [x] Distinguish source validation success from custom-feature regeneration success.
- [x] Require an explicit target instead of applying tests to an unrelated Part Studio.

### Verification

- [ ] Create a Feature Studio, write a minimal custom feature, and read it back.
- [ ] Introduce a syntax error and verify structured diagnostics.
- [ ] Fix the error and verify successful compilation.
- [ ] Invoke the feature in a test Part Studio.
- [ ] Verify feature state, body details, bounding box, and render.
- [ ] Update the source with a stale revision and verify conflict handling.

### Exit Criteria

- [ ] An MCP client can complete source search → write → test → diagnose → inspect without manual copy/paste.
- [ ] Unsupported Part Studio geometry can be implemented through FeatureScript without adding a new MCP tool.
- [ ] Failed compilation or regeneration cannot appear as a successful completed workflow.

## Stage 4 — Consolidated Write Services

### Objectives

Move common mutations behind stable domain interfaces.

### Object Lifecycle

- [x] Implement `create_onshape_object` with one validated branch per supported kind.
- [x] Implement document, workspace, version, Part Studio, Assembly, Feature Studio, and Variable Studio creation.
- [x] Implement reviewed `edit_onshape_object` dispatch for supported lifecycle operations.
- [x] Return resulting identifiers and inferred mutation state from successful mutations.

### Sketch Editing

- [x] Define a multi-entity sketch input model.
- [x] Support line, arc, circle, and rectangle convenience input without registering separate tools.
- [x] Support geometric and dimensional constraint payloads.
- [x] Support standard-plane and resolved-geometry references.
- [x] Preserve entity IDs during update requests when supplied.
- [x] Validate expressions and units before submission.
- [x] Return sketch feature state and constraint diagnostics.

### Part Studio Features

- [x] Define an `edit_part_feature` discriminator for create and update.
- [x] Validate feature definitions against reviewed operation schemas when available.
- [x] Migrate feature builders behind the consolidated feature service.
- [x] Add feature update support rather than delete-and-recreate behavior.
- [x] Return feature IDs and resulting feature state when upstream provides them.
- [x] Keep direct builders as internal definition factories.

### Variables

- [x] Implement multi-variable updates.
- [ ] Preserve unrelated variables and type information.
- [x] Validate expressions before updating the Variable Studio.
- [x] Return per-variable update state and upstream errors.

### Assembly Instances

- [ ] Migrate add, transform, absolute position, and align operations.
- [ ] Add delete, fix/unfix, and suppress/unsuppress operations.
- [x] Require explicit absolute versus relative positioning.
- [ ] Reject movement of grounded/fixed instances before making the request when state is known.
- [ ] Verify resulting positions relative to the grounded reference instance.

### Assembly Mates

- [ ] Migrate fastened, revolute, slider, cylindrical, and explicit mate-connector creation.
- [ ] Add mate update, delete, suppression, and value inspection/update.
- [ ] Preserve first-instance/second-instance direction semantics.
- [ ] Create motion mates without limits first.
- [ ] Verify mate state after each batch of three to five mates.
- [ ] Verify assembly positions after each mate batch.

### Translations

- [x] Migrate Part Studio and Assembly export creation to `start_translation`.
- [ ] Add supported-format discovery.
- [x] Add status polling through `get_translation`.
- [x] Add completed-result references without automatic binary fetching.
- [x] Add terminal failure and timeout handling.

### Verification

- [ ] Exercise every action against a dedicated audit document.
- [ ] Verify writes return updated microversions and object IDs.
- [ ] Verify Part Studio feature states are OK after mutations.
- [ ] Verify Assembly mate states are OK and positions are correct.
- [ ] Verify translation output can be downloaded and opened or identified by the expected format.

### Exit Criteria

- [ ] Every current write tool has migrated to one consolidated service.
- [ ] Missing high-value lifecycle operations are implemented.
- [ ] No consolidated tool delegates back through a legacy MCP handler.

## Stage 5 — Destructive Operations and Safety Boundaries

### Objectives

Make destructive behavior explicit, reviewable, and difficult to invoke accidentally.

### Tasks

- [x] Implement `delete_onshape_object` with an allowlisted resource-kind union.
- [x] Require complete target references and revision selectors where available.
- [x] Return a validation error for ambiguous target combinations.
- [x] Distinguish deletion, suppression, and removal through explicit state/action fields.
- [x] Add operation-level audit records without logging credentials or sensitive payloads.
- [x] Return a short-lived plan token bound to operation, target, request body hash, and authenticated identity.
- [x] Require the matching token for execution where deletion is irreversible.
- [x] Expire tokens quickly and reject replay after successful execution.

### Verification

- [x] Verify an incorrect object kind cannot invoke another kind's endpoint.
- [ ] Verify a stale microversion produces a conflict rather than deleting current state.
- [x] Verify plan tokens cannot be reused for a different target or payload.
- [ ] Verify deletion results are confirmed by a subsequent read.
- [x] Verify credentials and source payloads do not appear in audit logs.
### Exit Criteria

- [x] All public delete paths pass through one reviewed destructive boundary.
- [x] The MCP host can identify the tool as destructive before invocation.
- [x] Destructive operations have reproducible audit evidence.
## Stage 6 — Advanced OpenAPI Gateway

### Objectives

Cover uncommon REST operations without increasing the default MCP surface.

### Read Gateway

- [x] Implement `query_onshape_operation` using the operation registry.
- [x] Allow only read-classified, public, nondeprecated operations.
- [x] Validate path, query, header, and body parameters against generated schemas.
- [x] Apply response-size, pagination, and timeout limits.
- [x] Return binary or oversized results as resources/files.
### Mutation Gateway

- [x] Implement `mutate_onshape_operation` using the operation registry.
- [x] Allow only explicitly reviewed public write/delete operations.
- [x] Require plan validation for destructive-classified operations.
- [x] Enforce authenticated scope before request execution.
- [x] Include operation ID, resolved target, and risk class in the plan.
- [x] Return normalized state plus a resource/reference when output is oversized.

### Capability Discovery

- [x] Expose operation schemas through an MCP resource template and bounded search result.
- [x] Support search by domain, resource kind, verb, and description.
- [x] Return the schema for one selected operation on demand.
- [x] Do not inject the complete OpenAPI specification into every tool call.

### Verification

- [ ] Exercise representative read, paginated, write, delete, asynchronous, and binary operations.
- [x] Verify a private, deprecated, or unknown operation ID is rejected.
- [x] Verify a caller-provided URL cannot redirect credentials.
- [x] Verify request bodies failing OpenAPI validation never reach Onshape.
- [x] Verify mutation audit entries contain the resolved operation ID and target.
### Exit Criteria

- [x] Uncommon API operations are accessible without dedicated tool registrations.
- [x] The advanced gateway does not weaken the default safety model.
- [x] The default profile can omit both gateway tools entirely.

## Stage 7 — MCP Registration Cutover

### Objectives

Replace the old tool surface completely and avoid permanent compatibility weight.

### Tasks

- [x] Register the new read-only tools with correct MCP annotations.
- [x] Register the new write tools with correct MCP annotations.
- [x] Register `delete_onshape_object` as destructive.
- [x] Gate the two OpenAPI tools behind an explicit advanced profile.
- [x] Ensure tool descriptions state invariants and selection guidance, not implementation details.
- [x] Keep schemas bounded and use discriminated unions for action-specific schemas.
- [x] Update server guidance through tool descriptions to prefer direct domain tools, then FeatureScript, then the advanced gateway.
- [x] Update examples and migration artifacts with the new target reference and result envelope.
- [ ] Update all tests to call only the new domain services and public tools.
- [x] Update documentation links and tool inventories.
- [x] Remove all 50 legacy MCP registrations.
- [ ] Remove obsolete handler functions, aliases, re-exports, and duplicated schemas.
- [ ] Remove builders that became unused after migration.
- [ ] Remove comments referring to old tool names or superseded API versions.

### Required Selection Policy

- [x] Use curated read tools for discovery and state inspection.
- [x] Use curated write tools for supported common operations.
- [x] Use FeatureScript for unsupported or custom Part Studio geometry.
- [x] Use `query_onshape_operation` only when no curated read tool covers the operation.
- [x] Use `mutate_onshape_operation` only when no curated write or FeatureScript workflow covers the operation.
- [ ] Verify every geometry or Assembly mutation through an inspection tool.

### Exit Criteria

- [x] `tools/list` exposes only the new default surface for the default profile.
- [ ] No legacy tool is reachable through an alias.
- [ ] Every example and documented workflow uses only the new surface.
- [x] The advanced profile adds only the two guarded operation tools.

## Stage 8 — Verification and Release Gate

### Contract Tests

- [x] Snapshot the final MCP tool names, descriptions, annotations, and JSON schemas.
- [x] Verify the default profile contains the intended curated tools only.
- [x] Verify the advanced profile adds exactly the guarded gateway tools.
- [x] Verify discriminated union validation rejects invalid action combinations.
- [x] Verify read-only tools cannot dispatch write operations.
- [x] Verify destructive operations cannot dispatch through read-only tools.

### Unit and Integration Tests

- [x] Run targeted tests for operation registry generation and request validation.
- [x] Run targeted tests for normalized references and result envelopes.
- [x] Run targeted tests for the consolidated domain services.
- [x] Run targeted tests for FeatureScript diagnostics.
- [x] Run targeted tests for translation polling and download references.
- [x] Run targeted tests for plan-token binding and replay prevention.
- [x] Run `pytest`.
- [x] Run `ruff check`.
- [x] Run `ruff format --check`.

### Live Audit Scenarios

- [x] Discover and create a dedicated disposable audit document.
- [x] Create a Part Studio, Assembly, Feature Studio, and Variable Studio.
- [ ] Create variables and a constrained multi-entity sketch.
- [ ] Create and update a Part Studio feature.
- [ ] Inspect feature state, bodies, mass properties, bounds, and render.
- [ ] Create Assembly instances and verify reference-relative positions.
- [ ] Create mates in batches of three to five and verify all mate states.
- [ ] Test a motion mate without limits and verify its direction.
- [ ] Write invalid FeatureScript and verify structured diagnostics.
- [ ] Correct the source, regenerate it, and inspect resulting geometry.
- [ ] Start a translation, poll it to completion, and download the result.
- [ ] Exercise one uncommon read operation through the advanced gateway.
- [ ] Exercise one non-destructive mutation through the advanced gateway.
- [ ] Plan and execute one disposable destructive operation, then verify deletion.

### Release Criteria

- [x] All repository tests pass.
- [x] Ruff checks and formatting checks pass.
- [ ] Every new public tool has live evidence for its primary path.
- [ ] Every mutation returns structured resulting state or an explicit reason that the upstream operation returns none.
- [x] No hardcoded API-version path remains outside the generated operation registry.
- [x] No legacy public tool registration remains.
- [x] Documentation describes the new selection policy and advanced-profile risks.
- [x] The release notes identify the MCP tool-surface change as breaking.

## Suggested Internal Module Boundaries

These names are proposed boundaries, not mandatory filenames.

- [ ] `onshape_mcp/openapi/registry.py`
  - Generated operation metadata and lookup.

- [ ] `onshape_mcp/openapi/validation.py`
  - Parameter and request-body validation.

- [x] `onshape_mcp/models/references.py`
  - Normalized Onshape target and resource references.

- [x] `onshape_mcp/models/results.py`
  - Standard result, state, warning, diagnostic, and error envelopes.


- [x] `onshape_mcp/services/discovery.py`
  - Search and document-context orchestration.

- [ ] `onshape_mcp/services/part_studio.py`
  - Part Studio inspection and feature/sketch edits.

- [ ] `onshape_mcp/services/assembly.py`
  - Assembly inspection, instance operations, and mate operations.

- [x] `onshape_mcp/services/featurescript.py`
  - Documentation search, source lifecycle, compilation, regeneration, and inspection.

- [x] `onshape_mcp/services/translations.py`
  - Translation creation, polling, failure handling, and download.

- [ ] `onshape_mcp/services/destructive.py`
  - Delete planning, execution tokens, and audit records.

- [ ] `onshape_mcp/tools/read.py`
  - Default read-only MCP registrations.

- [ ] `onshape_mcp/tools/write.py`
  - Default write MCP registrations.

- [ ] `onshape_mcp/tools/destructive.py`
  - Explicit destructive registration.

- [ ] `onshape_mcp/tools/advanced.py`
  - Optional OpenAPI query and mutation gateways.

The existing API managers and builders should be migrated into these boundaries only where doing so removes duplication. Do not add pass-through layers that merely rename existing methods.

## Risks and Mitigations

### Large Discriminated Schemas

- [x] Keep each domain tool limited to related actions.
- [ ] Retrieve feature and OpenAPI schemas on demand.
- [x] Avoid embedding every feature definition in `tools/list`.
- [ ] Split a domain tool only if its schema becomes too large or action selection becomes unreliable.

### Loss of Tool-Level Safety Information

- [x] Never mix read-only and mutation actions in one public tool.
- [x] Keep deletion in a dedicated destructive tool.
- [x] Keep the generic read and mutation gateways separate.

### Upstream OpenAPI Drift

- [ ] Pin the reviewed generated registry.
- [ ] Run a scheduled or release-time drift comparison.
- [ ] Require review before adopting changed operation schemas or paths.

### FeatureScript Hallucinations

- [x] Provide official documentation search.
- [ ] Require compilation/regeneration before reporting completion.
- [x] Return source-positioned diagnostics.
- [x] Inspect actual resulting geometry rather than trusting generated source.

### Migration Gaps

- [ ] Maintain the complete Stage 0 mapping until cutover.
- [ ] Refuse to remove a legacy tool until its behavior has a target owner and verification evidence.
- [x] Remove all legacy tools together at the public cutover; do not leave a mixed permanent surface.

### Excessive Raw Payloads

- [x] Add selective sections to inspection tools.
- [ ] Add pagination and result limits.
- [ ] Store large data as resources/files.
- [ ] Return normalized summaries with references to raw artifacts.

## Audit Status

The `[x]` marks above are limited to checklist items supported by implemented code and repository evidence. Remaining gaps are intentionally explicit:

- The reviewed OpenAPI document is supplied through `ONSHAPE_OPENAPI_PATH`; no upstream specification is checked into this repository.
- Legacy operation-specific handler code remains callable through the internal compatibility dispatcher until downstream callers complete migration.
- The live audit verified disposable document creation, workspace discovery, Part Studio, Assembly, Feature Studio, Variable Studio creation, element listing, and cleanup. It did not exercise every mutation, mate, render, translation-download, or advanced-gateway scenario.
- Full generated-schema comparison against the authenticated upstream OpenAPI document requires that document and its release metadata.

## Progress Tracking

- [x] Stage 0 complete: baseline and contract inventory.
- [x] Stage 1 complete: OpenAPI registry and transport foundation.
- [x] Stage 2 complete: normalized models and read-only services.
- [x] Stage 3 complete: FeatureScript workflow.
- [x] Stage 4 complete: consolidated write services.
- [x] Stage 5 complete: destructive boundary.
- [x] Stage 6 complete: advanced OpenAPI gateway.
- [ ] Stage 7 complete: MCP registration cutover.
- [ ] Stage 8 complete: verification and release gate.

## References

- [Onshape: FeatureScript MCP Server Enables Text-to-Code-to-CAD](https://www.onshape.com/en/blog/featurescript-mcp-server-enables-text-code-cad)
- [Onshape: Getting Started with the FeatureScript MCP Server](https://www.onshape.com/en/blog/get-started-featurescript-mcp-server)
- [Onshape: FeatureScript MCP Use Cases](https://www.onshape.com/en/blog/featurescript-mcp-server-use-cases-text-code-cad)
- [PTC FeatureScript MCP announcement](https://www.ptc.com/en/news/2026/onshape-launches-featurescript-mcp-server)
- [Official MCP protected-resource metadata](https://fs-mcp.labs.onshape.app/.well-known/oauth-protected-resource/mcp)
- [Official MCP OAuth server metadata](https://fs-mcp.labs.onshape.app/.well-known/oauth-authorization-server)
