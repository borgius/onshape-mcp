"""Plan/execute boundary for destructive Onshape operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any, Awaitable, Callable, Mapping, Optional

from ..models.results import ToolResult


ALLOWED_RESOURCE_KINDS = frozenset(
    {
        "document",
        "workspace",
        "version",
        "microversion",
        "element",
        "partstudio",
        "assembly",
        "featurestudio",
        "variablestudio",
        "feature",
        "instance",
        "mate",
    }
)
_REVISION_KEYS = ("workspaceId", "versionId", "microversionId")


@dataclass(frozen=True)
class DestructivePlan:
    """A short-lived, payload-bound destructive execution plan."""

    token: str
    operation_id: str
    resource_kind: str
    target: dict[str, Any]
    request_hash: str
    identity: str
    expires_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "operationId": self.operation_id,
            "resourceKind": self.resource_kind,
            "target": self.target,
            "requestHash": self.request_hash,
            "identity": hashlib.sha256(self.identity.encode()).hexdigest()[:16],
            "expiresAt": self.expires_at.isoformat(),
        }


class DestructiveOperationGate:
    """Issue and consume single-use tokens for destructive requests."""

    def __init__(
        self,
        *,
        default_ttl_seconds: int = 120,
        allowed_resource_kinds: frozenset[str] = ALLOWED_RESOURCE_KINDS,
    ) -> None:
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be positive")
        self.default_ttl_seconds = default_ttl_seconds
        self.allowed_resource_kinds = allowed_resource_kinds
        self._plans: dict[str, DestructivePlan] = {}
        self._consumed: set[str] = set()
        self._audit: list[dict[str, Any]] = []

    @staticmethod
    def _request_hash(request_body: Any) -> str:
        encoded = json.dumps(
            request_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validate_target(self, resource_kind: str, target: Mapping[str, Any]) -> None:
        kind = resource_kind.lower()
        if kind not in self.allowed_resource_kinds:
            raise ValueError(f"resourceKind is not allowlisted: {resource_kind}")
        if not target.get("documentId"):
            raise ValueError("target.documentId is required")
        selectors = [target.get(key) for key in _REVISION_KEYS if target.get(key) is not None]
        if len(selectors) > 1:
            raise ValueError("target must contain at most one revision selector")
        id_key = {
            "document": "documentId",
            "workspace": "workspaceId",
            "version": "versionId",
            "microversion": "microversionId",
            "element": "elementId",
            "partstudio": "elementId",
            "assembly": "elementId",
            "featurestudio": "elementId",
            "variablestudio": "elementId",
            "feature": "featureId",
            "instance": "instanceId",
            "mate": "mateId",
        }[kind]
        if not target.get(id_key):
            raise ValueError(f"target.{id_key} is required for {resource_kind}")

    def _record(
        self, event: str, plan: DestructivePlan, *, ok: bool, error: str | None = None
    ) -> None:
        self._audit.append(
            {
                "event": event,
                "ok": ok,
                "operationId": plan.operation_id,
                "resourceKind": plan.resource_kind,
                "target": plan.target,
                "requestHash": plan.request_hash,
                "identity": hashlib.sha256(plan.identity.encode()).hexdigest()[:16],
                "expiresAt": plan.expires_at.isoformat(),
                **({"error": error} if error else {}),
            }
        )

    def audit_records(self) -> tuple[dict[str, Any], ...]:
        """Return redacted audit records; credentials and request bodies are never stored."""
        return tuple(self._audit)

    def plan(
        self,
        *,
        operation_id: str,
        resource_kind: str,
        target: Mapping[str, Any],
        request_body: Any = None,
        identity: str,
        ttl_seconds: Optional[int] = None,
    ) -> ToolResult[dict[str, Any]]:
        """Create a plan token bound to operation, target, body, and identity."""
        try:
            self._validate_target(resource_kind, target)
        except ValueError as exc:
            return ToolResult.failure(str(exc), code="INVALID_PLAN")
        if not operation_id or not identity:
            return ToolResult.failure("operationId and identity are required", code="INVALID_PLAN")
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            return ToolResult.failure("ttlSeconds must be positive", code="INVALID_PLAN")
        token = secrets.token_urlsafe(32)
        plan = DestructivePlan(
            token=token,
            operation_id=operation_id,
            resource_kind=resource_kind.lower(),
            target=dict(target),
            request_hash=self._request_hash(request_body),
            identity=identity,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
        )
        self._plans[token] = plan
        self._record("planned", plan, ok=True)
        return ToolResult.success(
            plan.as_dict(), summary="Destructive operation planned", state={"planned": True}
        )

    def _claim(
        self,
        token: str,
        *,
        operation_id: str,
        resource_kind: str,
        target: Mapping[str, Any],
        request_body: Any,
        identity: str,
    ) -> DestructivePlan:
        plan = self._plans.get(token)
        now = datetime.now(timezone.utc)
        if plan is None:
            raise ValueError("unknown destructive plan token")
        if token in self._consumed:
            raise ValueError("destructive plan token has already been consumed")
        if plan.expires_at <= now:
            self._plans.pop(token, None)
            raise ValueError("destructive plan token has expired")
        if not hmac.compare_digest(plan.operation_id, operation_id):
            raise ValueError("operationId does not match the plan")
        if not hmac.compare_digest(plan.resource_kind, resource_kind.lower()):
            raise ValueError("resourceKind does not match the plan")
        if not hmac.compare_digest(plan.identity, identity):
            raise ValueError("identity does not match the plan")
        if plan.target != dict(target):
            raise ValueError("target does not match the plan")
        if not hmac.compare_digest(plan.request_hash, self._request_hash(request_body)):
            raise ValueError("request body does not match the plan")
        self._consumed.add(token)
        return plan

    async def execute(
        self,
        token: str,
        *,
        operation_id: str,
        resource_kind: str,
        target: Mapping[str, Any],
        request_body: Any = None,
        identity: str,
        executor: Callable[[], Awaitable[Any]],
    ) -> ToolResult[Any]:
        """Validate and consume a plan before invoking the destructive executor."""
        plan = self._plans.get(token)
        try:
            plan = self._claim(
                token,
                operation_id=operation_id,
                resource_kind=resource_kind,
                target=target,
                request_body=request_body,
                identity=identity,
            )
        except ValueError as exc:
            if plan is not None:
                self._record("rejected", plan, ok=False, error=str(exc))
            return ToolResult.failure(str(exc), code="INVALID_PLAN")
        try:
            result = await executor()
        except Exception as exc:
            self._record("executed", plan, ok=False, error="upstream failure")
            return ToolResult.failure(
                f"destructive operation failed: {exc}",
                code="UPSTREAM_DELETE_FAILED",
                details={"operationId": plan.operation_id, "resourceKind": plan.resource_kind},
            )
        self._record("executed", plan, ok=True)
        return ToolResult.success(
            result,
            summary="Destructive operation executed",
            state={
                "executed": True,
                "operationId": plan.operation_id,
                "resourceKind": plan.resource_kind,
            },
        )
