"""Main MCP server for Onshape integration."""

import os
import sys
import asyncio
import hmac
import json
from typing import Any
from urllib.parse import parse_qs
import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent, Resource, ResourceTemplate
from loguru import logger

# Load environment variables from .env file before local imports read them.
# Look for .env in the package directory (where this server.py lives).
_package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_package_dir, ".env"))

from .api.client import OnshapeClient, OnshapeCredentials
from .api.partstudio import PartStudioManager
from .api.variables import VariableManager
from .api.documents import DocumentManager
from .builders.sketch import SketchBuilder, SketchPlane
from .builders.extrude import ExtrudeBuilder, ExtrudeType
from .builders.thicken import ThickenBuilder, ThickenType
from .api.assemblies import AssemblyManager
from .api.featurescript import FeatureScriptManager
from .api.export import ExportManager
from .api.visuals import VisualsManager
from .builders.mate import MateBuilder, MateConnectorBuilder, MateType, build_transform_matrix
from .builders.fillet import FilletBuilder
from .builders.chamfer import ChamferBuilder, ChamferType
from .builders.revolve import RevolveBuilder, RevolveType
from .builders.pattern import LinearPatternBuilder, CircularPatternBuilder
from .builders.axis_helper import build_axis_sketch
from .builders.boolean import BooleanBuilder, BooleanType
from .analysis.interference import check_assembly_interference, format_interference_result
from .analysis.positioning import get_assembly_positions, set_absolute_position, align_to_face
from .api.registry import OperationRegistry
from .models.references import OnshapeTarget
from .models.results import ToolResult
from .services import (
    AssemblyInspectionService,
    AssemblyWriteService,
    DestructiveOperationGate,
    DiscoveryService,
    FeatureScriptDocumentationIndex,
    FeatureScriptDocument,
    FeatureScriptWorkflowService,
    GeometryInspectionService,
    ObjectWriteService,
    OpenAPIGateway,
    PartFeatureWriteService,
    PartStudioInspectionService,
    SketchWriteService,
    TranslationService,
    VariableWriteService,
)
from .tools.consolidated import consolidated_tools

# Configure loguru to output to stderr
logger.remove()  # Remove default handler
logger.add(
    sys.stderr,
    level="DEBUG",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
)

# Bridge Python stdlib logging into loguru. The MCP SDK (including its
# Streamable HTTP transport, which emits the opaque -32603 "Internal error"
# responses) logs through stdlib logging, so without this bridge its exception
# stack traces never reach the server's stderr output.
import logging


class _LoguruInterceptHandler(logging.Handler):
    """Forward stdlib logging records to loguru with full exception info."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[_LoguruInterceptHandler()], level=logging.INFO, force=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# Initialize server
app = Server("onshape-mcp")

# Initialize Onshape client
_ak = os.getenv("ONSHAPE_ACCESS_KEY", "")
_sk = os.getenv("ONSHAPE_SECRET_KEY", "")
credentials = OnshapeCredentials(access_key=_ak, secret_key=_sk)
client = OnshapeClient(credentials)
partstudio_manager = PartStudioManager(client)
variable_manager = VariableManager(client)
document_manager = DocumentManager(client)
assembly_manager = AssemblyManager(client)
featurescript_manager = FeatureScriptManager(client)
export_manager = ExportManager(client)
visuals_manager = VisualsManager(client)
operation_registry = OperationRegistry()
_openapi_path = os.getenv("ONSHAPE_OPENAPI_PATH")
if _openapi_path and os.path.isfile(_openapi_path):
    operation_registry = OperationRegistry.from_json_file(_openapi_path)
_authenticated_scopes = frozenset(
    scope.strip() for scope in os.getenv("ONSHAPE_SCOPES", "").split(",") if scope.strip()
)
_mcp_profile = os.getenv("ONSHAPE_MCP_PROFILE", "default").lower()
if _mcp_profile not in {"default", "advanced"}:
    raise RuntimeError("ONSHAPE_MCP_PROFILE must be 'default' or 'advanced'")
destructive_gate = DestructiveOperationGate()
discovery_service = DiscoveryService(document_manager)
partstudio_inspection_service = PartStudioInspectionService(partstudio_manager)
assembly_inspection_service = AssemblyInspectionService(assembly_manager, partstudio_manager)
geometry_inspection_service = GeometryInspectionService(partstudio_manager, visuals_manager)
translation_service = TranslationService(export_manager)
featurescript_docs = FeatureScriptDocumentationIndex(
    [
        FeatureScriptDocument(
            title="FeatureScript introduction",
            content=(
                "FeatureScript is Onshape's language for building parametric features. "
                "Native features such as Extrude and Fillet are implemented with FeatureScript."
            ),
            source="https://cad.onshape.com/FsDoc/intro.html",
            version="v1",
        ),
        FeatureScriptDocument(
            title="Feature Studios",
            content=(
                "A Feature Studio is an Onshape document tab containing FeatureScript. "
                "Custom features authored there can be reused in Part Studios."
            ),
            source="https://cad.onshape.com/help/Content/featurestudios.htm",
            version="v1",
        ),
        FeatureScriptDocument(
            title="FeatureScript source validation cookbook",
            content=(
                "Validate source with a reviewed compile operation before regeneration. "
                "Preserve compiler diagnostics with line and column information, then "
                "inspect the resulting Part Studio bounding box and body details."
            ),
            source="onshape-mcp://featurescript/cookbook",
            version="v1",
        ),
    ]
)
featurescript_workflow_service = FeatureScriptWorkflowService(
    featurescript_manager,
    partstudio_manager=partstudio_manager,
    documentation=featurescript_docs,
)
object_write_service = ObjectWriteService(
    document_manager,
    partstudio_manager,
    assembly_manager,
    variable_manager,
    feature_manager=featurescript_manager,
)
sketch_write_service = SketchWriteService(partstudio_manager)
part_feature_write_service = PartFeatureWriteService(partstudio_manager)
variable_write_service = VariableWriteService(variable_manager)
assembly_write_service = AssemblyWriteService(assembly_manager)
openapi_gateway = OpenAPIGateway(
    client,
    operation_registry,
    destructive_gate=destructive_gate,
    authenticated_scopes=_authenticated_scopes,
)
CONSOLIDATED_TOOL_NAMES = frozenset(tool.name for tool in consolidated_tools())


async def _create_axis_edge(document_id: str, workspace_id: str, element_id: str, axis: str) -> str:
    """Add a construction line through the origin along the global ``axis`` and
    return that line's edge deterministic id, for use as a revolve / circular
    pattern axis. Onshape exposes no queryable origin axis, so we make one."""
    sketch_payload = build_axis_sketch(axis, name=f"_Axis {axis.upper()}")
    result = await partstudio_manager.add_feature(
        document_id, workspace_id, element_id, sketch_payload
    )
    sketch_id = result.get("feature", {}).get("featureId", result.get("featureId"))
    fs = await featurescript_manager.evaluate(
        document_id,
        workspace_id,
        element_id,
        "function(context is Context, queries) { return transientQueriesToStrings("
        f'evaluateQuery(context, qCreatedBy(makeId("{sketch_id}"), EntityType.EDGE))); }}',
    )
    values = fs.get("result", {}).get("value", [])
    if not values:
        raise RuntimeError("axis construction line produced no edge")
    return values[0]["value"]


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List the reviewed tools for the configured MCP profile."""
    return consolidated_tools(_mcp_profile)


@app.list_resources()
async def list_resources() -> list[Resource]:
    """List static MCP resources; operation schemas are exposed as templates."""
    return []


@app.list_resource_templates()
async def list_resource_templates() -> list[ResourceTemplate]:
    return [
        ResourceTemplate(
            name="operation-schema",
            uriTemplate="onshape://schemas/{operationId}",
            description="Reviewed OpenAPI schema for one operation.",
            mimeType="application/json",
        )
    ]


@app.read_resource()
async def read_resource(uri: Any) -> str:
    """Read one reviewed operation schema through the gateway."""
    prefix = "onshape://schemas/"
    uri_text = str(uri)
    if not uri_text.startswith(prefix):
        raise ValueError("unsupported resource URI")
    operation_id = uri_text[len(prefix) :]
    result = openapi_gateway.schema(operation_id)
    if not result.ok:
        raise ValueError(result.summary)
    return json.dumps(result.data, default=str)


METERS_TO_INCHES = 1 / 0.0254


def _enrich_rectangular_body(
    planar_faces: list[dict],
) -> dict | None:
    """Compute enriched face data for rectangular solids (6 planar faces).

    Groups faces by normal axis, determines true outward normals,
    computes face dimensions, and adds directional labels.

    Returns None if the body doesn't appear to be a rectangular solid.
    """
    if len(planar_faces) != 6:
        return None

    # Group faces by dominant normal axis
    axis_groups: dict[str, list[dict]] = {"x": [], "y": [], "z": []}
    for face in planar_faces:
        abs_nx = abs(face["nx"])
        abs_ny = abs(face["ny"])
        abs_nz = abs(face["nz"])
        if abs_nx >= abs_ny and abs_nx >= abs_nz:
            axis_groups["x"].append(face)
        elif abs_ny >= abs_nx and abs_ny >= abs_nz:
            axis_groups["y"].append(face)
        else:
            axis_groups["z"].append(face)

    # Need exactly 2 faces per axis for a rectangular solid
    if not all(len(g) == 2 for g in axis_groups.values()):
        return None

    # Sort each axis pair by origin coordinate; determine outward normals
    faces_enriched: dict[str, dict] = {}
    bbox: dict[str, float] = {}

    axis_coord = {"x": "ox", "y": "oy", "z": "oz"}
    outward_labels = {
        "x": [("-X", "(-1, 0, 0)"), ("+X", "(+1, 0, 0)")],
        "y": [("-Y", "(0, -1, 0)"), ("+Y", "(0, +1, 0)")],
        "z": [("-Z", "(0, 0, -1)"), ("+Z", "(0, 0, +1)")],
    }

    for axis, group in axis_groups.items():
        coord_key = axis_coord[axis]
        sorted_faces = sorted(group, key=lambda f: f[coord_key])
        bbox[f"{axis}_min"] = sorted_faces[0][coord_key]
        bbox[f"{axis}_max"] = sorted_faces[1][coord_key]

        for i, face in enumerate(sorted_faces):
            label, outward = outward_labels[axis][i]
            faces_enriched[face["id"]] = {
                "label": label,
                "outward_normal": outward,
                "axis": axis,
                "is_max": i == 1,
            }

    # Compute body dimensions in inches
    lx = (bbox["x_max"] - bbox["x_min"]) * METERS_TO_INCHES
    ly = (bbox["y_max"] - bbox["y_min"]) * METERS_TO_INCHES
    lz = (bbox["z_max"] - bbox["z_min"]) * METERS_TO_INCHES

    # Compute face dimensions (the two dimensions perpendicular to face normal)
    face_dims = {"x": (ly, lz), "y": (lx, lz), "z": (lx, ly)}
    for face_id, data in faces_enriched.items():
        w, h = face_dims[data["axis"]]
        data["width"] = w
        data["height"] = h

    return {"dimensions": (lx, ly, lz), "faces": faces_enriched}


def _extract_offsets(arguments: dict, prefix: str) -> tuple[float, float, float] | None:
    """Extract XYZ offset tuple from tool arguments, returning None if all zero."""
    x = arguments.get(f"{prefix}OffsetX", 0)
    y = arguments.get(f"{prefix}OffsetY", 0)
    z = arguments.get(f"{prefix}OffsetZ", 0)
    if x == 0 and y == 0 and z == 0:
        return None
    return (x, y, z)


async def _create_mate(
    assembly_manager,
    document_id: str,
    workspace_id: str,
    element_id: str,
    first_instance_id: str,
    second_instance_id: str,
    first_face_id: str,
    second_face_id: str,
    mate_name: str,
    mate_type: MateType,
    min_limit: float | None = None,
    max_limit: float | None = None,
    first_offset: tuple[float, float, float] | None = None,
    second_offset: tuple[float, float, float] | None = None,
) -> str:
    """Create a mate between two instances using explicit mate connectors.

    Creates mate connectors on faces of each instance, then creates the mate
    between them. Uses BTMInferenceQueryWithOccurrence-1083 with CENTROID
    inference to place connectors at face centers.

    Args:
        first_offset: Optional (x, y, z) offset in inches from first face centroid
        second_offset: Optional (x, y, z) offset in inches from second face centroid

    Returns the mate feature ID.
    """
    # Create explicit mate connector on a face of the first instance
    mc1 = MateConnectorBuilder(
        name=f"{mate_name} - MC1",
        face_id=first_face_id,
        occurrence_path=[first_instance_id],
    )
    if first_offset:
        mc1.set_translation(*first_offset)
    result1 = await assembly_manager.add_feature(
        document_id=document_id,
        workspace_id=workspace_id,
        element_id=element_id,
        feature_data=mc1.build(),
    )
    mc1_id = result1.get("feature", {}).get("featureId", "unknown")

    # Create explicit mate connector on a face of the second instance
    mc2 = MateConnectorBuilder(
        name=f"{mate_name} - MC2",
        face_id=second_face_id,
        occurrence_path=[second_instance_id],
    )
    if second_offset:
        mc2.set_translation(*second_offset)
    result2 = await assembly_manager.add_feature(
        document_id=document_id,
        workspace_id=workspace_id,
        element_id=element_id,
        feature_data=mc2.build(),
    )
    mc2_id = result2.get("feature", {}).get("featureId", "unknown")

    # Create the mate referencing the explicit mate connectors
    mate = MateBuilder(name=mate_name, mate_type=mate_type)
    mate.set_first_connector(mc1_id)
    mate.set_second_connector(mc2_id)
    if min_limit is not None and max_limit is not None:
        mate.set_limits(min_limit, max_limit)
    result = await assembly_manager.add_feature(
        document_id=document_id,
        workspace_id=workspace_id,
        element_id=element_id,
        feature_data=mate.build(),
    )
    return result.get("feature", {}).get("featureId", "unknown")


def _sanitize_args(arguments: Any, max_length: int = 500) -> str:
    """Compact, secret-free representation of tool arguments for logging."""
    text = str(arguments)
    return text if len(text) <= max_length else text[:max_length] + "... (truncated)"


def _consolidated_text(result: Any, tool_name: str) -> list[TextContent]:
    """Encode a structured domain result as one MCP text item."""
    if hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json", by_alias=True)
        if not payload.get("summary"):
            payload["summary"] = (
                f"{tool_name} completed" if payload.get("ok") else f"{tool_name} failed"
            )
    else:
        payload = result
    return [TextContent(type="text", text=json.dumps(payload, default=str))]


def _target_from_arguments(arguments: dict[str, Any], *, element: bool = False) -> OnshapeTarget:
    values = {"documentId": arguments["documentId"]}
    selectors = ("workspaceId", "versionId", "microversionId")
    provided = [name for name in selectors if arguments.get(name) is not None]
    if len(provided) != 1:
        raise ValueError("exactly one of workspaceId, versionId, or microversionId is required")
    values[provided[0]] = arguments[provided[0]]
    if element:
        values["elementId"] = arguments["elementId"]
    for identifier in ("partId", "featureId", "instanceId", "mateId"):
        if arguments.get(identifier) is not None:
            values[identifier] = arguments[identifier]
    return OnshapeTarget(**values)


async def _call_consolidated_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Dispatch the public consolidated tool surface to domain services."""
    if name == "search_onshape":
        result = await discovery_service.search_onshape(
            arguments.get("resourceKind", "documents"),
            query=arguments.get("query"),
            document_id=arguments.get("documentId"),
            workspace_id=arguments.get("workspaceId"),
            limit=arguments.get("limit", 20),
            filter_type=arguments.get("filterType"),
        )
    elif name == "get_onshape_context":
        result = await discovery_service.get_onshape_context(arguments["documentId"])
    elif name == "inspect_part_studio":
        result = await partstudio_inspection_service.inspect(
            _target_from_arguments(arguments, element=True),
            sections=arguments.get("sections", ["features", "parts", "bodyDetails"]),
        )
    elif name == "inspect_assembly":
        result = await assembly_inspection_service.inspect(
            _target_from_arguments(arguments, element=True),
            sections=arguments.get("sections", ["definition", "features"]),
            include_positions=arguments.get("includePositions", False),
            include_interference=arguments.get("includeInterference", False),
        )
    elif name == "inspect_geometry":
        result = await geometry_inspection_service.inspect_part_studio(
            _target_from_arguments(arguments, element=True),
            include_screenshot=arguments.get("includeScreenshot", False),
            screenshot_options=arguments.get("screenshotOptions"),
        )
    elif name == "get_translation":
        target = None
        if arguments.get("documentId") and arguments.get("workspaceId"):
            target = _target_from_arguments(arguments)
        result = await translation_service.get_translation(
            arguments["translationId"],
            target=target,
            poll=arguments.get("poll", False),
            timeout_seconds=arguments.get("timeoutSeconds", 30.0),
            interval_seconds=arguments.get("intervalSeconds", 1.0),
        )
    elif name == "search_featurescript_docs":
        result = featurescript_workflow_service.search_docs(
            arguments["query"], limit=arguments.get("limit", 5)
        )
    elif name == "test_featurescript":
        target = _target_from_arguments(arguments, element=True)
        if arguments.get("compileOperationId"):
            result = await featurescript_workflow_service.test_source(
                target,
                operation_registry,
                compile_operation_id=arguments["compileOperationId"],
                path_params=arguments.get("pathParams", {}),
                compile_body=arguments.get("compileBody"),
                regenerate_operation_id=arguments.get("regenerateOperationId"),
                regenerate_path_params=arguments.get("regeneratePathParams"),
                regenerate_body=arguments.get("regenerateBody"),
                inspect_geometry=arguments.get("inspectGeometry", False),
            )
        else:
            result = await featurescript_workflow_service.evaluate_expression(
                target,
                arguments["script"],
                inspect_geometry=arguments.get("inspectGeometry", False),
            )
    elif name == "get_feature_schema":
        result = openapi_gateway.schema(arguments["operationId"])
    elif name == "read_featurescript":
        result = await featurescript_workflow_service.read_source(
            _target_from_arguments(arguments, element=True),
            operation_registry,
            arguments["operationId"],
            path_params=arguments.get("pathParams", {}),
            query_params=arguments.get("queryParams"),
        )
    elif name == "write_featurescript":
        result = await featurescript_workflow_service.write_source(
            _target_from_arguments(arguments, element=True),
            operation_registry,
            arguments["operationId"],
            path_params=arguments.get("pathParams", {}),
            body=arguments["body"],
            expected_revision=arguments.get("expectedRevision"),
        )
    elif name == "start_translation":
        if arguments.get("operationId"):
            result = await openapi_gateway.mutate(
                arguments["operationId"],
                path_params=arguments.get("pathParams"),
                query_params=arguments.get("queryParams"),
                body=arguments.get("body"),
            )
        else:
            result = await translation_service.start_translation(
                _target_from_arguments(arguments, element=True),
                resource_kind=arguments["resourceKind"],
                format_name=arguments.get("formatName", "STL"),
                part_id=arguments.get("partId"),
            )
    elif name == "edit_onshape_object":
        result = await openapi_gateway.mutate(
            arguments["operationId"],
            path_params=arguments.get("pathParams"),
            query_params=arguments.get("queryParams"),
            body=arguments.get("body"),
        )
    elif name == "create_onshape_object":
        if arguments.get("operationId"):
            result = await openapi_gateway.mutate(
                arguments["operationId"],
                path_params=arguments.get("pathParams"),
                query_params=arguments.get("queryParams"),
                body=arguments.get("body", {"name": arguments.get("name")}),
            )
        else:
            result = await object_write_service.create(
                arguments["kind"],
                name=arguments["name"],
                document_id=arguments.get("documentId"),
                workspace_id=arguments.get("workspaceId"),
                description=arguments.get("description"),
                is_public=arguments.get("isPublic", True),
            )
    elif name == "edit_sketch":
        result = await sketch_write_service.edit(
            _target_from_arguments(arguments, element=True),
            action=arguments.get("action", "create"),
            plane=arguments.get("plane", "Front"),
            plane_id=arguments.get("planeId"),
            name=arguments.get("name", "Sketch"),
            entities=arguments.get("entities"),
            constraints=arguments.get("constraints"),
            feature_data=arguments.get("featureData"),
            feature_id=arguments.get("featureId"),
        )
    elif name == "edit_part_feature":
        result = await part_feature_write_service.edit(
            _target_from_arguments(arguments, element=True),
            action=arguments.get("action", "create"),
            feature_data=arguments.get("featureData"),
            feature_id=arguments.get("featureId"),
        )
    elif name == "edit_variables":
        result = await variable_write_service.edit(
            _target_from_arguments(arguments, element=True),
            arguments["variables"],
        )
    elif name == "edit_assembly_instance":
        if arguments.get("operationId"):
            result = await openapi_gateway.mutate(
                arguments["operationId"],
                path_params=arguments.get("pathParams"),
                query_params=arguments.get("queryParams"),
                body=arguments.get("payload"),
            )
        else:
            result = await assembly_write_service.edit_instance(
                _target_from_arguments(arguments, element=True),
                action=arguments["action"],
                payload=arguments["payload"],
            )
    elif name == "edit_assembly_mate":
        if arguments.get("operationId"):
            result = await openapi_gateway.mutate(
                arguments["operationId"],
                path_params=arguments.get("pathParams"),
                query_params=arguments.get("queryParams"),
                body=arguments.get("featureData"),
            )
        else:
            result = await assembly_write_service.edit_mate(
                _target_from_arguments(arguments, element=True),
                action=arguments["action"],
                feature_data=arguments.get("featureData"),
                feature_id=arguments.get("featureId"),
            )
    elif name == "delete_onshape_object":
        if arguments.get("planOnly"):
            try:
                operation = operation_registry.get(arguments["operationId"])
                if not operation.is_destructive:
                    result = ToolResult.failure(
                        "delete_onshape_object requires a delete-classified operation",
                        code="DELETE_GATE_REJECTED",
                    )
                elif not operation.reviewed:
                    result = ToolResult.failure(
                        "destructive operation is not reviewed",
                        code="REVIEW_REQUIRED",
                    )
                else:
                    result = destructive_gate.plan(
                        operation_id=operation.operation_id,
                        resource_kind=arguments["resourceKind"],
                        target=arguments["target"],
                        request_body=arguments.get("requestBody"),
                        identity=arguments["identity"],
                        ttl_seconds=arguments.get("ttlSeconds"),
                    )
            except Exception as exc:
                result = ToolResult.failure(str(exc), code="INVALID_PLAN")
        else:
            result = await openapi_gateway.mutate(
                arguments["operationId"],
                path_params=arguments.get("pathParams"),
                query_params=arguments.get("queryParams"),
                body=arguments.get("requestBody"),
                resource_kind=arguments["resourceKind"],
                target=arguments["target"],
                plan_token=arguments["planToken"],
                identity=arguments["identity"],
            )
    elif name == "query_onshape_operation":
        result = await openapi_gateway.query(
            arguments["operationId"],
            path_params=arguments.get("pathParams"),
            query_params=arguments.get("queryParams"),
        )
    elif name == "mutate_onshape_operation":
        result = await openapi_gateway.mutate(
            arguments["operationId"],
            path_params=arguments.get("pathParams"),
            query_params=arguments.get("queryParams"),
            body=arguments.get("body"),
            resource_kind=arguments.get("resourceKind"),
            target=arguments.get("target"),
            plan_token=arguments.get("planToken"),
            identity=arguments.get("identity"),
        )
    else:
        raise ValueError(f"Unknown consolidated tool: {name}")
    return _consolidated_text(result, name)


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent | ImageContent]:
    """Handle tool calls with entry + exception logging.

    Exceptions are logged with a full stack trace and re-raised so the MCP SDK
    turns them into an ``isError=True`` tool result (never an opaque transport
    error). The Onshape client already logs upstream status/request-id/body.
    """
    logger.info(f"MCP call_tool '{name}' args={_sanitize_args(arguments)}")
    try:
        return await _call_tool_impl(name, arguments)
    except Exception:
        logger.exception(f"MCP call_tool '{name}' raised an exception")
        raise


async def _call_tool_impl(name: str, arguments: Any) -> list[TextContent | ImageContent]:
    """Dispatch a tool call to its handler."""
    if name in CONSOLIDATED_TOOL_NAMES:
        return await _call_consolidated_tool(name, arguments)

    if name == "create_sketch_rectangle":
        try:
            # Get the plane name and resolve its ID
            plane_name = arguments.get("plane", "Front")
            plane = SketchPlane[plane_name.upper()]

            # Resolve the plane ID from Onshape
            plane_id = await partstudio_manager.get_plane_id(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                plane_name,
            )

            # Build sketch with rectangle
            sketch = SketchBuilder(
                name=arguments.get("name", "Sketch"), plane=plane, plane_id=plane_id
            )

            sketch.add_rectangle(
                corner1=tuple(arguments["corner1"]),
                corner2=tuple(arguments["corner2"]),
                variable_width=arguments.get("variableWidth"),
                variable_height=arguments.get("variableHeight"),
            )

            # Add feature to Part Studio
            feature_data = sketch.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )

            feature_id = result.get("feature", {}).get("featureId", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Created sketch '{arguments.get('name', 'Sketch')}' with rectangle on {plane_name} plane. Feature ID: {feature_id}",
                )
            ]

        except Exception as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating sketch: {str(e)}\n\nPlease check the document/workspace/element IDs and try again.",
                )
            ]

    elif name == "create_extrude":
        try:
            # Build extrude
            op_type = ExtrudeType[arguments.get("operationType", "NEW")]
            extrude = ExtrudeBuilder(
                name=arguments.get("name", "Extrude"),
                sketch_feature_id=arguments["sketchFeatureId"],
                operation_type=op_type,
            )

            extrude.set_depth(arguments["depth"], variable_name=arguments.get("variableDepth"))

            if arguments.get("oppositeDirection"):
                extrude.set_opposite_direction(True)

            # Add feature to Part Studio
            feature_data = extrude.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )

            return [
                TextContent(
                    type="text",
                    text=f"Created extrude '{arguments.get('name', 'Extrude')}'. Feature ID: {result.get('feature', {}).get('featureId', result.get('featureId', 'unknown'))}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(
                f"API error creating extrude: {e.response.status_code} - {e.response.text[:500]}"
            )
            return [
                TextContent(
                    type="text",
                    text=f"Error creating extrude: API returned {e.response.status_code}. Check that the sketch feature ID is valid and parameters are correct.",
                )
            ]
        except KeyError:
            return [
                TextContent(
                    type="text",
                    text="Error creating extrude: Invalid operation type. Must be one of: NEW, ADD, REMOVE, INTERSECT.",
                )
            ]
        except ValueError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating extrude: {str(e)}",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating extrude")
            return [
                TextContent(
                    type="text",
                    text=f"Error creating extrude: {str(e)}\n\nPlease check the parameters and try again.",
                )
            ]

    elif name == "create_thicken":
        try:
            # Build thicken
            op_type = ThickenType[arguments.get("operationType", "NEW")]
            thicken = ThickenBuilder(
                name=arguments.get("name", "Thicken"),
                sketch_feature_id=arguments["sketchFeatureId"],
                operation_type=op_type,
            )

            thicken.set_thickness(
                arguments["thickness"], variable_name=arguments.get("variableThickness")
            )

            if arguments.get("midplane"):
                thicken.set_midplane(True)

            if arguments.get("oppositeDirection"):
                thicken.set_opposite_direction(True)

            # Add feature to Part Studio
            feature_data = thicken.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )

            return [
                TextContent(
                    type="text",
                    text=f"Created thicken '{arguments.get('name', 'Thicken')}'. Feature ID: {result.get('feature', {}).get('featureId', result.get('featureId', 'unknown'))}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(
                f"API error creating thicken: {e.response.status_code} - {e.response.text[:500]}"
            )
            return [
                TextContent(
                    type="text",
                    text=f"Error creating thicken: API returned {e.response.status_code}. Check that the sketch feature ID is valid and parameters are correct.",
                )
            ]
        except KeyError:
            return [
                TextContent(
                    type="text",
                    text="Error creating thicken: Invalid operation type. Must be one of: NEW, ADD, REMOVE, INTERSECT.",
                )
            ]
        except ValueError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating thicken: {str(e)}",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating thicken")
            return [
                TextContent(
                    type="text",
                    text=f"Error creating thicken: {str(e)}\n\nPlease check the parameters and try again.",
                )
            ]

    elif name == "create_variable_studio":
        try:
            result = await variable_manager.create_variable_studio(
                arguments["documentId"], arguments["workspaceId"], arguments["name"]
            )
            element_id = result.get("id", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Created Variable Studio '{arguments['name']}'. Element ID: {element_id}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(
                f"API error creating variable studio: {e.response.status_code} - {e.response.text[:500]}"
            )
            return [
                TextContent(
                    type="text",
                    text=f"Error creating variable studio: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating variable studio")
            return [TextContent(type="text", text=f"Error creating variable studio: {str(e)}")]

    elif name == "get_variables":
        try:
            variables = await variable_manager.get_variables(
                arguments["documentId"], arguments["workspaceId"], arguments["elementId"]
            )

            var_list = "\n".join(
                [
                    f"- {var.name} = {var.expression}"
                    + (f" ({var.description})" if var.description else "")
                    for var in variables
                ]
            )

            return [
                TextContent(
                    type="text",
                    text=(
                        f"Variables in Variable Studio:\n{var_list}"
                        if var_list
                        else "No variables found"
                    ),
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(
                f"API error getting variables: {e.response.status_code} - {e.response.text[:500]}"
            )
            return [
                TextContent(
                    type="text",
                    text=f"Error getting variables: API returned {e.response.status_code}. Check that the document/workspace/element IDs are valid.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error getting variables")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting variables: {str(e)}",
                )
            ]

    elif name == "set_variable":
        try:
            result = await variable_manager.set_variable(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                arguments["name"],
                arguments["expression"],
                arguments.get("description"),
            )

            return [
                TextContent(
                    type="text",
                    text=f"Set variable '{arguments['name']}' = {arguments['expression']}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(
                f"API error setting variable: {e.response.status_code} - {e.response.text[:500]}"
            )
            return [
                TextContent(
                    type="text",
                    text=f"Error setting variable: API returned {e.response.status_code}. Check the variable expression format (e.g., '0.75 in').",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error setting variable")
            return [
                TextContent(
                    type="text",
                    text=f"Error setting variable: {str(e)}",
                )
            ]

    elif name == "get_features":
        try:
            features = await partstudio_manager.get_features(
                arguments["documentId"], arguments["workspaceId"], arguments["elementId"]
            )

            return [TextContent(type="text", text=f"Features data: {features}")]
        except httpx.HTTPStatusError as e:
            logger.error(
                f"API error getting features: {e.response.status_code} - {e.response.text[:500]}"
            )
            return [
                TextContent(
                    type="text",
                    text=f"Error getting features: API returned {e.response.status_code}. Check that the document/workspace/element IDs are valid.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error getting features")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting features: {str(e)}",
                )
            ]

    elif name == "delete_feature":
        try:
            element_type = arguments.get("elementType", "PARTSTUDIO")
            if element_type == "ASSEMBLY":
                result = await assembly_manager.delete_feature(
                    arguments["documentId"],
                    arguments["workspaceId"],
                    arguments["elementId"],
                    arguments["featureId"],
                )
            else:
                result = await partstudio_manager.delete_feature(
                    arguments["documentId"],
                    arguments["workspaceId"],
                    arguments["elementId"],
                    arguments["featureId"],
                )
            return [TextContent(type="text", text=f"Deleted feature {arguments['featureId']}")]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error deleting feature: API returned {e.response.status_code}",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error deleting feature: {str(e)}")]

    elif name == "list_documents":
        try:
            # Map filter type to API value
            filter_map = {"all": None, "owned": "1", "created": "4", "shared": "5"}
            filter_type = filter_map.get(arguments.get("filterType", "all"))

            documents = await document_manager.list_documents(
                filter_type=filter_type,
                sort_by=arguments.get("sortBy", "modifiedAt"),
                sort_order=arguments.get("sortOrder", "desc"),
                limit=arguments.get("limit", 20),
            )

            if not documents:
                return [TextContent(type="text", text="No documents found")]

            doc_list = "\n\n".join(
                [
                    f"**{doc.name}**\n"
                    f"  ID: {doc.id}\n"
                    f"  Modified: {doc.modified_at}\n"
                    f"  Owner: {doc.owner_name or doc.owner_id}"
                    + (f"\n  Description: {doc.description}" if doc.description else "")
                    for doc in documents
                ]
            )

            return [
                TextContent(type="text", text=f"Found {len(documents)} document(s):\n\n{doc_list}")
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error listing documents: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error listing documents: API returned {e.response.status_code}. Please check your API credentials.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error listing documents")
            return [
                TextContent(
                    type="text",
                    text=f"Error listing documents: {str(e)}",
                )
            ]

    elif name == "search_documents":
        try:
            documents = await document_manager.search_documents(
                query=arguments["query"], limit=arguments.get("limit", 20)
            )

            if not documents:
                return [
                    TextContent(
                        type="text", text=f"No documents found matching '{arguments['query']}'"
                    )
                ]

            doc_list = "\n\n".join(
                [
                    f"**{doc.name}**\n  ID: {doc.id}\n  Modified: {doc.modified_at}"
                    for doc in documents
                ]
            )

            return [
                TextContent(
                    type="text",
                    text=f"Found {len(documents)} document(s) matching '{arguments['query']}':\n\n{doc_list}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error searching documents: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error searching documents: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error searching documents")
            return [
                TextContent(
                    type="text",
                    text=f"Error searching documents: {str(e)}",
                )
            ]

    elif name == "get_document":
        try:
            doc = await document_manager.get_document(arguments["documentId"])

            return [
                TextContent(
                    type="text",
                    text=f"**{doc.name}**\n"
                    f"ID: {doc.id}\n"
                    f"Created: {doc.created_at}\n"
                    f"Modified: {doc.modified_at}\n"
                    f"Owner: {doc.owner_name or doc.owner_id}\n"
                    f"Public: {doc.public}"
                    + (f"\nDescription: {doc.description}" if doc.description else ""),
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error getting document: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting document: API returned {e.response.status_code}. Check that the document ID is valid.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error getting document")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting document: {str(e)}",
                )
            ]

    elif name == "get_document_summary":
        try:
            summary = await document_manager.get_document_summary(arguments["documentId"])

            doc = summary["document"]
            workspaces = summary["workspaces"]

            # Build summary text
            text_parts = [
                f"**{doc.name}**",
                f"ID: {doc.id}",
                f"Modified: {doc.modified_at}",
                "",
                f"Workspaces: {len(workspaces)}",
            ]

            for ws_detail in summary["workspace_details"]:
                ws = ws_detail["workspace"]
                elements = ws_detail["elements"]

                text_parts.append(f"\n**Workspace: {ws.name}**")
                text_parts.append(f"  ID: {ws.id}")
                text_parts.append(f"  Elements: {len(elements)}")

                if elements:
                    text_parts.append("  Element types:")
                    elem_types = {}
                    for elem in elements:
                        elem_types[elem.element_type] = elem_types.get(elem.element_type, 0) + 1

                    for elem_type, count in elem_types.items():
                        text_parts.append(f"    - {elem_type}: {count}")

            return [TextContent(type="text", text="\n".join(text_parts))]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error getting document summary: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting document summary: API returned {e.response.status_code}. Check that the document ID is valid.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error getting document summary")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting document summary: {str(e)}",
                )
            ]

    elif name == "find_part_studios":
        try:
            part_studios = await document_manager.find_part_studios(
                arguments["documentId"],
                arguments["workspaceId"],
                name_pattern=arguments.get("namePattern"),
            )

            if not part_studios:
                pattern_msg = (
                    f" matching '{arguments['namePattern']}'"
                    if arguments.get("namePattern")
                    else ""
                )
                return [TextContent(type="text", text=f"No Part Studios found{pattern_msg}")]

            ps_list = "\n".join([f"- **{ps.name}** (ID: {ps.id})" for ps in part_studios])

            pattern_msg = (
                f" matching '{arguments['namePattern']}'" if arguments.get("namePattern") else ""
            )
            return [
                TextContent(
                    type="text",
                    text=f"Found {len(part_studios)} Part Studio(s){pattern_msg}:\n\n{ps_list}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error finding part studios: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error finding part studios: API returned {e.response.status_code}. Check that the document/workspace IDs are valid.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error finding part studios")
            return [
                TextContent(
                    type="text",
                    text=f"Error finding part studios: {str(e)}",
                )
            ]

    elif name == "get_parts":
        try:
            parts = await partstudio_manager.get_parts(
                arguments["documentId"], arguments["workspaceId"], arguments["elementId"]
            )

            if not parts:
                return [TextContent(type="text", text="No parts found in Part Studio")]

            parts_list = []
            for i, part in enumerate(parts, 1):
                part_info = f"**Part {i}: {part.get('name', 'Unnamed')}**"
                if "partId" in part:
                    part_info += f"\n  Part ID: {part['partId']}"
                if "bodyType" in part:
                    part_info += f"\n  Body Type: {part['bodyType']}"
                if "state" in part:
                    part_info += f"\n  State: {part['state']}"
                parts_list.append(part_info)

            return [
                TextContent(
                    type="text", text=f"Found {len(parts)} part(s):\n\n" + "\n\n".join(parts_list)
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error getting parts: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting parts: API returned {e.response.status_code}. Check that the document/workspace/element IDs are valid.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error getting parts")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting parts: {str(e)}",
                )
            ]

    elif name == "get_elements":
        try:
            elements = await document_manager.get_elements(
                arguments["documentId"],
                arguments["workspaceId"],
                element_type=arguments.get("elementType"),
            )

            if not elements:
                type_msg = (
                    f" of type '{arguments['elementType']}'" if arguments.get("elementType") else ""
                )
                return [TextContent(type="text", text=f"No elements found{type_msg}")]

            elem_list = []
            for elem in elements:
                elem_info = f"**{elem.name}**"
                elem_info += f"\n  ID: {elem.id}"
                elem_info += f"\n  Type: {elem.element_type}"
                if elem.data_type:
                    elem_info += f"\n  Data Type: {elem.data_type}"
                elem_list.append(elem_info)

            type_msg = (
                f" of type '{arguments['elementType']}'" if arguments.get("elementType") else ""
            )
            return [
                TextContent(
                    type="text",
                    text=f"Found {len(elements)} element(s){type_msg}:\n\n"
                    + "\n\n".join(elem_list),
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error getting elements: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting elements: API returned {e.response.status_code}. Check that the document/workspace IDs are valid.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error getting elements")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting elements: {str(e)}",
                )
            ]

    elif name == "get_assembly":
        try:
            assembly_data = await assembly_manager.get_assembly_definition(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
            )

            root_assembly = assembly_data.get("rootAssembly", {})
            instances = root_assembly.get("instances", [])

            if not instances:
                return [TextContent(type="text", text="No instances found in assembly")]

            instance_list = []
            for i, instance in enumerate(instances, 1):
                inst_info = f"**Instance {i}: {instance.get('name', 'Unnamed')}**"
                inst_info += f"\n  ID: {instance.get('id', 'N/A')}"
                inst_info += f"\n  Type: {instance.get('type', 'N/A')}"
                if "partId" in instance:
                    inst_info += f"\n  Part ID: {instance['partId']}"
                if "elementId" in instance:
                    inst_info += f"\n  Element ID: {instance['elementId']}"
                if "suppressed" in instance:
                    inst_info += f"\n  Suppressed: {instance['suppressed']}"
                instance_list.append(inst_info)

            return [
                TextContent(
                    type="text",
                    text=(
                        f"Assembly Structure:\n\n"
                        f"Found {len(instances)} instance(s):\n\n" + "\n\n".join(instance_list)
                    ),
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error getting assembly: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting assembly: API returned {e.response.status_code}. Check that the document/workspace/element IDs are valid.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error getting assembly")
            return [
                TextContent(
                    type="text",
                    text=f"Error getting assembly: {str(e)}",
                )
            ]

    elif name == "create_document":
        try:
            doc = await document_manager.create_document(
                name=arguments["name"],
                description=arguments.get("description"),
                is_public=arguments.get("isPublic", True),
            )

            return [
                TextContent(
                    type="text",
                    text=f"Created document '{doc.name}'\n"
                    f"Document ID: {doc.id}\n"
                    f"Use this ID with other commands to work with this document.",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error creating document: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error creating document: API returned {e.response.status_code}. Check your API credentials and permissions.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating document")
            return [
                TextContent(
                    type="text",
                    text=f"Error creating document: {str(e)}",
                )
            ]

    elif name == "copy_workspace_to_document":
        try:
            result = await document_manager.copy_workspace_to_document(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                new_name=arguments["newName"],
                is_public=arguments.get("isPublic", True),
            )

            new_doc_id = result.get("newDocumentId", "unknown")
            new_doc_name = result.get("newDocumentName", arguments["newName"])
            new_ws_id = result.get("newWorkspaceId", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Copied workspace '{arguments['workspaceId']}' to new document "
                    f"'{new_doc_name}'\n"
                    f"New document ID: {new_doc_id}\n"
                    f"New workspace ID: {new_ws_id}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error copying workspace: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error copying workspace: API returned {e.response.status_code}. Check your API credentials and permissions.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error copying workspace")
            return [
                TextContent(
                    type="text",
                    text=f"Error copying workspace: {str(e)}",
                )
            ]

    elif name == "delete_workspace":
        try:
            await document_manager.delete_workspace(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
            )
            return [
                TextContent(
                    type="text",
                    text=f"Deleted workspace '{arguments['workspaceId']}' from document "
                    f"'{arguments['documentId']}'.",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error deleting workspace: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error deleting workspace: API returned {e.response.status_code}. The main workspace cannot be deleted.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error deleting workspace")
            return [
                TextContent(
                    type="text",
                    text=f"Error deleting workspace: {str(e)}",
                )
            ]

    elif name == "create_part_studio":
        try:
            result = await partstudio_manager.create_part_studio(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                name=arguments["name"],
            )

            element_id = result.get("id", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Created Part Studio '{arguments['name']}'\n"
                    f"Element ID: {element_id}\n"
                    f"Use this ID with sketch and feature commands.",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error creating Part Studio: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error creating Part Studio: API returned {e.response.status_code}. Check the document/workspace IDs.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating Part Studio")
            return [
                TextContent(
                    type="text",
                    text=f"Error creating Part Studio: {str(e)}",
                )
            ]

    elif name == "create_assembly":
        try:
            result = await assembly_manager.create_assembly(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                name=arguments["name"],
            )
            element_id = result.get("id", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Created Assembly '{arguments['name']}'\nElement ID: {element_id}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error creating assembly: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error creating assembly: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating assembly")
            return [TextContent(type="text", text=f"Error creating assembly: {str(e)}")]

    elif name == "add_assembly_instance":
        try:
            result = await assembly_manager.add_instance(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                part_studio_element_id=arguments["partStudioElementId"],
                part_id=arguments.get("partId"),
                is_assembly=arguments.get("isAssembly", False),
            )
            instance_id = result.get("id")
            instance_name = result.get("name")
            if not instance_id:
                definition = await assembly_manager.get_assembly_definition(
                    arguments["documentId"],
                    arguments["workspaceId"],
                    arguments["elementId"],
                )
                expected_type = "Assembly" if arguments.get("isAssembly", False) else "Part"
                candidates = [
                    instance
                    for instance in definition.get("rootAssembly", {}).get("instances", [])
                    if instance.get("type") == expected_type
                    and instance.get("elementId") == arguments["partStudioElementId"]
                    and (
                        expected_type == "Assembly"
                        or arguments.get("partId") is None
                        or instance.get("partId") == arguments.get("partId")
                    )
                ]
                if candidates:
                    instance_id = candidates[-1].get("id")
                    instance_name = candidates[-1].get("name")
            instance_id = instance_id or "unknown"
            instance_name = instance_name or "unnamed"
            return [
                TextContent(
                    type="text",
                    text=f"Added instance '{instance_name}' to assembly.\nInstance ID: {instance_id}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error adding instance: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error adding instance: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error adding instance")
            return [TextContent(type="text", text=f"Error adding instance: {str(e)}")]

    elif name == "transform_instance":
        try:
            transform = build_transform_matrix(
                tx=arguments.get("translateX", 0),
                ty=arguments.get("translateY", 0),
                tz=arguments.get("translateZ", 0),
                rx=arguments.get("rotateX", 0),
                ry=arguments.get("rotateY", 0),
                rz=arguments.get("rotateZ", 0),
            )
            occurrences = [{"path": [arguments["instanceId"]], "transform": transform}]
            await assembly_manager.transform_occurrences(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                occurrences=occurrences,
            )
            return [
                TextContent(type="text", text=f"Transformed instance {arguments['instanceId']}.")
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error transforming instance: {e.response.status_code}")
            return [
                TextContent(
                    type="text",
                    text=f"Error transforming instance: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error transforming instance")
            return [TextContent(type="text", text=f"Error transforming instance: {str(e)}")]

    elif name == "create_fastened_mate":
        try:
            mate_name = arguments.get("name", "Fastened mate")
            feature_id = await _create_mate(
                assembly_manager,
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                arguments["firstInstanceId"],
                arguments["secondInstanceId"],
                arguments["firstFaceId"],
                arguments["secondFaceId"],
                mate_name,
                MateType.FASTENED,
                first_offset=_extract_offsets(arguments, "first"),
                second_offset=_extract_offsets(arguments, "second"),
            )
            return [
                TextContent(
                    type="text",
                    text=f"Created fastened mate '{mate_name}'. Feature ID: {feature_id}",
                )
            ]
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.text[:500]
            except Exception:
                pass
            logger.error(f"API error creating mate: {e.response.status_code} - {error_body}")
            return [
                TextContent(
                    type="text",
                    text=f"Error creating mate: API returned {e.response.status_code}. Details: {error_body}",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating mate")
            return [TextContent(type="text", text=f"Error creating mate: {str(e)}")]

    elif name == "create_revolute_mate":
        try:
            mate_name = arguments.get("name", "Revolute mate")
            feature_id = await _create_mate(
                assembly_manager,
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                arguments["firstInstanceId"],
                arguments["secondInstanceId"],
                arguments["firstFaceId"],
                arguments["secondFaceId"],
                mate_name,
                MateType.REVOLUTE,
                min_limit=arguments.get("minLimit"),
                max_limit=arguments.get("maxLimit"),
                first_offset=_extract_offsets(arguments, "first"),
                second_offset=_extract_offsets(arguments, "second"),
            )
            return [
                TextContent(
                    type="text",
                    text=f"Created revolute mate '{mate_name}'. Feature ID: {feature_id}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error creating mate: {e.response.status_code}")
            return [
                TextContent(
                    type="text", text=f"Error creating mate: API returned {e.response.status_code}."
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating mate")
            return [TextContent(type="text", text=f"Error creating mate: {str(e)}")]

    elif name == "create_slider_mate":
        try:
            mate_name = arguments.get("name", "Slider mate")
            feature_id = await _create_mate(
                assembly_manager,
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                arguments["firstInstanceId"],
                arguments["secondInstanceId"],
                arguments["firstFaceId"],
                arguments["secondFaceId"],
                mate_name,
                MateType.SLIDER,
                min_limit=arguments.get("minLimit"),
                max_limit=arguments.get("maxLimit"),
                first_offset=_extract_offsets(arguments, "first"),
                second_offset=_extract_offsets(arguments, "second"),
            )
            return [
                TextContent(
                    type="text", text=f"Created slider mate '{mate_name}'. Feature ID: {feature_id}"
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error creating mate: {e.response.status_code}")
            return [
                TextContent(
                    type="text", text=f"Error creating mate: API returned {e.response.status_code}."
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating mate")
            return [TextContent(type="text", text=f"Error creating mate: {str(e)}")]

    elif name == "create_cylindrical_mate":
        try:
            mate_name = arguments.get("name", "Cylindrical mate")
            feature_id = await _create_mate(
                assembly_manager,
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                arguments["firstInstanceId"],
                arguments["secondInstanceId"],
                arguments["firstFaceId"],
                arguments["secondFaceId"],
                mate_name,
                MateType.CYLINDRICAL,
                min_limit=arguments.get("minLimit"),
                max_limit=arguments.get("maxLimit"),
                first_offset=_extract_offsets(arguments, "first"),
                second_offset=_extract_offsets(arguments, "second"),
            )
            return [
                TextContent(
                    type="text",
                    text=f"Created cylindrical mate '{mate_name}'. Feature ID: {feature_id}",
                )
            ]
        except httpx.HTTPStatusError as e:
            logger.error(f"API error creating mate: {e.response.status_code}")
            return [
                TextContent(
                    type="text", text=f"Error creating mate: API returned {e.response.status_code}."
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating mate")
            return [TextContent(type="text", text=f"Error creating mate: {str(e)}")]

    elif name == "create_mate_connector":
        try:
            mc = MateConnectorBuilder(
                name=arguments.get("name", "Mate connector"),
                face_id=arguments["faceId"],
                occurrence_path=[arguments["instanceId"]],
            )
            if arguments.get("flipPrimary"):
                mc.set_flip_primary(True)
            secondary = arguments.get("secondaryAxisType")
            if secondary and secondary != "PLUS_X":
                mc.set_secondary_axis(secondary)
            ox = arguments.get("offsetX", 0)
            oy = arguments.get("offsetY", 0)
            oz = arguments.get("offsetZ", 0)
            if ox != 0 or oy != 0 or oz != 0:
                mc.set_translation(ox, oy, oz)
            result = await assembly_manager.add_feature(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                feature_data=mc.build(),
            )
            feature_id = result.get("feature", {}).get("featureId", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Created mate connector '{arguments.get('name', 'Mate connector')}' on instance {arguments['instanceId']}. Feature ID: {feature_id}",
                )
            ]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid input: {str(e)}")]
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.text[:500]
            except Exception:
                pass
            logger.error(
                f"API error creating mate connector: {e.response.status_code} - {error_body}"
            )
            return [
                TextContent(
                    type="text",
                    text=f"Error creating mate connector: API returned {e.response.status_code}. Details: {error_body}",
                )
            ]
        except Exception as e:
            logger.exception("Unexpected error creating mate connector")
            return [TextContent(type="text", text=f"Error creating mate connector: {str(e)}")]

    elif name == "create_sketch_circle":
        try:
            plane_name = arguments.get("plane", "Front")
            plane = SketchPlane[plane_name.upper()]
            plane_id = await partstudio_manager.get_plane_id(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                plane_name,
            )
            sketch = SketchBuilder(
                name=arguments.get("name", "Sketch"), plane=plane, plane_id=plane_id
            )
            sketch.add_circle(
                center=(arguments.get("centerX", 0), arguments.get("centerY", 0)),
                radius=arguments["radius"],
            )
            feature_data = sketch.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get("featureId", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Created sketch with circle on {plane_name} plane. Feature ID: {feature_id}",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating sketch circle: {str(e)}")]

    elif name == "create_sketch_line":
        try:
            plane_name = arguments.get("plane", "Front")
            plane = SketchPlane[plane_name.upper()]
            plane_id = await partstudio_manager.get_plane_id(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                plane_name,
            )
            sketch = SketchBuilder(
                name=arguments.get("name", "Sketch"), plane=plane, plane_id=plane_id
            )
            sketch.add_line(
                start=tuple(arguments["startPoint"]),
                end=tuple(arguments["endPoint"]),
            )
            feature_data = sketch.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get("featureId", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Created sketch with line on {plane_name} plane. Feature ID: {feature_id}",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating sketch line: {str(e)}")]

    elif name == "create_sketch_arc":
        try:
            plane_name = arguments.get("plane", "Front")
            plane = SketchPlane[plane_name.upper()]
            plane_id = await partstudio_manager.get_plane_id(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                plane_name,
            )
            sketch = SketchBuilder(
                name=arguments.get("name", "Sketch"), plane=plane, plane_id=plane_id
            )
            sketch.add_arc(
                center=(arguments.get("centerX", 0), arguments.get("centerY", 0)),
                radius=arguments["radius"],
                start_angle=arguments.get("startAngle", 0),
                end_angle=arguments.get("endAngle", 180),
            )
            feature_data = sketch.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get("featureId", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Created sketch with arc on {plane_name} plane. Feature ID: {feature_id}",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating sketch arc: {str(e)}")]

    elif name == "create_fillet":
        try:
            fillet = FilletBuilder(name=arguments.get("name", "Fillet"), radius=arguments["radius"])
            for edge_id in arguments["edgeIds"]:
                fillet.add_edge(edge_id)
            if arguments.get("variableRadius"):
                fillet.set_radius(arguments["radius"], variable_name=arguments["variableRadius"])
            feature_data = fillet.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get(
                "featureId", result.get("featureId", "unknown")
            )
            return [TextContent(type="text", text=f"Created fillet. Feature ID: {feature_id}")]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating fillet: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating fillet: {str(e)}")]

    elif name == "create_chamfer":
        try:
            chamfer_type = ChamferType[arguments.get("chamferType", "EQUAL_OFFSETS")]
            chamfer = ChamferBuilder(
                name=arguments.get("name", "Chamfer"),
                distance=arguments["distance"],
                chamfer_type=chamfer_type,
            )
            for edge_id in arguments["edgeIds"]:
                chamfer.add_edge(edge_id)
            if arguments.get("variableDistance"):
                chamfer.set_distance(
                    arguments["distance"], variable_name=arguments["variableDistance"]
                )
            feature_data = chamfer.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get(
                "featureId", result.get("featureId", "unknown")
            )
            return [TextContent(type="text", text=f"Created chamfer. Feature ID: {feature_id}")]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating chamfer: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating chamfer: {str(e)}")]

    elif name == "create_revolve":
        try:
            op_type = RevolveType[arguments.get("operationType", "NEW")]
            revolve = RevolveBuilder(
                name=arguments.get("name", "Revolve"),
                sketch_feature_id=arguments["sketchFeatureId"],
                axis=arguments.get("axis", "Y"),
                angle=arguments.get("angle", 360.0),
                operation_type=op_type,
            )
            axis_edge_id = await _create_axis_edge(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                arguments.get("axis", "Y"),
            )
            feature_data = revolve.build(axis_edge_id=axis_edge_id)
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get(
                "featureId", result.get("featureId", "unknown")
            )
            return [TextContent(type="text", text=f"Created revolve. Feature ID: {feature_id}")]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating revolve: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating revolve: {str(e)}")]

    elif name == "create_linear_pattern":
        try:
            pattern = LinearPatternBuilder(
                name=arguments.get("name", "Linear pattern"),
                distance=arguments["distance"],
                count=arguments.get("count", 2),
            )
            for fid in arguments["featureIds"]:
                pattern.add_feature(fid)
            pattern.set_direction(arguments.get("direction", "X"))
            if arguments.get("reapplyFeatures"):
                pattern.set_reapply_features(True)
            feature_data = pattern.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get(
                "featureId", result.get("featureId", "unknown")
            )
            return [
                TextContent(type="text", text=f"Created linear pattern. Feature ID: {feature_id}")
            ]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating pattern: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating pattern: {str(e)}")]

    elif name == "create_circular_pattern":
        try:
            pattern = CircularPatternBuilder(
                name=arguments.get("name", "Circular pattern"),
                count=arguments["count"],
            )
            pattern.set_angle(arguments.get("angle", 360.0))
            pattern.set_axis(arguments.get("axis", "Z"))
            if arguments.get("reapplyFeatures"):
                pattern.set_reapply_features(True)
            for fid in arguments["featureIds"]:
                pattern.add_feature(fid)
            axis_edge_id = await _create_axis_edge(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                arguments.get("axis", "Z"),
            )
            feature_data = pattern.build(axis_edge_id=axis_edge_id)
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get(
                "featureId", result.get("featureId", "unknown")
            )
            return [
                TextContent(type="text", text=f"Created circular pattern. Feature ID: {feature_id}")
            ]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating pattern: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating pattern: {str(e)}")]

    elif name == "create_boolean":
        try:
            bool_type = BooleanType[arguments["booleanType"]]
            boolean = BooleanBuilder(name=arguments.get("name", "Boolean"), boolean_type=bool_type)
            for body_id in arguments["toolBodyIds"]:
                boolean.add_tool_body(body_id)
            for body_id in arguments.get("targetBodyIds", []):
                boolean.add_target_body(body_id)
            feature_data = boolean.build()
            result = await partstudio_manager.add_feature(
                arguments["documentId"],
                arguments["workspaceId"],
                arguments["elementId"],
                feature_data,
            )
            feature_id = result.get("feature", {}).get(
                "featureId", result.get("featureId", "unknown")
            )
            return [
                TextContent(
                    type="text",
                    text=f"Created boolean {arguments['booleanType'].lower()}. Feature ID: {feature_id}",
                )
            ]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error creating boolean: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating boolean: {str(e)}")]

    elif name == "eval_featurescript":
        try:
            result = await featurescript_manager.evaluate(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                script=arguments["script"],
            )
            import json

            return [
                TextContent(
                    type="text", text=f"FeatureScript result:\n{json.dumps(result, indent=2)}"
                )
            ]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error evaluating FeatureScript: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error evaluating FeatureScript: {str(e)}")]

    elif name == "get_bounding_box":
        try:
            result = await featurescript_manager.get_bounding_box(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
            )
            import json

            return [TextContent(type="text", text=f"Bounding box:\n{json.dumps(result, indent=2)}")]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error getting bounding box: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error getting bounding box: {str(e)}")]

    elif name == "export_part_studio":
        try:
            result = await export_manager.export_part_studio(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                format_name=arguments.get("format", "STL"),
                part_id=arguments.get("partId"),
            )
            translation_id = result.get("id", "unknown")
            state = result.get("requestState", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Export started. Translation ID: {translation_id}\nState: {state}",
                )
            ]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text", text=f"Error exporting: API returned {e.response.status_code}."
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error exporting: {str(e)}")]

    elif name == "capture_part_studio_screenshot":
        try:
            result = await visuals_manager.capture_part_studio(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                view=arguments.get("view", "iso"),
                output_width=arguments.get("outputWidth", 800),
                output_height=arguments.get("outputHeight", 600),
                show_all_parts=arguments.get("showAllParts", True),
                output_path=arguments.get("outputPath"),
            )
            content: list[Any] = [
                ImageContent(type="image", data=result["data"], mimeType=result["mimeType"])
            ]
            if result.get("path"):
                content.append(TextContent(type="text", text=f"Saved to {result['path']}"))
            return content
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error capturing screenshot: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error capturing screenshot: {str(e)}")]

    elif name == "capture_assembly_screenshot":
        try:
            result = await visuals_manager.capture_assembly(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                view=arguments.get("view", "iso"),
                output_width=arguments.get("outputWidth", 800),
                output_height=arguments.get("outputHeight", 600),
                show_all_parts=arguments.get("showAllParts", True),
                output_path=arguments.get("outputPath"),
            )
            content: list[Any] = [
                ImageContent(type="image", data=result["data"], mimeType=result["mimeType"])
            ]
            if result.get("path"):
                content.append(TextContent(type="text", text=f"Saved to {result['path']}"))
            return content
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error capturing screenshot: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error capturing screenshot: {str(e)}")]

    elif name == "export_assembly":
        try:
            result = await export_manager.export_assembly(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                format_name=arguments.get("format", "STL"),
            )
            translation_id = result.get("id", "unknown")
            state = result.get("requestState", "unknown")
            return [
                TextContent(
                    type="text",
                    text=f"Export started. Translation ID: {translation_id}\nState: {state}",
                )
            ]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text", text=f"Error exporting: API returned {e.response.status_code}."
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error exporting: {str(e)}")]

    elif name == "check_assembly_interference":
        try:
            result = await check_assembly_interference(
                assembly_manager=assembly_manager,
                partstudio_manager=partstudio_manager,
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
            )
            return [TextContent(type="text", text=format_interference_result(result))]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error checking interference: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error checking interference: {str(e)}")]

    elif name == "get_assembly_positions":
        try:
            report = await get_assembly_positions(
                assembly_manager=assembly_manager,
                partstudio_manager=partstudio_manager,
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
            )
            return [TextContent(type="text", text=report)]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error getting positions: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error getting positions: {str(e)}")]

    elif name == "set_instance_position":
        try:
            msg = await set_absolute_position(
                assembly_manager=assembly_manager,
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                instance_id=arguments["instanceId"],
                x_inches=arguments["x"],
                y_inches=arguments["y"],
                z_inches=arguments["z"],
            )
            return [TextContent(type="text", text=msg)]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error setting position: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error setting position: {str(e)}")]

    elif name == "align_instance_to_face":
        try:
            msg = await align_to_face(
                assembly_manager=assembly_manager,
                partstudio_manager=partstudio_manager,
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                source_instance_id=arguments["sourceInstanceId"],
                target_instance_id=arguments["targetInstanceId"],
                face=arguments["face"],
            )
            return [TextContent(type="text", text=msg)]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error aligning instance: API returned {e.response.status_code}.",
                )
            ]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid input: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error aligning instance: {str(e)}")]

    elif name == "get_body_details":
        try:
            result = await partstudio_manager.get_body_details(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
            )

            bodies = result.get("bodies", [])
            if not bodies:
                return [TextContent(type="text", text="No bodies found in Part Studio.")]

            output_parts = []
            for body in bodies:
                body_id = body.get("id", "N/A")
                body_type = body.get("type", "N/A")

                faces = body.get("faces", [])

                # Collect planar face data for enrichment
                planar_data = []
                for face in faces:
                    surface = face.get("surface", {})
                    if surface.get("type", "").lower() == "plane":
                        normal = surface.get("normal", {})
                        origin = surface.get("origin", {})
                        planar_data.append(
                            {
                                "id": face.get("id", "N/A"),
                                "nx": normal.get("x", 0),
                                "ny": normal.get("y", 0),
                                "nz": normal.get("z", 0),
                                "ox": origin.get("x", 0),
                                "oy": origin.get("y", 0),
                                "oz": origin.get("z", 0),
                            }
                        )

                enriched = _enrich_rectangular_body(planar_data)

                if enriched:
                    dims = enriched["dimensions"]
                    part_header = f"**Body: {body_id}** (type: {body_type})"
                    part_header += f'\n  Bounding box: {dims[0]:.3f}" x {dims[1]:.3f}" x {dims[2]:.3f}" (X x Y x Z)'

                    faces_info = []
                    for face in faces:
                        face_id = face.get("id", "N/A")
                        surface = face.get("surface", {})
                        surface_type = surface.get("type", "unknown")

                        if face_id in enriched["faces"]:
                            ef = enriched["faces"][face_id]
                            face_line = f"  Face `{face_id}`: {surface_type}"
                            face_line += f" | {ef['label']} face"
                            face_line += f' | {ef["width"]:.2f}" x {ef["height"]:.2f}"'
                            face_line += f" | outward normal={ef['outward_normal']}"
                        else:
                            face_line = f"  Face `{face_id}`: {surface_type}"

                        faces_info.append(face_line)

                    faces_info.append("")
                    faces_info.append(
                        "  MC axes: Z=outward normal, X=world+X (for Y/Z faces). "
                        "offsetZ>0 moves AWAY from body, <0 moves INTO body."
                    )

                    output_parts.append(part_header + "\n" + "\n".join(faces_info))
                else:
                    # Fallback to original format for non-rectangular bodies
                    part_header = f"**Body: {body_id}** (type: {body_type})"
                    faces_info = []
                    for face in faces:
                        face_id = face.get("id", "N/A")
                        surface = face.get("surface", {})
                        surface_type = surface.get("type", "unknown")
                        face_line = f"  Face `{face_id}`: {surface_type}"
                        stype_lower = surface_type.lower()
                        if stype_lower == "plane":
                            normal = surface.get("normal", {})
                            origin = surface.get("origin", {})
                            nx = normal.get("x", 0)
                            ny = normal.get("y", 0)
                            nz = normal.get("z", 0)
                            ox = origin.get("x", 0)
                            oy = origin.get("y", 0)
                            oz = origin.get("z", 0)
                            face_line += f" | normal=({nx:.4f}, {ny:.4f}, {nz:.4f})"
                            face_line += f" | origin=({ox:.6f}, {oy:.6f}, {oz:.6f})m"
                        elif stype_lower == "cylinder":
                            radius = surface.get("radius", 0)
                            face_line += f" | radius={radius:.6f}m"
                        faces_info.append(face_line)
                    output_parts.append(part_header + "\n" + "\n".join(faces_info))

            return [
                TextContent(
                    type="text",
                    text=f"Body Details ({len(bodies)} bodies):\n\n" + "\n\n".join(output_parts),
                )
            ]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error getting body details: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error getting body details: {str(e)}")]

    elif name == "get_assembly_features":
        try:
            result = await assembly_manager.get_features(
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
            )

            features = result.get("features", [])
            feature_states = result.get("featureStates", {})

            if not features:
                return [TextContent(type="text", text="No features found in assembly.")]

            feature_lines = []
            for i, feat in enumerate(features, 1):
                feat_type = feat.get("typeName", feat.get("btType", "unknown"))
                feat_id = feat.get("featureId", "N/A")
                feat_name = feat.get("name", "Unnamed")
                state_info = feature_states.get(feat_id, {})
                state = state_info.get("featureStatus", "UNKNOWN")

                line = f"{i}. **{feat_name}** ({feat_type})"
                line += f"\n   ID: `{feat_id}` | State: {state}"

                # For mates, show mate type
                if feat_type == "mate" or feat.get("btType") == "BTMMate-64":
                    params = feat.get("parameters", [])
                    for p in params:
                        if p.get("parameterId") == "mateType":
                            line += f" | Type: {p.get('value', 'N/A')}"
                            break

                feature_lines.append(line)

            return [
                TextContent(
                    type="text",
                    text=f"Assembly Features ({len(features)}):\n\n" + "\n\n".join(feature_lines),
                )
            ]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error getting assembly features: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error getting assembly features: {str(e)}")]

    elif name == "get_face_coordinate_system":
        try:
            from .analysis.face_cs import query_face_coordinate_system

            cs = await query_face_coordinate_system(
                assembly_manager=assembly_manager,
                document_id=arguments["documentId"],
                workspace_id=arguments["workspaceId"],
                element_id=arguments["elementId"],
                instance_id=arguments["instanceId"],
                face_id=arguments["faceId"],
            )

            ox, oy, oz = cs.origin_inches
            zx, zy, zz = cs.z_axis
            xx, xy, xz = cs.x_axis
            yx, yy, yz = cs.y_axis

            text = (
                f"Face `{arguments['faceId']}` coordinate system on instance `{arguments['instanceId']}`:\n\n"
                f"  Origin: ({ox:.4f}, {oy:.4f}, {oz:.4f}) inches\n"
                f"  Z-axis (outward normal): ({zx:.6f}, {zy:.6f}, {zz:.6f})\n"
                f"  X-axis: ({xx:.6f}, {xy:.6f}, {xz:.6f})\n"
                f"  Y-axis: ({yx:.6f}, {yy:.6f}, {yz:.6f})"
            )
            return [TextContent(type="text", text=text)]
        except RuntimeError as e:
            return [TextContent(type="text", text=f"Error querying face CS: {str(e)}")]
        except httpx.HTTPStatusError as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error querying face CS: API returned {e.response.status_code}.",
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error querying face CS: {str(e)}")]

    else:
        raise ValueError(f"Unknown tool: {name}")


async def main_stdio():
    """Run the MCP server with stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


# Bearer token required for the SSE endpoint. Set via MCP_AUTH_TOKEN (loaded from
# .env). Empty means the endpoint is open.
_MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "").strip()


def _request_is_authorized(scope) -> bool:
    """Return True if the request carries the configured bearer token.

    The token may be supplied as a ``?token=`` query parameter (what ChatGPT's
    URL-only connector supports) or an ``Authorization: Bearer`` header. When
    ``MCP_AUTH_TOKEN`` is unset, requests are allowed (open endpoint).
    """
    if not _MCP_AUTH_TOKEN:
        return True
    qs = parse_qs(scope.get("query_string", b"").decode("utf-8"))
    token = (qs.get("token") or [""])[0]
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
    }
    auth = headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return hmac.compare_digest(token, _MCP_AUTH_TOKEN) or hmac.compare_digest(
        bearer, _MCP_AUTH_TOKEN
    )


async def _reject(scope, receive, send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [[b"content-type", b"text/plain"]],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_sse_app():
    """Create SSE ASGI application."""
    from mcp.server.sse import SseServerTransport

    sse = SseServerTransport("/messages")

    async def app_logic(scope, receive, send):
        """Main ASGI app logic."""
        if scope["type"] == "http":
            path = scope["path"]

            if path == "/sse":
                if not _request_is_authorized(scope):
                    await _reject(scope, receive, send, 401, b"Unauthorized")
                    return
                # Handle SSE endpoint
                async with sse.connect_sse(scope, receive, send) as streams:
                    await app.run(streams[0], streams[1], app.create_initialization_options())
            elif path == "/messages" and scope["method"] == "POST":
                # Handle POST messages endpoint
                await sse.handle_post_message(scope, receive, send)
            else:
                # 404 for other paths
                await send(
                    {
                        "type": "http.response.start",
                        "status": 404,
                        "headers": [[b"content-type", b"text/plain"]],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Not Found",
                    }
                )

    return app_logic


# Create module-level SSE app for uvicorn reload
sse_app = create_sse_app()


def create_streamable_http_app():
    """Create a Starlette app serving MCP over Streamable HTTP.

    This is the transport ChatGPT's connector speaks: a single endpoint
    accepting POST JSON-RPC (and GET for server-initiated SSE streams), instead
    of the legacy split ``/sse`` + ``/messages`` transport.
    """
    import contextlib

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    manager = StreamableHTTPSessionManager(app)

    async def mcp_endpoint(scope, receive, send):
        if not _request_is_authorized(scope):
            await _reject(scope, receive, send, 401, b"Unauthorized")
            return
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    return Starlette(
        routes=[Mount("/mcp", app=mcp_endpoint), Mount("/", app=mcp_endpoint)],
        lifespan=lifespan,
    )


# Module-level Streamable HTTP app for uvicorn
http_app = create_streamable_http_app()


def main():
    """Main entry point - run stdio by default."""
    import uvicorn

    # Get port from args or env
    port = 3000
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    port = int(os.getenv("MCP_PORT", port))

    # Check if reload is requested
    reload = "--reload" in sys.argv or os.getenv("MCP_RELOAD") == "true"

    # Streamable HTTP transport (what ChatGPT's connector speaks)
    if "--http" in sys.argv or os.getenv("MCP_TRANSPORT") == "http":
        print(f"Starting Onshape MCP server over Streamable HTTP on port {port}", file=sys.stderr)
        uvicorn.run(http_app, host="127.0.0.1", port=port)
    # Legacy SSE transport
    elif "--sse" in sys.argv or os.getenv("MCP_TRANSPORT") == "sse":
        print(f"Starting Onshape MCP server in SSE mode on port {port}", file=sys.stderr)
        if reload:
            print("Auto-reload enabled - server will restart on code changes", file=sys.stderr)
            # When using reload, we need to pass the module path string
            # and uvicorn will import and re-import on changes
            uvicorn.run(
                "onshape_mcp.server:sse_app",
                host="127.0.0.1",
                port=port,
                reload=True,
                reload_dirs=["./onshape_mcp"],
            )
        else:
            # Without reload, pass the app instance directly
            uvicorn.run(sse_app, host="127.0.0.1", port=port)
    else:
        # Default to stdio
        asyncio.run(main_stdio())


if __name__ == "__main__":
    main()
