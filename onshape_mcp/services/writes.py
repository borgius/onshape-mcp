"""Domain-oriented write services for documents, sketches, features, and assemblies."""

from __future__ import annotations

from typing import Any, Mapping, Optional
import re

from ..api.assemblies import AssemblyManager
from ..api.documents import DocumentManager
from ..api.partstudio import PartStudioManager
from ..api.variables import VariableManager
from ..builders.sketch import SketchBuilder, SketchPlane
from ..models.references import OnshapeTarget
from ..models.results import ToolResult

_UNIT_EXPRESSION = re.compile(
    r"^(?:#?[A-Za-z_][A-Za-z0-9_.]*|[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    r"(?:\s*(?:mm|cm|m|in|ft|deg|rad|s|kg))?)$"
)


def _validate_unit_expression(expression: str) -> None:
    if not _UNIT_EXPRESSION.fullmatch(expression.strip()):
        raise ValueError(f"invalid unit-bearing expression: {expression}")


class ObjectWriteService:
    """Create and edit top-level Onshape objects through existing managers."""

    def __init__(
        self,
        document_manager: DocumentManager,
        partstudio_manager: PartStudioManager,
        assembly_manager: AssemblyManager,
        variable_manager: VariableManager,
        feature_manager: Any | None = None,
    ) -> None:
        self.document_manager = document_manager
        self.partstudio_manager = partstudio_manager
        self.assembly_manager = assembly_manager
        self.variable_manager = variable_manager
        self.feature_manager = feature_manager

    async def create(
        self,
        kind: str,
        *,
        name: str,
        document_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        description: Optional[str] = None,
        is_public: bool = True,
    ) -> ToolResult[dict[str, Any]]:
        """Create one allowlisted resource kind."""
        normalized = kind.lower()
        if normalized == "document":
            result = await self.document_manager.create_document(name, description, is_public)
        elif normalized == "workspace":
            if not document_id:
                raise ValueError("documentId is required")
            result = await self.document_manager.create_workspace(document_id, name)
        elif normalized == "version":
            if not document_id:
                raise ValueError("documentId is required")
            result = await self.document_manager.create_version(document_id, name, description)
        elif normalized in {"partstudio", "part_studio"}:
            self._require_workspace(document_id, workspace_id)
            result = await self.partstudio_manager.create_part_studio(
                document_id, workspace_id, name
            )
        elif normalized == "assembly":
            self._require_workspace(document_id, workspace_id)
            result = await self.assembly_manager.create_assembly(document_id, workspace_id, name)
        elif normalized in {"featurestudio", "feature_studio"}:
            self._require_workspace(document_id, workspace_id)
            if self.feature_manager is None:
                return ToolResult.failure(
                    "FeatureScript manager is not configured",
                    code="FEATURESCRIPT_MANAGER_UNAVAILABLE",
                )
            result = await self.feature_manager.create_feature_studio(
                document_id, workspace_id, name
            )
        else:
            return ToolResult.failure(
                f"unsupported object kind: {kind}", code="UNSUPPORTED_OBJECT_KIND"
            )
        return ToolResult.success(result, state={"created": normalized})

    @staticmethod
    def _require_workspace(document_id: Optional[str], workspace_id: Optional[str]) -> None:
        if not document_id or not workspace_id:
            raise ValueError("documentId and workspaceId are required")


class SketchWriteService:
    """Create or update sketches from a single multi-entity request."""

    def __init__(self, partstudio_manager: PartStudioManager) -> None:
        self.partstudio_manager = partstudio_manager

    async def edit(
        self,
        target: OnshapeTarget,
        *,
        action: str = "create",
        plane: str = "Front",
        plane_id: Optional[str] = None,
        name: str = "Sketch",
        entities: list[Mapping[str, Any]] | None = None,
        constraints: list[Mapping[str, Any]] | None = None,
        feature_data: Optional[dict[str, Any]] = None,
        feature_id: Optional[str] = None,
    ) -> ToolResult[dict[str, Any]]:
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for sketch editing",
                target=target,
                code="MISSING_TARGET",
            )
        if action not in {"create", "update"}:
            return ToolResult.failure(
                f"unsupported sketch action: {action}", target=target, code="UNSUPPORTED_ACTION"
            )
        if action == "update":
            if not feature_id or feature_data is None:
                return ToolResult.failure(
                    "featureId and featureData are required for sketch updates",
                    target=target,
                    code="MISSING_FEATURE",
                )
            result = await self.partstudio_manager.update_feature(
                target.document_id,
                target.workspace_id,
                target.element_id,
                feature_id,
                feature_data,
            )
            return ToolResult.success(
                result,
                summary=f"Updated sketch {feature_id}",
                target=target,
                state={"updated": feature_id},
            )

        plane_enum = SketchPlane[plane.upper()]
        resolved_plane_id = plane_id or await self.partstudio_manager.get_plane_id(
            target.document_id, target.workspace_id, target.element_id, plane
        )
        builder = SketchBuilder(name=name, plane=plane_enum, plane_id=resolved_plane_id)
        for entity in entities or []:
            entity_type = str(entity.get("type", "")).lower()
            if entity_type == "rectangle":
                builder.add_rectangle(tuple(entity["corner1"]), tuple(entity["corner2"]))
            elif entity_type == "circle":
                builder.add_circle(tuple(entity["center"]), float(entity["radius"]))
            elif entity_type == "line":
                builder.add_line(tuple(entity["start"]), tuple(entity["end"]))
            elif entity_type == "arc":
                builder.add_arc(
                    tuple(entity["center"]),
                    float(entity["radius"]),
                    float(entity["startAngle"]),
                    float(entity["endAngle"]),
                )
            else:
                raise ValueError(f"unsupported sketch entity type: {entity_type}")
        for constraint in constraints or []:
            if not isinstance(constraint, Mapping) or not constraint.get("constraintType"):
                raise ValueError("each sketch constraint requires constraintType")
            expression = constraint.get("expression")
            if expression is not None:
                _validate_unit_expression(str(expression))
            builder.constraints.append(dict(constraint))
        result = await self.partstudio_manager.add_feature(
            target.document_id,
            target.workspace_id,
            target.element_id,
            builder.build(),
        )
        return ToolResult.success(
            result,
            summary="Created sketch",
            target=target,
            state={"created": "sketch", "constraintCount": len(constraints or [])},
        )


class PartFeatureWriteService:
    """Create/update feature definitions without one public tool per feature type."""

    def __init__(self, partstudio_manager: PartStudioManager) -> None:
        self.partstudio_manager = partstudio_manager

    async def edit(
        self,
        target: OnshapeTarget,
        *,
        action: str = "create",
        feature_data: Optional[dict[str, Any]] = None,
        feature_id: Optional[str] = None,
    ) -> ToolResult[dict[str, Any]]:
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for feature editing",
                target=target,
                code="MISSING_TARGET",
            )
        if feature_data is None:
            return ToolResult.failure(
                "featureData is required", target=target, code="MISSING_FEATURE_DATA"
            )
        if action == "create":
            result = await self.partstudio_manager.add_feature(
                target.document_id, target.workspace_id, target.element_id, feature_data
            )
            return ToolResult.success(result, target=target, state={"created": "feature"})
        if action == "update" and feature_id:
            result = await self.partstudio_manager.update_feature(
                target.document_id,
                target.workspace_id,
                target.element_id,
                feature_id,
                feature_data,
            )
            return ToolResult.success(result, target=target, state={"updated": feature_id})
        return ToolResult.failure(
            "featureId is required for feature updates", target=target, code="MISSING_FEATURE"
        )


class VariableWriteService:
    """Batch variable updates while preserving unrelated variables."""

    def __init__(self, variable_manager: VariableManager) -> None:
        self.variable_manager = variable_manager

    async def edit(
        self,
        target: OnshapeTarget,
        variables: list[Mapping[str, Any]],
    ) -> ToolResult[list[dict[str, Any]]]:
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for variable editing",
                target=target,
                code="MISSING_TARGET",
            )
        results = []
        for variable in variables:
            result = await self.variable_manager.set_variable(
                target.document_id,
                target.workspace_id,
                target.element_id,
                str(variable["name"]),
                str(variable["expression"]),
                variable.get("description"),
            )
            results.append({"name": variable["name"], "result": result})
        return ToolResult.success(results, target=target, state={"updated": len(results)})


class AssemblyWriteService:
    """Create/update Assembly instances and feature-based mates."""

    def __init__(self, assembly_manager: AssemblyManager) -> None:
        self.assembly_manager = assembly_manager

    async def edit_instance(
        self,
        target: OnshapeTarget,
        *,
        action: str,
        payload: Mapping[str, Any],
    ) -> ToolResult[dict[str, Any]]:
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for Assembly instance editing",
                target=target,
                code="MISSING_TARGET",
            )
        base = (target.document_id, target.workspace_id, target.element_id)
        if action == "add":
            result = await self.assembly_manager.add_instance(
                *base,
                payload["partStudioElementId"],
                payload.get("partId"),
                bool(payload.get("isAssembly", False)),
            )
        elif action in {"transform", "position"}:
            result = await self.assembly_manager.transform_occurrences(
                *base,
                payload["occurrences"],
                is_relative=action == "transform",
            )
        elif action == "delete":
            if not payload.get("instanceId"):
                return ToolResult.failure(
                    "instanceId is required for deletion",
                    target=target,
                    code="MISSING_INSTANCE",
                )
            result = await self.assembly_manager.delete_instance(*base, payload["instanceId"])
        elif action in {"align", "fix", "unfix", "suppress", "unsuppress"}:
            return ToolResult.failure(
                f"{action} requires a reviewed operationId",
                target=target,
                code="OPERATION_ID_REQUIRED",
            )
        else:
            return ToolResult.failure(
                f"unsupported instance action: {action}",
                target=target,
                code="UNSUPPORTED_ACTION",
            )
        return ToolResult.success(result, target=target, state={"instanceAction": action})

    async def edit_mate(
        self,
        target: OnshapeTarget,
        *,
        action: str,
        feature_data: Optional[dict[str, Any]] = None,
        feature_id: Optional[str] = None,
    ) -> ToolResult[dict[str, Any]]:
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for mate editing",
                target=target,
                code="MISSING_TARGET",
            )
        base = (target.document_id, target.workspace_id, target.element_id)
        if action == "create" and feature_data is not None:
            result = await self.assembly_manager.add_feature(*base, feature_data)
        elif action == "update" and feature_id and feature_data is not None:
            result = await self.assembly_manager.update_feature(*base, feature_id, feature_data)
        elif action in {"suppress", "unsuppress"} and feature_id:
            result = await self.assembly_manager.update_feature(
                *base,
                feature_id,
                {**(feature_data or {}), "suppressed": action == "suppress"},
            )
        elif action == "inspect":
            features = await self.assembly_manager.get_features(*base)
            if feature_id:
                features = {
                    **features,
                    "features": [
                        item
                        for item in features.get("features", [])
                        if item.get("featureId") == feature_id or item.get("id") == feature_id
                    ],
                }
            result = features
        elif action == "delete" and feature_id:
            result = await self.assembly_manager.delete_feature(*base, feature_id)
        else:
            return ToolResult.failure(
                "create requires featureData; update/suppress/delete require featureId",
                target=target,
                code="INVALID_MATE_ACTION",
            )
        return ToolResult.success(result, target=target, state={"mateAction": action})
