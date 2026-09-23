"""Read-only Part Studio, Assembly, and geometry inspection services."""

from __future__ import annotations

from typing import Any, Iterable

from ..api.assemblies import AssemblyManager
from ..api.partstudio import PartStudioManager
from ..analysis.interference import check_assembly_interference
from ..analysis.positioning import get_assembly_positions
from ..models.references import OnshapeTarget
from ..models.results import ToolResult


_DEFAULT_PART_STUDIO_SECTIONS = frozenset({"features", "parts", "bodyDetails"})
_DEFAULT_ASSEMBLY_SECTIONS = frozenset({"definition", "features"})


class PartStudioInspectionService:
    """Compose bounded read sections for a Part Studio target."""

    def __init__(self, partstudio_manager: PartStudioManager) -> None:
        self.partstudio_manager = partstudio_manager

    async def inspect(
        self,
        target: OnshapeTarget,
        *,
        sections: Iterable[str] = _DEFAULT_PART_STUDIO_SECTIONS,
    ) -> ToolResult[dict[str, Any]]:
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for Part Studio inspection",
                target=target,
                code="MISSING_TARGET",
            )
        selected = set(sections)
        allowed = {"features", "parts", "bodyDetails"}
        unknown = sorted(selected - allowed)
        if unknown:
            return ToolResult.failure(
                f"unsupported Part Studio sections: {', '.join(unknown)}",
                target=target,
                code="UNSUPPORTED_SECTION",
            )

        data: dict[str, Any] = {}
        if "features" in selected:
            data["features"] = await self.partstudio_manager.get_features(
                target.document_id, target.workspace_id, target.element_id
            )
        if "parts" in selected:
            data["parts"] = await self.partstudio_manager.get_parts(
                target.document_id, target.workspace_id, target.element_id
            )
        if "bodyDetails" in selected:
            data["bodyDetails"] = await self.partstudio_manager.get_body_details(
                target.document_id, target.workspace_id, target.element_id
            )
        return ToolResult.success(data, target=target)


class AssemblyInspectionService:
    """Compose bounded Assembly state and solver verification reads."""

    def __init__(
        self,
        assembly_manager: AssemblyManager,
        partstudio_manager: PartStudioManager,
    ) -> None:
        self.assembly_manager = assembly_manager
        self.partstudio_manager = partstudio_manager

    async def inspect(
        self,
        target: OnshapeTarget,
        *,
        sections: Iterable[str] = _DEFAULT_ASSEMBLY_SECTIONS,
        include_positions: bool = False,
        include_interference: bool = False,
    ) -> ToolResult[dict[str, Any]]:
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for Assembly inspection",
                target=target,
                code="MISSING_TARGET",
            )
        selected = set(sections)
        allowed = {"definition", "features"}
        unknown = sorted(selected - allowed)
        if unknown:
            return ToolResult.failure(
                f"unsupported Assembly sections: {', '.join(unknown)}",
                target=target,
                code="UNSUPPORTED_SECTION",
            )

        data: dict[str, Any] = {}
        if "definition" in selected:
            data["definition"] = await self.assembly_manager.get_assembly_definition(
                target.document_id, target.workspace_id, target.element_id
            )
        if "features" in selected:
            data["features"] = await self.assembly_manager.get_features(
                target.document_id, target.workspace_id, target.element_id
            )
        if include_positions:
            data["positions"] = await get_assembly_positions(
                self.assembly_manager,
                self.partstudio_manager,
                target.document_id,
                target.workspace_id,
                target.element_id,
            )
        if include_interference:
            data["interference"] = await check_assembly_interference(
                self.assembly_manager,
                self.partstudio_manager,
                target.document_id,
                target.workspace_id,
                target.element_id,
            )
        return ToolResult.success(data, target=target)


class GeometryInspectionService:
    """Read geometry details and optional screenshots through existing managers."""

    def __init__(
        self,
        partstudio_manager: PartStudioManager,
        visuals_manager: Any | None = None,
    ) -> None:
        self.partstudio_manager = partstudio_manager
        self.visuals_manager = visuals_manager

    async def inspect_part_studio(
        self,
        target: OnshapeTarget,
        *,
        include_screenshot: bool = False,
        screenshot_options: dict[str, Any] | None = None,
    ) -> ToolResult[dict[str, Any]]:
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for geometry inspection",
                target=target,
                code="MISSING_TARGET",
            )
        data: dict[str, Any] = {
            "bodyDetails": await self.partstudio_manager.get_body_details(
                target.document_id, target.workspace_id, target.element_id
            ),
            "parts": await self.partstudio_manager.get_parts(
                target.document_id, target.workspace_id, target.element_id
            ),
        }
        if include_screenshot:
            if self.visuals_manager is None:
                return ToolResult.failure(
                    "visual capture is not configured",
                    target=target,
                    code="VISUALS_UNAVAILABLE",
                )
            data["screenshot"] = await self.visuals_manager.capture_part_studio(
                target.document_id,
                target.workspace_id,
                target.element_id,
                **(screenshot_options or {}),
            )
        return ToolResult.success(data, target=target)
