"""Document, workspace, and element discovery orchestration."""

from __future__ import annotations

from typing import Any, Optional

from ..api.documents import DocumentManager
from ..models.references import OnshapeTarget, ResourceRef
from ..models.results import ToolResult


class DiscoveryService:
    """Normalize existing document-manager reads for consolidated callers."""

    def __init__(self, document_manager: DocumentManager) -> None:
        self.document_manager = document_manager

    async def search_onshape(
        self,
        resource_kind: str = "documents",
        *,
        query: Optional[str] = None,
        document_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        limit: int = 20,
        filter_type: Optional[str] = None,
    ) -> ToolResult[list[ResourceRef]]:
        """Search documents or list resources within a document workspace."""
        kind = resource_kind.lower()
        if kind not in {"documents", "workspaces", "elements", "partstudios"}:
            return ToolResult.failure(
                f"unsupported resourceKind: {resource_kind}",
                code="UNSUPPORTED_RESOURCE_KIND",
            )

        target = None
        if document_id and workspace_id:
            target = OnshapeTarget(documentId=document_id, workspaceId=workspace_id)

        if kind == "documents":
            if query:
                values = await self.document_manager.search_documents(query=query, limit=limit)
            else:
                values = await self.document_manager.list_documents(
                    filter_type=filter_type, limit=limit
                )
            refs = [
                ResourceRef(
                    kind="document",
                    id=item.id,
                    name=item.name,
                    metadata=item.model_dump(by_alias=True),
                )
                for item in values
            ]
            return ToolResult.success(refs, target=target)

        if not document_id or not workspace_id:
            return ToolResult.failure(
                "documentId and workspaceId are required for workspace resources",
                code="MISSING_TARGET",
            )

        if kind == "workspaces":
            values = await self.document_manager.get_workspaces(document_id)
            refs = [
                ResourceRef(
                    kind="workspace",
                    id=item.id,
                    name=item.name,
                    target=OnshapeTarget(documentId=document_id, workspaceId=item.id),
                    metadata=item.model_dump(by_alias=True),
                )
                for item in values[:limit]
            ]
        else:
            values = await self.document_manager.get_elements(document_id, workspace_id)
            if kind == "partstudios":
                values = [
                    item
                    for item in values
                    if item.element_type.upper().replace(" ", "") == "PARTSTUDIO"
                ]
            refs = [
                ResourceRef(
                    kind=item.element_type.lower(),
                    id=item.id,
                    name=item.name,
                    target=target.child(elementId=item.id),
                    metadata=item.model_dump(by_alias=True),
                )
                for item in values[:limit]
            ]
        return ToolResult.success(refs, target=target)

    async def get_onshape_context(self, document_id: str) -> ToolResult[dict[str, Any]]:
        """Return a normalized document/workspace/element context."""
        summary = await self.document_manager.get_document_summary(document_id)
        document = summary["document"]
        workspaces = summary.get("workspaces", [])
        context = {
            "document": ResourceRef(
                kind="document",
                id=document.id,
                name=document.name,
                metadata=document.model_dump(by_alias=True),
            ).model_dump(mode="json"),
            "workspaces": [
                ResourceRef(
                    kind="workspace",
                    id=workspace.id,
                    name=workspace.name,
                    target=OnshapeTarget(documentId=document_id, workspaceId=workspace.id),
                    metadata=workspace.model_dump(by_alias=True),
                ).model_dump(mode="json")
                for workspace in workspaces
            ],
            "workspaceDetails": [
                {
                    "workspace": item["workspace"].model_dump(by_alias=True),
                    "elements": [element.model_dump(by_alias=True) for element in item["elements"]],
                }
                for item in summary.get("workspace_details", [])
            ],
        }
        return ToolResult.success(context)
