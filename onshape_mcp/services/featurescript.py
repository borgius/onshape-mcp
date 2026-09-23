"""FeatureScript documentation, source, evaluation, and verification workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Optional

from ..api.featurescript import FeatureScriptManager
from ..api.registry import OperationRegistry
from ..models.references import OnshapeTarget
from ..models.results import Diagnostic, ToolResult


@dataclass(frozen=True)
class FeatureScriptDocument:
    """One searchable documentation excerpt."""

    title: str
    content: str
    source: str
    version: Optional[str] = None


class FeatureScriptDocumentationIndex:
    """Small bounded index for reviewed FeatureScript documentation excerpts."""

    def __init__(self, documents: Iterable[FeatureScriptDocument] = ()) -> None:
        self._documents = list(documents)

    def add(self, document: FeatureScriptDocument) -> None:
        self._documents.append(document)

    def add_markdown_file(self, path: str | Path, *, source: Optional[str] = None) -> None:
        file_path = Path(path)
        content = file_path.read_text(encoding="utf-8")
        self.add(
            FeatureScriptDocument(
                title=next(
                    (
                        line.lstrip("# ").strip()
                        for line in content.splitlines()
                        if line.startswith("#")
                    ),
                    file_path.stem,
                ),
                content=content,
                source=source or str(file_path),
            )
        )

    def search(self, query: str, *, limit: int = 5) -> list[FeatureScriptDocument]:
        """Return documents ranked by simple token overlap."""
        tokens = {token for token in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(token) > 1}
        if not tokens:
            return []
        ranked: list[tuple[int, FeatureScriptDocument]] = []
        for document in self._documents:
            haystack = f"{document.title} {document.content}".lower()
            score = sum(haystack.count(token) for token in tokens)
            if score:
                ranked.append((score, document))
        ranked.sort(key=lambda item: (-item[0], item[1].title))
        return [document for _, document in ranked[:limit]]


class FeatureScriptWorkflowService:
    """Compose FeatureScript source lifecycle with live geometry verification."""

    def __init__(
        self,
        feature_manager: FeatureScriptManager,
        *,
        partstudio_manager: Any | None = None,
        documentation: FeatureScriptDocumentationIndex | None = None,
    ) -> None:
        self.feature_manager = feature_manager
        self.partstudio_manager = partstudio_manager
        self.documentation = documentation or FeatureScriptDocumentationIndex()

    def search_docs(self, query: str, *, limit: int = 5) -> ToolResult[list[dict[str, Any]]]:
        results = [
            {
                "title": document.title,
                "content": document.content[:4000],
                "source": document.source,
                "version": document.version,
            }
            for document in self.documentation.search(query, limit=limit)
        ]
        return ToolResult.success(results)

    async def evaluate_expression(
        self,
        target: OnshapeTarget,
        script: str,
        *,
        inspect_geometry: bool = False,
    ) -> ToolResult[dict[str, Any]]:
        """Evaluate an expression and optionally verify resulting geometry."""
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for FeatureScript evaluation",
                target=target,
                code="MISSING_TARGET",
            )
        response = await self.feature_manager.evaluate(
            target.document_id,
            target.workspace_id,
            target.element_id,
            script,
        )
        data: dict[str, Any] = {"evaluation": response}
        diagnostics = self._diagnostics_from_response(response)
        if inspect_geometry and self.partstudio_manager is not None:
            data["boundingBox"] = await self.feature_manager.get_bounding_box(
                target.document_id,
                target.workspace_id,
                target.element_id,
            )
            data["bodyDetails"] = await self.partstudio_manager.get_body_details(
                target.document_id,
                target.workspace_id,
                target.element_id,
            )
        return ToolResult(
            ok=not any(item.severity == "error" for item in diagnostics),
            target=target,
            data=data,
            diagnostics=diagnostics,
            state={"evaluated": True, "geometryInspected": inspect_geometry},
        )

    async def test_source(
        self,
        target: OnshapeTarget,
        registry: OperationRegistry,
        *,
        compile_operation_id: str,
        path_params: Mapping[str, Any],
        compile_body: Mapping[str, Any] | None = None,
        regenerate_operation_id: str | None = None,
        regenerate_path_params: Mapping[str, Any] | None = None,
        regenerate_body: Mapping[str, Any] | None = None,
        inspect_geometry: bool = False,
    ) -> ToolResult[dict[str, Any]]:
        """Compile source, optionally regenerate a custom feature, then inspect it."""
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for FeatureScript testing",
                target=target,
                code="MISSING_TARGET",
            )
        try:
            compiled = await self.feature_manager.client.execute(
                registry,
                compile_operation_id,
                path_params=path_params,
                body=compile_body,
            )
        except Exception as exc:
            return ToolResult.failure(
                f"FeatureScript compilation failed: {exc}",
                target=target,
                code="FEATURESCRIPT_COMPILE_FAILED",
                details={"compileOperationId": compile_operation_id},
            )
        diagnostics = self._diagnostics_from_response(compiled)
        data: dict[str, Any] = {"compile": compiled, "sourceUpdated": False}
        if any(item.severity == "error" for item in diagnostics):
            return ToolResult(
                ok=False,
                summary="FeatureScript compilation failed",
                target=target,
                data=data,
                diagnostics=diagnostics,
                state={"compiled": False, "regenerated": False},
            )
        state = {"compiled": True, "sourceValidated": True, "regenerated": False}
        if regenerate_operation_id:
            try:
                regenerated = await self.feature_manager.client.execute(
                    registry,
                    regenerate_operation_id,
                    path_params=regenerate_path_params or path_params,
                    body=regenerate_body,
                )
            except Exception as exc:
                return ToolResult.failure(
                    f"FeatureScript regeneration failed: {exc}",
                    target=target,
                    code="FEATURESCRIPT_REGENERATE_FAILED",
                    details={"regenerateOperationId": regenerate_operation_id},
                )
            data["regeneration"] = regenerated
            diagnostics.extend(self._diagnostics_from_response(regenerated))
            state["regenerated"] = not any(item.severity == "error" for item in diagnostics)
        if inspect_geometry:
            data["boundingBox"] = await self.feature_manager.get_bounding_box(
                target.document_id, target.workspace_id, target.element_id
            )
            if self.partstudio_manager is not None:
                data["bodyDetails"] = await self.partstudio_manager.get_body_details(
                    target.document_id, target.workspace_id, target.element_id
                )
        return ToolResult(
            ok=not any(item.severity == "error" for item in diagnostics),
            summary="FeatureScript compiled and inspected",
            target=target,
            data=data,
            diagnostics=diagnostics,
            state=state,
        )

    async def read_source(
        self,
        target: OnshapeTarget,
        registry: OperationRegistry,
        operation_id: str,
        *,
        path_params: Mapping[str, Any],
        query_params: Mapping[str, Any] | None = None,
    ) -> ToolResult[Any]:
        """Read Feature Studio source through a reviewed OpenAPI operation."""
        response = await self.feature_manager.client.execute(
            registry,
            operation_id,
            path_params=path_params,
            query_params=query_params,
        )
        return ToolResult.success(response, target=target, state={"sourceRead": True})

    async def write_source(
        self,
        target: OnshapeTarget,
        registry: OperationRegistry,
        operation_id: str,
        *,
        path_params: Mapping[str, Any],
        body: Mapping[str, Any],
        expected_revision: Optional[str] = None,
    ) -> ToolResult[Any]:
        """Write Feature Studio source with optional optimistic concurrency."""
        payload = dict(body)
        if expected_revision is not None:
            payload["expectedRevision"] = expected_revision
        response = await self.feature_manager.client.execute(
            registry,
            operation_id,
            path_params=path_params,
            body=payload,
        )
        diagnostics = self._diagnostics_from_response(response)
        return ToolResult(
            ok=not any(item.severity == "error" for item in diagnostics),
            target=target,
            data=response,
            diagnostics=diagnostics,
            state={"sourceWritten": True},
        )

    @staticmethod
    def _diagnostics_from_response(response: Any) -> list[Diagnostic]:
        if not isinstance(response, Mapping):
            return []
        raw = response.get("diagnostics", response.get("errors", []))
        if not isinstance(raw, list):
            return []
        diagnostics = []
        for item in raw:
            if isinstance(item, str):
                diagnostics.append(Diagnostic(message=item))
            elif isinstance(item, Mapping):
                diagnostics.append(
                    Diagnostic(
                        severity=str(item.get("severity", "error")).lower(),
                        message=str(item.get("message", item.get("error", "FeatureScript error"))),
                        code=item.get("code"),
                        line=item.get("line"),
                        column=item.get("column"),
                        details=dict(item),
                    )
                )
        return diagnostics
