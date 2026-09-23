"""Guarded operation-ID gateway for uncommon Onshape API operations."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional

import httpx

from ..api.client import OnshapeClient
from ..api.registry import OperationRegistry, OperationRegistryError
from ..models.results import ToolResult
from .destructive import DestructiveOperationGate


class OpenAPIGateway:
    """Expose reviewed OpenAPI operations without accepting arbitrary URLs."""

    def __init__(
        self,
        client: OnshapeClient,
        registry: OperationRegistry,
        *,
        destructive_gate: Optional[DestructiveOperationGate] = None,
        authenticated_scopes: frozenset[str] = frozenset(),
        reviewed_writes_only: bool = True,
        max_result_bytes: int = 2_000_000,
    ) -> None:
        self.client = client
        self.registry = registry
        self.destructive_gate = destructive_gate or DestructiveOperationGate()
        self.authenticated_scopes = authenticated_scopes
        self.reviewed_writes_only = reviewed_writes_only
        self._schema_cache: dict[str, tuple[float, ToolResult[dict[str, Any]]]] = {}

    def discover(
        self,
        query: str = "",
        *,
        classification: Optional[str] = None,
        tag: Optional[str] = None,
        resource_kind: Optional[str] = None,
        limit: int = 20,
    ) -> ToolResult[list[dict[str, Any]]]:
        """Return bounded operation metadata for capability discovery."""
        if limit < 1 or limit > 100:
            return ToolResult.failure("limit must be between 1 and 100", code="INVALID_LIMIT")
        try:
            matches = self.registry.search(
                query,
                classification=classification,
                tag=tag,
                resource_kind=resource_kind,
            )
        except OperationRegistryError as exc:
            return ToolResult.failure(str(exc), code="INVALID_OPERATION_FILTER")
        data = [
            {
                "operationId": item.operation_id,
                "method": item.method.upper(),
                "path": item.path,
                "classification": item.classification,
                "summary": item.summary,
                "description": item.description,
                "tags": list(item.tags),
                "scopes": list(item.scopes),
                "reviewed": item.reviewed,
                "pagination": item.pagination,
                "asynchronous": item.asynchronous,
                "binaryResponse": item.binary_response,
            }
            for item in matches[:limit]
        ]
        return ToolResult.success(data, summary=f"Found {len(data)} operations")

    def schema(self, operation_id: str) -> ToolResult[dict[str, Any]]:
        """Return one operation schema on demand with a bounded TTL cache."""
        cached = self._schema_cache.get(operation_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            operation = self.registry.get(operation_id)
        except OperationRegistryError as exc:
            return ToolResult.failure(str(exc), code="INVALID_OPERATION")
        result = ToolResult.success(
            {
                "operationId": operation.operation_id,
                "method": operation.method.upper(),
                "path": operation.path,
                "classification": operation.classification,
                "parameters": list(operation.parameters),
                "requestBody": operation.request_body,
                "responses": operation.responses,
                "scopes": list(operation.scopes),
                "reviewed": operation.reviewed,
            },
            summary=f"Schema for {operation_id}",
        )
        self._schema_cache[operation_id] = (time.monotonic() + 300, result)
        return result

    def _check_scopes(self, operation: Any) -> Optional[ToolResult[Any]]:
        required = set(operation.scopes)
        if required and not required.issubset(self.authenticated_scopes):
            missing = sorted(required - self.authenticated_scopes)
            return ToolResult.failure(
                f"authenticated scope is missing: {', '.join(missing)}",
                code="AUTH_SCOPE_REQUIRED",
            )
        return None

    def _check_size(self, result: Any) -> Optional[ToolResult[Any]]:
        if isinstance(result, (bytes, bytearray)):
            payload = bytes(result)
            mime_type = "application/octet-stream"
        else:
            try:
                payload = json.dumps(result, default=str).encode()
            except (TypeError, ValueError):
                return None
            mime_type = "application/json"
        if len(payload) <= self.max_result_bytes:
            return None
        with tempfile.NamedTemporaryFile(
            prefix="onshape-mcp-result-", suffix=".bin", delete=False
        ) as handle:
            handle.write(payload)
            path = handle.name
        return ToolResult.success(
            {
                "resource": {
                    "path": path,
                    "uri": Path(path).as_uri(),
                    "mimeType": mime_type,
                    "size": len(payload),
                }
            },
            summary="Result written to a local resource file",
            warnings=["Inline result exceeded the configured response limit"],
            state={"truncated": True, "maxResultBytes": self.max_result_bytes},
        )

    @staticmethod
    def _upstream_error(exc: Exception) -> ToolResult[Any]:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        headers = getattr(response, "headers", {}) or {}
        request_id = headers.get("x-request-id")
        return ToolResult.failure(
            str(exc),
            code="UPSTREAM_ERROR",
            http_status=status,
            request_id=request_id,
            retryable=status in {408, 409, 429} or (status is not None and status >= 500),
        )

    async def query(
        self,
        operation_id: str,
        *,
        path_params: Optional[Mapping[str, Any]] = None,
        query_params: Optional[Mapping[str, Any]] = None,
    ) -> ToolResult[Any]:
        """Execute a read-only operation selected by operation ID."""
        try:
            operation = self.registry.get(operation_id)
            if not operation.is_read_only:
                return ToolResult.failure(
                    f"{operation_id} is not read-only", code="READ_GATE_REJECTED"
                )
            scope_error = self._check_scopes(operation)
            if scope_error:
                return scope_error
            result = await self.client.execute(
                self.registry,
                operation_id,
                path_params=path_params,
                query_params=query_params,
            )
        except OperationRegistryError as exc:
            return ToolResult.failure(str(exc), code="INVALID_OPERATION")
        except (httpx.HTTPStatusError, ValueError) as exc:
            return self._upstream_error(exc)
        size_error = self._check_size(result)
        if size_error:
            return size_error
        return ToolResult.success(
            result,
            summary=f"Executed read operation {operation_id}",
            state={"operationId": operation_id, "classification": operation.classification},
        )

    async def mutate(
        self,
        operation_id: str,
        *,
        path_params: Optional[Mapping[str, Any]] = None,
        query_params: Optional[Mapping[str, Any]] = None,
        body: Any = None,
        resource_kind: Optional[str] = None,
        target: Optional[Mapping[str, Any]] = None,
        plan_token: Optional[str] = None,
        identity: Optional[str] = None,
    ) -> ToolResult[Any]:
        """Execute reviewed writes; deletes require a matching plan token."""
        try:
            operation = self.registry.get(operation_id)
        except OperationRegistryError as exc:
            return ToolResult.failure(str(exc), code="INVALID_OPERATION")
        if operation.is_read_only:
            return ToolResult.failure(
                f"{operation_id} is read-only; use query_onshape_operation",
                code="MUTATION_GATE_REJECTED",
            )
        if self.reviewed_writes_only and not operation.reviewed:
            return ToolResult.failure(
                f"{operation_id} is not in the reviewed mutation allowlist",
                code="REVIEW_REQUIRED",
            )
        scope_error = self._check_scopes(operation)
        if scope_error:
            return scope_error
        if operation.is_destructive:
            if not plan_token or not identity or not resource_kind or target is None:
                return ToolResult.failure(
                    "destructive mutations require planToken, identity, resourceKind, and target",
                    code="PLAN_REQUIRED",
                )

            async def execute_delete() -> Any:
                return await self.client.execute(
                    self.registry,
                    operation_id,
                    path_params=path_params,
                    query_params=query_params,
                    body=body,
                )

            return await self.destructive_gate.execute(
                plan_token,
                operation_id=operation_id,
                resource_kind=resource_kind,
                target=target,
                request_body=body,
                identity=identity,
                executor=execute_delete,
            )

        try:
            result = await self.client.execute(
                self.registry,
                operation_id,
                path_params=path_params,
                query_params=query_params,
                body=body,
            )
        except OperationRegistryError as exc:
            return ToolResult.failure(str(exc), code="INVALID_OPERATION")
        except (httpx.HTTPStatusError, ValueError) as exc:
            return self._upstream_error(exc)
        size_error = self._check_size(result)
        if size_error:
            return size_error
        return ToolResult.success(
            result,
            summary=f"Executed write operation {operation_id}",
            state={"operationId": operation_id, "classification": operation.classification},
        )
