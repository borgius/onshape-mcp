"""Translation lifecycle read operations."""

from __future__ import annotations

from typing import Any

from ..api.export import ExportManager
from ..models.references import OnshapeTarget
from ..models.results import Diagnostic, ToolResult


class TranslationService:
    """Normalize translation status and terminal state handling."""

    def __init__(self, export_manager: ExportManager) -> None:
        self.export_manager = export_manager

    async def get_translation(
        self,
        translation_id: str,
        *,
        target: OnshapeTarget | None = None,
        poll: bool = False,
        timeout_seconds: float = 30.0,
        interval_seconds: float = 1.0,
    ) -> ToolResult[dict[str, Any]]:
        """Read one translation, optionally polling to a terminal state."""
        if not translation_id:
            return ToolResult.failure("translationId is required", code="MISSING_TRANSLATION_ID")
        if timeout_seconds <= 0 or interval_seconds <= 0:
            return ToolResult.failure(
                "timeoutSeconds and intervalSeconds must be positive",
                target=target,
                code="INVALID_POLLING_LIMIT",
            )
        response: dict[str, Any] = {}
        if poll:
            import asyncio
            import time

            deadline = time.monotonic() + timeout_seconds
            while True:
                response = await self.export_manager.get_translation_status(translation_id)
                state = str(response.get("requestState", response.get("state", ""))).upper()
                if state in {"DONE", "COMPLETE", "FAILED", "ERROR", "CANCELLED"}:
                    break
                if time.monotonic() >= deadline:
                    return ToolResult.failure(
                        "translation polling timed out",
                        target=target,
                        code="TRANSLATION_TIMEOUT",
                        retryable=True,
                        details={"translationId": translation_id, "lastState": state},
                    )
                await asyncio.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))
        else:
            response = await self.export_manager.get_translation_status(translation_id)
        state = str(response.get("requestState", response.get("state", ""))).upper()
        diagnostics: list[Diagnostic] = []
        if state in {"FAILED", "ERROR", "CANCELLED"}:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    message=response.get("failureReason", "translation failed"),
                    code=state,
                    details=response,
                )
            )
        download = (
            response.get("resultExternalData")
            or response.get("result")
            or response.get("download")
            or response.get("downloadUrl")
        )
        data = dict(response)
        if download is not None:
            data["downloadReference"] = download
        return ToolResult(
            ok=state not in {"FAILED", "ERROR", "CANCELLED"},
            summary=f"Translation {state.lower() or 'unknown'}",
            target=target,
            data=data,
            state={"translationId": translation_id, "requestState": state},
            diagnostics=diagnostics,
            nextActions=[]
            if state in {"DONE", "COMPLETE", "FAILED", "ERROR", "CANCELLED"}
            else ["get_translation"],
        )

    async def start_translation(
        self,
        target: OnshapeTarget,
        *,
        resource_kind: str,
        format_name: str = "STL",
        part_id: str | None = None,
    ) -> ToolResult[dict[str, Any]]:
        """Start a Part Studio or Assembly translation and return its ID."""
        if not target.workspace_id or not target.element_id:
            return ToolResult.failure(
                "workspaceId and elementId are required for translation",
                target=target,
                code="MISSING_TARGET",
            )
        kind = resource_kind.lower()
        if kind == "partstudio":
            response = await self.export_manager.export_part_studio(
                target.document_id,
                target.workspace_id,
                target.element_id,
                format_name=format_name,
                part_id=part_id,
            )
        elif kind == "assembly":
            response = await self.export_manager.export_assembly(
                target.document_id,
                target.workspace_id,
                target.element_id,
                format_name=format_name,
            )
        else:
            return ToolResult.failure(
                f"unsupported translation resourceKind: {resource_kind}",
                target=target,
                code="UNSUPPORTED_RESOURCE_KIND",
            )
        translation_id = response.get("id", response.get("translationId"))
        return ToolResult.success(
            response,
            target=target,
            state={"translationId": translation_id, "requestState": "PENDING"},
            next_actions=["get_translation"] if translation_id else [],
        )
