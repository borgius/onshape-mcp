"""Release checks for the consolidated MCP tool surface."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mcp.types import Tool

from .tools.consolidated import consolidated_tools

LEGACY_TOOL_NAMES = frozenset(
    {
        "create_sketch_rectangle",
        "create_extrude",
        "create_thicken",
        "create_variable_studio",
        "get_variables",
        "set_variable",
        "get_features",
        "delete_feature",
        "list_documents",
        "search_documents",
        "get_document",
        "get_document_summary",
        "find_part_studios",
        "get_parts",
        "get_elements",
        "get_assembly",
        "create_document",
        "copy_workspace_to_document",
        "delete_workspace",
        "create_part_studio",
        "create_assembly",
        "add_assembly_instance",
        "transform_instance",
        "create_fastened_mate",
        "create_revolute_mate",
        "create_slider_mate",
        "create_cylindrical_mate",
        "create_mate_connector",
        "create_sketch_circle",
        "create_sketch_line",
        "create_sketch_arc",
        "create_fillet",
        "create_chamfer",
        "create_revolve",
        "create_linear_pattern",
        "create_circular_pattern",
        "create_boolean",
        "eval_featurescript",
        "get_bounding_box",
        "export_part_studio",
        "export_assembly",
        "capture_part_studio_screenshot",
        "capture_assembly_screenshot",
        "check_assembly_interference",
        "get_assembly_positions",
        "set_instance_position",
        "align_instance_to_face",
        "get_body_details",
        "get_assembly_features",
        "get_face_coordinate_system",
    }
)


def validate_tool_surface(tools: Iterable[Tool]) -> list[str]:
    """Return release-gate violations for a public MCP tool list."""
    violations: list[str] = []
    tools = list(tools)
    names = [tool.name for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        violations.append(f"duplicate tool names: {', '.join(duplicates)}")
    legacy = sorted(set(names) & LEGACY_TOOL_NAMES)
    if legacy:
        violations.append(f"legacy public tools registered: {', '.join(legacy)}")
    for tool in tools:
        schema = tool.inputSchema
        if not isinstance(schema, dict) or schema.get("type") != "object":
            violations.append(f"{tool.name} does not publish an object input schema")
        elif "properties" not in schema:
            violations.append(f"{tool.name} schema has no properties")
    return violations


def expected_tool_names(profile: str = "default") -> frozenset[str]:
    """Return reviewed names for the selected MCP profile."""
    return frozenset(tool.name for tool in consolidated_tools(profile))


def validate_expected_surface(tools: Iterable[Tool], profile: str = "default") -> list[str]:
    """Ensure a profile matches its reviewed consolidated set."""
    actual = frozenset(tool.name for tool in tools)
    expected = expected_tool_names(profile)
    violations = validate_tool_surface(tools)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            violations.append(f"missing public tools: {', '.join(missing)}")
        if extra:
            violations.append(f"unexpected public tools: {', '.join(extra)}")
    return violations


def verification_report(tools: Iterable[Tool], profile: str = "default") -> dict[str, Any]:
    """Build a machine-readable release report."""
    tools = list(tools)
    violations = validate_expected_surface(tools, profile)
    return {
        "ok": not violations,
        "profile": profile,
        "toolCount": len(tools),
        "toolNames": [tool.name for tool in tools],
        "violations": violations,
    }
