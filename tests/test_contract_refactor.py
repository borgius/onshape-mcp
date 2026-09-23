"""High-signal contract tests for the refactored service boundaries."""

import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from onshape_mcp.api.registry import (
    OperationRegistry,
    OperationRegistryError,
    OperationSpec,
    registry_diff,
)
from onshape_mcp.models.references import OnshapeTarget
from onshape_mcp.models.results import ToolResult
from onshape_mcp.services.destructive import DestructiveOperationGate
from onshape_mcp.services.featurescript import FeatureScriptWorkflowService
from onshape_mcp.services.gateway import OpenAPIGateway
from onshape_mcp.services.translations import TranslationService


def _operation(
    operation_id: str,
    *,
    method: str = "get",
    classification: str = "read",
    reviewed: bool = True,
    scopes: tuple[str, ...] = (),
    summary: str = "",
    parameters: tuple[dict, ...] = (),
    request_body: dict | None = None,
) -> OperationSpec:
    return OperationSpec(
        operation_id=operation_id,
        method=method,
        path=f"/api/{operation_id}",
        classification=classification,
        reviewed=reviewed,
        scopes=scopes,
        summary=summary,
        parameters=parameters,
        request_body=request_body,
    )


class TestOperationRegistryContracts:
    def test_validate_parameters_rejects_unknown_and_missing_parameters(self):
        operation = _operation(
            "find_parts",
            parameters=(
                {
                    "name": "documentId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ),
        )

        with pytest.raises(OperationRegistryError, match="unknown parameters.*workspaceId"):
            operation.validate_parameters({"workspaceId": "ws"})
        with pytest.raises(
            OperationRegistryError, match="missing required path parameter.*documentId"
        ):
            operation.validate_parameters({})

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({}, "body is missing required properties: mode"),
            ({"mode": "safe", "count": "3"}, "body.count must be integer"),
            ({"mode": "unsafe"}, "body.mode must be one of"),
        ],
        ids=["required-property", "type", "enum"],
    )
    def test_validate_parameters_enforces_body_schema(self, body, message):
        operation = _operation(
            "compile_feature",
            method="post",
            classification="write",
            request_body={
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["mode"],
                            "properties": {
                                "mode": {"type": "string", "enum": ["safe", "fast"]},
                                "count": {"type": "integer"},
                            },
                        }
                    }
                },
            },
        )

        with pytest.raises(OperationRegistryError, match=message):
            operation.validate_parameters({}, body=body)

    def test_registry_diff_reports_added_removed_and_changed_operations(self):
        expected = OperationRegistry(
            [
                _operation("kept", summary="before"),
                _operation("removed"),
            ]
        )
        actual = OperationRegistry(
            [
                _operation("kept", summary="after"),
                _operation("added"),
            ]
        )

        assert registry_diff(expected, actual) == {
            "added": ["added"],
            "removed": ["removed"],
            "changed": ["kept"],
        }


class TestOpenAPIGatewayContracts:
    @pytest.mark.asyncio
    async def test_query_rejects_missing_authenticated_scope_before_execution(self):
        operation = _operation("list_parts", scopes=("parts:read",))
        registry = OperationRegistry([operation])
        client = Mock()
        client.execute = AsyncMock(return_value={"parts": []})
        gateway = OpenAPIGateway(client, registry, authenticated_scopes=frozenset())

        result = await gateway.query("list_parts")

        assert not result.ok
        assert result.diagnostics[0].code == "AUTH_SCOPE_REQUIRED"
        client.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mutate_rejects_unreviewed_operation_before_execution(self):
        operation = _operation(
            "update_part",
            method="post",
            classification="write",
            reviewed=False,
        )
        registry = OperationRegistry([operation])
        client = Mock()
        client.execute = AsyncMock(return_value={"updated": True})
        gateway = OpenAPIGateway(client, registry)

        result = await gateway.mutate("update_part", body={"name": "new"})

        assert not result.ok
        assert result.diagnostics[0].code == "REVIEW_REQUIRED"
        client.execute.assert_not_awaited()


class TestDestructiveOperationGateContracts:
    def test_plan_rejects_non_allowlisted_resource_kind(self):
        gate = DestructiveOperationGate()

        result = gate.plan(
            operation_id="delete_object",
            resource_kind="credential",
            target={"documentId": "doc-1"},
            identity="operator@example.com",
        )

        assert not result.ok
        assert result.diagnostics[0].code == "INVALID_PLAN"
        assert gate.audit_records() == ()

    def test_plan_and_audit_redact_identity_and_never_store_request_body(self):
        gate = DestructiveOperationGate()
        identity = "operator@example.com"
        secret_body = {"reason": "contains-sensitive-value"}

        result = gate.plan(
            operation_id="delete_object",
            resource_kind="document",
            target={"documentId": "doc-1"},
            request_body=secret_body,
            identity=identity,
        )

        assert result.ok
        assert result.data["identity"] != identity
        assert identity not in repr(result.data)
        audit = gate.audit_records()[0]
        assert audit["identity"] != identity
        assert identity not in repr(audit)
        assert "requestBody" not in audit
        assert "contains-sensitive-value" not in repr(audit)


class TestModelContracts:
    @pytest.mark.parametrize(
        "selectors",
        [
            {},
            {"workspaceId": "ws", "versionId": "v"},
            {"workspaceId": "ws", "microversionId": "mv"},
        ],
        ids=["none", "workspace-and-version", "workspace-and-microversion"],
    )
    def test_onshape_target_requires_exactly_one_revision_selector(self, selectors):
        with pytest.raises(ValidationError, match="exactly one"):
            OnshapeTarget(documentId="doc", **selectors)

    def test_onshape_target_accepts_one_revision_selector(self):
        target = OnshapeTarget(documentId="doc", versionId="v", elementId="element")

        assert target.as_api_params() == {
            "documentId": "doc",
            "versionId": "v",
            "elementId": "element",
        }

    def test_tool_result_retains_retryable_diagnostic_details(self):
        result = ToolResult.failure(
            "upstream temporarily unavailable",
            code="UPSTREAM_ERROR",
            retryable=True,
            request_id="request-7",
            details={"attempt": 2},
        )

        assert not result.ok
        assert result.retryable is True
        assert result.request_id == "request-7"
        assert result.diagnostics[0].code == "UPSTREAM_ERROR"
        assert result.diagnostics[0].retryable is True
        assert result.diagnostics[0].details == {"attempt": 2}


class _TranslationExportFake:
    def __init__(self, responses):
        self.get_translation_status = AsyncMock(side_effect=responses)


class TestTranslationServiceContracts:
    @pytest.mark.asyncio
    async def test_polling_returns_terminal_success_and_download_reference(self, monkeypatch):
        export = _TranslationExportFake(
            [
                {"requestState": "IN_PROGRESS"},
                {"requestState": "DONE", "resultExternalData": "resource-42"},
            ]
        )
        service = TranslationService(export)
        monotonic_values = [0.0, 0.0, 0.0]
        monkeypatch.setattr(
            time, "monotonic", lambda: monotonic_values.pop(0) if monotonic_values else 0.0
        )

        async def no_wait(_delay):
            return None

        monkeypatch.setattr(asyncio, "sleep", no_wait)
        result = await service.get_translation(
            "translation-1", poll=True, timeout_seconds=10, interval_seconds=1
        )

        assert result.ok
        assert result.state == {"translationId": "translation-1", "requestState": "DONE"}
        assert result.data["downloadReference"] == "resource-42"
        assert result.next_actions == []
        assert export.get_translation_status.await_count == 2

    @pytest.mark.asyncio
    async def test_polling_timeout_is_retryable_and_reports_last_state(self, monkeypatch):
        export = _TranslationExportFake(
            [{"requestState": "IN_PROGRESS"}, {"requestState": "IN_PROGRESS"}]
        )
        service = TranslationService(export)
        monotonic_values = [0.0, 0.0, 2.0]
        monkeypatch.setattr(
            time, "monotonic", lambda: monotonic_values.pop(0) if monotonic_values else 2.0
        )
        sleep = AsyncMock(return_value=None)
        monkeypatch.setattr(asyncio, "sleep", sleep)
        result = await service.get_translation(
            "translation-2", poll=True, timeout_seconds=1, interval_seconds=1
        )

        assert not result.ok
        assert result.diagnostics[0].code == "TRANSLATION_TIMEOUT"
        assert result.retryable is True
        assert result.diagnostics[0].details == {
            "translationId": "translation-2",
            "lastState": "IN_PROGRESS",
        }
        assert export.get_translation_status.await_count == 2


class TestFeatureScriptWorkflowContracts:
    @pytest.mark.asyncio
    async def test_compile_errors_block_regeneration_and_geometry_inspection(self):
        feature_manager = Mock()
        feature_manager.client = Mock()
        feature_manager.client.execute = AsyncMock(
            return_value={
                "diagnostics": [
                    {"severity": "error", "code": "FS001", "message": "unknown variable"}
                ]
            }
        )
        feature_manager.get_bounding_box = AsyncMock(return_value={"minCorner": {"x": 0}})
        partstudio_manager = Mock()
        partstudio_manager.get_body_details = AsyncMock(return_value={"faces": []})
        service = FeatureScriptWorkflowService(
            feature_manager,
            partstudio_manager=partstudio_manager,
        )
        target = OnshapeTarget(documentId="doc", workspaceId="ws", elementId="element")

        result = await service.test_source(
            target,
            OperationRegistry(),
            compile_operation_id="compile_feature",
            path_params={"elementId": "element"},
            regenerate_operation_id="regenerate_feature",
            inspect_geometry=True,
        )

        assert not result.ok
        assert result.summary == "FeatureScript compilation failed"
        assert result.state == {"compiled": False, "regenerated": False}
        assert result.diagnostics[0].code == "FS001"
        feature_manager.client.execute.assert_awaited_once()
        feature_manager.get_bounding_box.assert_not_awaited()
        partstudio_manager.get_body_details.assert_not_awaited()
        assert "regeneration" not in result.data
        assert "boundingBox" not in result.data
        assert "bodyDetails" not in result.data
