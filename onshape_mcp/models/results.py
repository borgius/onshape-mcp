"""Structured success, warning, diagnostic, and error results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .references import OnshapeTarget

T = TypeVar("T")


class Diagnostic(BaseModel):
    """A compiler, regeneration, solver, translation, or validation diagnostic."""

    severity: str = "error"
    message: str
    code: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    retryable: bool = False
    request_id: Optional[str] = Field(default=None, alias="requestId")
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ToolResult(BaseModel, Generic[T]):
    """Common envelope for consolidated service responses."""

    ok: bool
    summary: str = ""
    target: Optional[OnshapeTarget] = None
    data: Optional[T] = None
    state: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Diagnostic] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list, alias="nextActions")
    http_status: Optional[int] = Field(default=None, alias="httpStatus")
    request_id: Optional[str] = Field(default=None, alias="requestId")
    retryable: bool = False

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @classmethod
    def success(
        cls,
        data: Optional[T] = None,
        *,
        summary: str = "",
        target: Optional[OnshapeTarget] = None,
        state: Optional[dict[str, Any]] = None,
        warnings: Optional[list[Diagnostic]] = None,
        next_actions: Optional[list[str]] = None,
        http_status: Optional[int] = None,
        request_id: Optional[str] = None,
    ) -> "ToolResult[T]":
        inferred: dict[str, Any] = {}
        if isinstance(data, Mapping):
            for key in (
                "id",
                "documentId",
                "workspaceId",
                "versionId",
                "microversionId",
                "elementId",
                "featureId",
                "translationId",
            ):
                if key in data:
                    inferred[key] = data[key]
        inferred.update(state or {})
        return cls(
            ok=True,
            summary=summary,
            target=target,
            data=data,
            state=inferred,
            warnings=warnings or [],
            nextActions=next_actions or [],
            httpStatus=http_status,
            requestId=request_id,
        )

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        summary: str = "Operation failed",
        target: Optional[OnshapeTarget] = None,
        code: Optional[str] = None,
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
        http_status: Optional[int] = None,
        request_id: Optional[str] = None,
    ) -> "ToolResult[None]":
        diagnostic = Diagnostic(
            message=message,
            code=code,
            retryable=retryable,
            requestId=request_id,
            details=details or {},
        )
        return cls(
            ok=False,
            summary=summary,
            target=target,
            diagnostics=[diagnostic],
            retryable=retryable,
            httpStatus=http_status,
            requestId=request_id,
        )
