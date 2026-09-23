"""Reviewed OpenAPI operation metadata, validation, and drift support."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")
_READ_METHODS = frozenset({"get", "head", "options"})
_DELETE_METHODS = frozenset({"delete"})


class OperationRegistryError(ValueError):
    """Raised when an operation cannot be safely registered or resolved."""


def _schema_type_matches(value: Any, schema_type: str) -> bool:
    if schema_type == "null":
        return value is None
    if schema_type == "object":
        return isinstance(value, Mapping)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    return True


def _validate_schema(value: Any, schema: Mapping[str, Any], path: str) -> None:
    """Validate the useful OpenAPI/JSON-schema subset without a runtime dependency."""
    if not schema:
        return
    if "oneOf" in schema:
        errors = []
        for option in schema["oneOf"]:
            try:
                _validate_schema(value, option, path)
                return
            except OperationRegistryError as exc:
                errors.append(str(exc))
        raise OperationRegistryError(f"{path} does not match any oneOf schema: {'; '.join(errors)}")
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _validate_schema(value, option, path)
                return
            except OperationRegistryError:
                pass
        raise OperationRegistryError(f"{path} does not match any anyOf schema")
    if "enum" in schema and value not in schema["enum"]:
        raise OperationRegistryError(f"{path} must be one of {schema['enum']!r}")
    schema_type = schema.get("type")
    if schema_type and not _schema_type_matches(value, schema_type):
        raise OperationRegistryError(f"{path} must be {schema_type}")
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        raise OperationRegistryError(f"{path} is shorter than minLength")
    if "maxLength" in schema and isinstance(value, str) and len(value) > schema["maxLength"]:
        raise OperationRegistryError(f"{path} exceeds maxLength")
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        raise OperationRegistryError(f"{path} is below minimum")
    if "maximum" in schema and isinstance(value, (int, float)) and value > schema["maximum"]:
        raise OperationRegistryError(f"{path} exceeds maximum")
    if isinstance(value, Mapping):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise OperationRegistryError(
                f"{path} is missing required properties: {', '.join(missing)}"
            )
        properties = schema.get("properties", {})
        for name, child in properties.items():
            if name in value and isinstance(child, Mapping):
                _validate_schema(value[name], child, f"{path}.{name}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise OperationRegistryError(
                    f"{path} contains unknown properties: {', '.join(unknown)}"
                )
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], f"{path}[{index}]")


@dataclass(frozen=True)
class OperationSpec:
    """The reviewed metadata required to execute one OpenAPI operation safely."""

    operation_id: str
    method: str
    path: str
    classification: str
    deprecated: bool = False
    reviewed: bool = False
    summary: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    parameters: tuple[dict[str, Any], ...] = ()
    request_body: dict[str, Any] | None = None
    responses: dict[str, Any] = field(default_factory=dict)
    scopes: tuple[str, ...] = ()
    pii_classification: str = "unknown"
    pagination: bool = False
    asynchronous: bool = False
    binary_response: bool = False
    idempotent: bool = False
    response_limit: int | None = None

    def __post_init__(self) -> None:
        if not self.operation_id:
            raise OperationRegistryError("operation_id must not be empty")
        method = self.method.lower()
        if method not in _HTTP_METHODS:
            raise OperationRegistryError(f"unsupported HTTP method: {self.method}")
        if not self.path.startswith("/") or self.path.startswith("//"):
            raise OperationRegistryError(f"operation path must be relative: {self.path}")
        if self.classification not in {"read", "write", "delete"}:
            raise OperationRegistryError(f"invalid operation classification: {self.classification}")
        if self.response_limit is not None and self.response_limit <= 0:
            raise OperationRegistryError("response_limit must be positive")

    @property
    def is_read_only(self) -> bool:
        return self.classification == "read"

    @property
    def is_destructive(self) -> bool:
        return self.classification == "delete"

    def expand_path(self, path_params: Mapping[str, Any] | None = None) -> str:
        values = dict(path_params or {})
        names = set(_PATH_PARAMETER.findall(self.path))
        missing = sorted(names - values.keys())
        if missing:
            raise OperationRegistryError(
                f"missing path parameters for {self.operation_id}: {', '.join(missing)}"
            )
        extra = sorted(set(values) - names)
        if extra:
            raise OperationRegistryError(
                f"unknown path parameters for {self.operation_id}: {', '.join(extra)}"
            )
        expanded = self.path
        for name in names:
            value = str(values[name])
            if not value or any(char in value for char in ("/", "\\", "?", "#")):
                raise OperationRegistryError(f"unsafe path parameter {name!r}")
            expanded = expanded.replace("{" + name + "}", value)
        return expanded

    def validate_parameters(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        body: Any = None,
    ) -> None:
        """Validate required and schema-constrained OpenAPI parameters and body."""
        supplied = dict(params or {})
        declared = {parameter.get("name"): parameter for parameter in self.parameters}
        unknown = sorted(set(supplied) - set(declared))
        if unknown:
            raise OperationRegistryError(
                f"unknown parameters for {self.operation_id}: {', '.join(unknown)}"
            )
        for parameter in self.parameters:
            name = parameter.get("name")
            if parameter.get("required") and name not in supplied:
                location = parameter.get("in", "parameter")
                raise OperationRegistryError(
                    f"missing required {location} parameter {name!r} for {self.operation_id}"
                )
            if name in supplied and isinstance(parameter.get("schema"), Mapping):
                _validate_schema(supplied[name], parameter["schema"], f"parameter.{name}")
        if body is not None and self.request_body:
            content = self.request_body.get("content", {})
            media = content.get("application/json") or next(iter(content.values()), {})
            schema = media.get("schema", {}) if isinstance(media, Mapping) else {}
            if isinstance(schema, Mapping):
                _validate_schema(body, schema, "body")


class OperationRegistry:
    """Indexed, reviewed operation metadata loaded from OpenAPI or registry JSON."""

    def __init__(
        self, operations: Iterable[OperationSpec] = (), *, reviewed_only: bool = False
    ) -> None:
        self._operations: dict[str, OperationSpec] = {}
        for operation in operations:
            if reviewed_only and not operation.reviewed:
                continue
            self.register(operation)

    def register(self, operation: OperationSpec) -> None:
        if operation.operation_id in self._operations:
            raise OperationRegistryError(f"duplicate operationId: {operation.operation_id}")
        self._operations[operation.operation_id] = operation

    def get(self, operation_id: str) -> OperationSpec:
        try:
            return self._operations[operation_id]
        except KeyError as exc:
            raise OperationRegistryError(f"unknown operationId: {operation_id}") from exc

    def __contains__(self, operation_id: str) -> bool:
        return operation_id in self._operations

    def __len__(self) -> int:
        return len(self._operations)

    def __iter__(self):
        return iter(self._operations.values())

    def search(
        self,
        query: str = "",
        *,
        classification: str | None = None,
        tag: str | None = None,
        resource_kind: str | None = None,
    ) -> list[OperationSpec]:
        needle = query.lower().strip()
        if classification is not None and classification not in {"read", "write", "delete"}:
            raise OperationRegistryError(f"invalid classification: {classification}")
        matches = []
        for operation in self._operations.values():
            haystack = " ".join(
                [operation.operation_id, operation.summary, operation.description, *operation.tags]
            ).lower()
            if needle and needle not in haystack:
                continue
            if classification is not None and operation.classification != classification:
                continue
            if tag is not None and tag not in operation.tags:
                continue
            if resource_kind is not None and resource_kind.lower() not in haystack:
                continue
            matches.append(operation)
        return sorted(matches, key=lambda item: item.operation_id)

    def to_dict(self) -> dict[str, Any]:
        return {operation.operation_id: asdict(operation) for operation in self}

    @classmethod
    def from_openapi(
        cls,
        document: Mapping[str, Any],
        *,
        include_deprecated: bool = False,
        public_only: bool = True,
        reviewed_only: bool = False,
    ) -> "OperationRegistry":
        operations: list[OperationSpec] = []
        paths = document.get("paths", {})
        if not isinstance(paths, Mapping):
            raise OperationRegistryError("OpenAPI paths must be an object")
        for path, path_item in paths.items():
            if not isinstance(path_item, Mapping):
                continue
            inherited = tuple(
                parameter
                for parameter in path_item.get("parameters", [])
                if isinstance(parameter, Mapping)
            )
            for method, raw_operation in path_item.items():
                method_lower = str(method).lower()
                if method_lower not in _HTTP_METHODS or not isinstance(raw_operation, Mapping):
                    continue
                if raw_operation.get("deprecated", False) and not include_deprecated:
                    continue
                if public_only and raw_operation.get("x-internal", False):
                    continue
                operation_id = raw_operation.get("operationId")
                if not operation_id:
                    continue
                classification = raw_operation.get("x-mcp-classification")
                if classification is None:
                    classification = (
                        "read"
                        if method_lower in _READ_METHODS
                        else "delete"
                        if method_lower in _DELETE_METHODS
                        else "write"
                    )
                parameters = inherited + tuple(
                    parameter
                    for parameter in raw_operation.get("parameters", [])
                    if isinstance(parameter, Mapping)
                )
                responses = raw_operation.get("responses", {})
                operation = OperationSpec(
                    operation_id=str(operation_id),
                    method=method_lower,
                    path=str(path),
                    classification=str(classification),
                    deprecated=bool(raw_operation.get("deprecated", False)),
                    reviewed=bool(raw_operation.get("x-mcp-reviewed", False)),
                    summary=str(raw_operation.get("summary", "")),
                    description=str(raw_operation.get("description", "")),
                    tags=tuple(str(tag) for tag in raw_operation.get("tags", [])),
                    parameters=parameters,
                    request_body=(
                        dict(raw_operation["requestBody"])
                        if isinstance(raw_operation.get("requestBody"), Mapping)
                        else None
                    ),
                    responses=dict(responses) if isinstance(responses, Mapping) else {},
                    scopes=tuple(str(scope) for scope in raw_operation.get("x-mcp-scopes", [])),
                    pii_classification=str(raw_operation.get("x-mcp-pii", "unknown")),
                    pagination=bool(raw_operation.get("x-mcp-pagination", False)),
                    asynchronous=bool(raw_operation.get("x-mcp-asynchronous", False)),
                    binary_response=bool(raw_operation.get("x-mcp-binary", False)),
                    idempotent=bool(
                        raw_operation.get("x-mcp-idempotent", method_lower in {"put", "delete"})
                    ),
                    response_limit=raw_operation.get("x-mcp-response-limit"),
                )
                if not reviewed_only or operation.reviewed:
                    operations.append(operation)
        return cls(operations)

    @classmethod
    def from_json_file(
        cls,
        path: str | Path,
        *,
        reviewed_only: bool = False,
        **kwargs: Any,
    ) -> "OperationRegistry":
        """Load either an OpenAPI document or generated registry JSON."""
        with Path(path).open(encoding="utf-8") as stream:
            document = json.load(stream)
        if isinstance(document, Mapping) and "paths" in document:
            return cls.from_openapi(document, reviewed_only=reviewed_only, **kwargs)
        operations = []
        for operation_id, raw in document.items():
            if not isinstance(raw, Mapping):
                continue
            values = dict(raw)
            values["operation_id"] = values.pop("operation_id", operation_id)
            for key in ("tags", "scopes"):
                values[key] = tuple(values.get(key, ()))
            values["parameters"] = tuple(values.get("parameters", ()))
            operations.append(OperationSpec(**values))
        return cls(operations, reviewed_only=reviewed_only)


def registry_diff(expected: OperationRegistry, actual: OperationRegistry) -> dict[str, Any]:
    """Return a deterministic reviewed-registry drift report."""
    expected_data = expected.to_dict()
    actual_data = actual.to_dict()
    return {
        "added": sorted(set(actual_data) - set(expected_data)),
        "removed": sorted(set(expected_data) - set(actual_data)),
        "changed": sorted(
            operation_id
            for operation_id in set(expected_data) & set(actual_data)
            if expected_data[operation_id] != actual_data[operation_id]
        ),
    }
