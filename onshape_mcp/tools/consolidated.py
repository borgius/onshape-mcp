"""Public schemas for the consolidated Onshape MCP surface."""

from __future__ import annotations

from mcp.types import Tool, ToolAnnotations


def _target_properties(*, element: bool = False) -> dict:
    properties = {
        "documentId": {"type": "string", "minLength": 1, "description": "Onshape document ID"},
        "workspaceId": {"type": "string", "minLength": 1, "description": "Onshape workspace ID"},
        "versionId": {"type": "string", "minLength": 1, "description": "Onshape version ID"},
        "microversionId": {
            "type": "string",
            "minLength": 1,
            "description": "Onshape microversion ID",
        },
        "partId": {"type": "string", "minLength": 1, "description": "Onshape part ID"},
        "featureId": {"type": "string", "minLength": 1, "description": "Onshape feature ID"},
        "instanceId": {"type": "string", "minLength": 1, "description": "Assembly instance ID"},
        "mateId": {"type": "string", "minLength": 1, "description": "Assembly mate ID"},
    }
    if element:
        properties["elementId"] = {
            "type": "string",
            "minLength": 1,
            "description": "Onshape element ID",
        }
    return properties


def _target_required(*, element: bool = False) -> list[str]:
    return ["documentId", *(["elementId"] if element else [])]


def _target_selector_schema() -> dict:
    return {
        "oneOf": [
            {
                "required": ["workspaceId"],
                "not": {"anyOf": [{"required": ["versionId"]}, {"required": ["microversionId"]}]},
            },
            {
                "required": ["versionId"],
                "not": {"anyOf": [{"required": ["workspaceId"]}, {"required": ["microversionId"]}]},
            },
            {
                "required": ["microversionId"],
                "not": {"anyOf": [{"required": ["workspaceId"]}, {"required": ["versionId"]}]},
            },
        ]
    }


def consolidated_tools(profile: str = "advanced") -> list[Tool]:
    """Return the reviewed tool list for the selected MCP profile."""
    target = _target_properties()
    target_element = _target_properties(element=True)
    tools = [
        Tool(
            name="search_onshape",
            description="Search documents or list workspaces, elements, and Part Studios.",
            inputSchema={
                "type": "object",
                "properties": {
                    "resourceKind": {
                        "type": "string",
                        "enum": ["documents", "workspaces", "elements", "partstudios"],
                        "default": "documents",
                    },
                    "query": {"type": "string"},
                    "documentId": {"type": "string"},
                    "workspaceId": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "filterType": {"type": "string"},
                },
            },
        ),
        Tool(
            name="get_onshape_context",
            description="Get a normalized document, workspace, and element context.",
            inputSchema={
                "type": "object",
                "properties": {"documentId": {"type": "string"}},
                "required": ["documentId"],
            },
        ),
        Tool(
            name="inspect_part_studio",
            description="Inspect Part Studio features, parts, and body geometry.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "sections": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["features", "parts", "bodyDetails"]},
                    },
                },
                "required": _target_required(element=True),
            },
        ),
        Tool(
            name="inspect_assembly",
            description="Inspect Assembly definition, features, positions, and interference.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "sections": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["definition", "features"]},
                    },
                    "includePositions": {"type": "boolean", "default": False},
                    "includeInterference": {"type": "boolean", "default": False},
                },
                "required": _target_required(element=True),
            },
        ),
        Tool(
            name="inspect_geometry",
            description="Inspect Part Studio body details and optionally capture a screenshot.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "includeScreenshot": {"type": "boolean", "default": False},
                    "screenshotOptions": {"type": "object"},
                },
                "required": _target_required(element=True),
            },
        ),
        Tool(
            name="get_translation",
            description="Read or poll translation status and expose download references.",
            inputSchema={
                "type": "object",
                "properties": {
                    "translationId": {"type": "string", "minLength": 1},
                    **target,
                    "poll": {"type": "boolean", "default": False},
                    "timeoutSeconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 600,
                        "default": 30,
                    },
                    "intervalSeconds": {
                        "type": "number",
                        "minimum": 0.1,
                        "maximum": 60,
                        "default": 1,
                    },
                },
                "required": ["translationId"],
            },
        ),
        Tool(
            name="search_featurescript_docs",
            description="Search indexed, reviewed FeatureScript documentation excerpts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="test_featurescript",
            description="Evaluate or compile/regenerate FeatureScript and inspect resulting geometry.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "script": {"type": "string"},
                    "compileOperationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "compileBody": {"type": "object"},
                    "regenerateOperationId": {"type": "string"},
                    "regeneratePathParams": {"type": "object"},
                    "regenerateBody": {"type": "object"},
                    "inspectGeometry": {"type": "boolean", "default": False},
                },
                "required": _target_required(element=True),
                "oneOf": [
                    {"required": ["script"]},
                    {"required": ["compileOperationId", "pathParams"]},
                ],
            },
        ),
        Tool(
            name="create_onshape_object",
            description="Create a supported object kind, or execute a reviewed creation operation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "document",
                            "workspace",
                            "version",
                            "partStudio",
                            "assembly",
                            "featureStudio",
                            "variableStudio",
                        ],
                    },
                    "name": {"type": "string"},
                    "documentId": {"type": "string"},
                    "workspaceId": {"type": "string"},
                    "description": {"type": "string"},
                    "isPublic": {"type": "boolean", "default": True},
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                    "body": {"type": "object"},
                },
                "required": ["kind"],
                "oneOf": [
                    {"required": ["name"]},
                    {"required": ["operationId", "body"]},
                ],
            },
        ),
        Tool(
            name="edit_sketch",
            description="Create or update a multi-entity sketch with one domain request.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "action": {"type": "string", "enum": ["create", "update"], "default": "create"},
                    "plane": {
                        "type": "string",
                        "enum": ["Front", "Top", "Right"],
                        "default": "Front",
                    },
                    "planeId": {"type": "string"},
                    "name": {"type": "string", "default": "Sketch"},
                    "entities": {"type": "array", "items": {"type": "object"}},
                    "constraints": {"type": "array", "items": {"type": "object"}},
                    "featureId": {"type": "string"},
                    "featureData": {"type": "object"},
                },
                "required": _target_required(element=True),
            },
        ),
        Tool(
            name="edit_part_feature",
            description="Create or update a Part Studio feature definition.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "action": {"type": "string", "enum": ["create", "update"], "default": "create"},
                    "featureId": {"type": "string"},
                    "featureData": {"type": "object"},
                },
                "required": [*_target_required(element=True), "featureData"],
            },
        ),
        Tool(
            name="edit_variables",
            description="Create or update multiple Variable Studio variables.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "variables": {
                        "type": "array",
                        "items": {"type": "object", "required": ["name", "expression"]},
                    },
                },
                "required": [*_target_required(element=True), "variables"],
            },
        ),
        Tool(
            name="edit_assembly_instance",
            description="Add, transform, position, align, fix, suppress, or delete an Assembly instance.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "action": {
                        "type": "string",
                        "enum": [
                            "add",
                            "transform",
                            "position",
                            "align",
                            "fix",
                            "unfix",
                            "suppress",
                            "unsuppress",
                            "delete",
                        ],
                    },
                    "payload": {"type": "object"},
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                },
                "required": [*_target_required(element=True), "action", "payload"],
            },
        ),
        Tool(
            name="edit_assembly_mate",
            description="Create or delete an Assembly mate or mate connector feature.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "action": {
                        "type": "string",
                        "enum": [
                            "create",
                            "update",
                            "delete",
                            "suppress",
                            "unsuppress",
                            "inspect",
                        ],
                    },
                    "featureId": {"type": "string"},
                    "featureData": {"type": "object"},
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                },
                "required": [*_target_required(element=True), "action"],
            },
        ),
        Tool(
            name="delete_onshape_object",
            description="Plan or execute an allowlisted destructive operation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "operationId": {"type": "string"},
                    "resourceKind": {"type": "string"},
                    "target": {"type": "object"},
                    "requestBody": {},
                    "planOnly": {"type": "boolean", "default": False},
                    "ttlSeconds": {"type": "integer", "minimum": 1, "maximum": 600},
                    "planToken": {"type": "string"},
                    "identity": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                },
                "required": ["operationId", "resourceKind", "target", "identity"],
                "oneOf": [
                    {"required": ["planToken"]},
                    {"required": ["planOnly"], "properties": {"planOnly": {"const": True}}},
                ],
            },
        ),
        Tool(
            name="query_onshape_operation",
            description="Execute an allowlisted read-only OpenAPI operation by operationId.",
            inputSchema={
                "type": "object",
                "properties": {
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                },
                "required": ["operationId"],
            },
        ),
        Tool(
            name="mutate_onshape_operation",
            description="Execute an allowlisted write operation by operationId; deletes require a plan token.",
            inputSchema={
                "type": "object",
                "properties": {
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                    "body": {},
                    "resourceKind": {"type": "string"},
                    "target": {"type": "object"},
                    "planToken": {"type": "string"},
                    "identity": {"type": "string"},
                },
                "required": ["operationId"],
            },
        ),
        Tool(
            name="get_feature_schema",
            description="Retrieve one feature definition schema through a reviewed OpenAPI operation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                },
                "required": ["operationId"],
            },
        ),
        Tool(
            name="read_featurescript",
            description="Read Feature Studio source through a reviewed OpenAPI operation.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                },
                "required": [*_target_required(element=True), "operationId"],
            },
        ),
        Tool(
            name="write_featurescript",
            description="Write Feature Studio source with an optional optimistic revision.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "body": {"type": "object"},
                    "expectedRevision": {"type": "string"},
                },
                "required": [*_target_required(element=True), "operationId", "body"],
            },
        ),
        Tool(
            name="start_translation",
            description="Start a Part Studio, Assembly, or reviewed translation operation.",
            inputSchema={
                "type": "object",
                "properties": {
                    **target_element,
                    "resourceKind": {
                        "type": "string",
                        "enum": ["partstudio", "assembly", "drawing", "blob"],
                    },
                    "formatName": {"type": "string", "default": "STL"},
                    "partId": {"type": "string"},
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                    "body": {"type": "object"},
                },
                "required": [*_target_required(element=True)],
                "oneOf": [
                    {"required": ["resourceKind"]},
                    {"required": ["operationId", "body"]},
                ],
            },
        ),
        Tool(
            name="edit_onshape_object",
            description="Execute a reviewed non-destructive object lifecycle operation by operationId.",
            inputSchema={
                "type": "object",
                "properties": {
                    "operationId": {"type": "string"},
                    "pathParams": {"type": "object"},
                    "queryParams": {"type": "object"},
                    "body": {},
                },
                "required": ["operationId"],
            },
        ),
    ]
    read_only = {
        "search_onshape",
        "get_onshape_context",
        "inspect_part_studio",
        "inspect_assembly",
        "inspect_geometry",
        "get_translation",
        "search_featurescript_docs",
        "test_featurescript",
        "get_feature_schema",
        "read_featurescript",
    }
    target_tools = {
        "inspect_part_studio",
        "inspect_assembly",
        "inspect_geometry",
        "test_featurescript",
        "read_featurescript",
        "write_featurescript",
        "edit_sketch",
        "edit_part_feature",
        "edit_variables",
        "edit_assembly_instance",
        "edit_assembly_mate",
        "start_translation",
    }
    for tool in tools:
        if tool.name in target_tools:
            tool.inputSchema.setdefault("allOf", []).append(_target_selector_schema())
    destructive = {"delete_onshape_object", "mutate_onshape_operation"}
    for tool in tools:
        tool.annotations = ToolAnnotations(
            readOnlyHint=tool.name in read_only,
            destructiveHint=tool.name in destructive,
            idempotentHint=tool.name in read_only or tool.name in {"edit_onshape_object"},
            openWorldHint=tool.name in {"query_onshape_operation", "mutate_onshape_operation"},
        )
    if profile not in {"default", "advanced"}:
        raise ValueError(f"unknown MCP profile: {profile}")
    if profile == "default":
        return [
            tool
            for tool in tools
            if tool.name not in {"query_onshape_operation", "mutate_onshape_operation"}
        ]
    return tools
