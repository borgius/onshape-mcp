"""Onshape API client for REST API communication."""

import base64
import json
import os
from typing import Any, Dict, Mapping, Optional

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from .registry import OperationRegistry


class OnshapeCredentials(BaseModel):
    """Onshape API credentials."""

    access_key: str
    secret_key: str
    base_url: str = "https://cad.onshape.com"
    api_versions: dict[str, str] = Field(
        default_factory=lambda: {
            "default": "v9",
            "documents": "v6",
            "documents_write": "v10",
            "featurescript": "v8",
            "featurestudios": "v9",
            "partstudios": "v9",
            "parts": "v6",
            "variables": "v6",
            "translations": "v11",
            "translation_status": "v6",
            "visuals": "v10",
        }
    )


class OnshapeClient:
    """Client for interacting with Onshape API REST and reviewed operations."""

    def __init__(self, credentials: OnshapeCredentials):
        self.credentials = credentials
        self.base_url = credentials.base_url
        self.api_versions = dict(credentials.api_versions)
        configured = os.getenv("ONSHAPE_API_VERSIONS_JSON")
        if configured:
            self.api_versions.update(json.loads(configured))
        self._client: Optional[httpx.AsyncClient] = None
        self._own_client = False

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        self._own_client = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
            self._own_client = True

    def api_path(self, path: str, family: str = "default") -> str:
        """Resolve a configured API version without embedding one in a manager."""
        if not path.startswith("/"):
            raise ValueError("API path must start with '/'")
        version = self.api_versions.get(family) or self.api_versions.get("default")
        if not version:
            raise RuntimeError(f"no configured API version for {family}")
        version = version.strip("/")
        return f"/api/{version}{path}"

    def _get_auth_header(self) -> str:
        encoded = base64.b64encode(
            f"{self.credentials.access_key}:{self.credentials.secret_key}".encode()
        ).decode()
        return f"Basic {encoded}"

    def _sanitize_for_logging(self, data: Any, max_length: int = 200) -> str:
        if isinstance(data, dict):
            sanitized = {}
            for key, value in data.items():
                if key.lower() in {
                    "authorization",
                    "api_key",
                    "secret",
                    "password",
                    "token",
                    "access_key",
                    "secret_key",
                }:
                    sanitized[key] = "***REDACTED***"
                else:
                    sanitized[key] = value
            return str(sanitized)[:max_length]
        result = str(data)
        return result if len(result) <= max_length else result[:max_length] + "... (truncated)"

    def _log_error_response(self, method: str, url: str, response: httpx.Response) -> None:
        req_id = response.headers.get("x-request-id", "")
        id_suffix = f" request_id={req_id}" if req_id else ""
        try:
            body = self._sanitize_for_logging(response.json(), max_length=500)
        except Exception:
            body = self._sanitize_for_logging(response.text, max_length=500)
        logger.error(f"{method} {url} failed: HTTP {response.status_code}{id_suffix} body={body}")

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
        }
        self._ensure_client()
        response = await self._client.get(url, params=params, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._log_error_response("GET", url, exc.response)
            raise
        if not response.content:
            return {}
        return response.json()

    async def post(
        self,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
            "Content-Type": "application/json;charset=UTF-8; qs=0.09",
        }
        self._ensure_client()
        response = await self._client.post(url, json=data, params=params, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._log_error_response("POST", url, exc.response)
            raise
        if not response.content:
            return {}
        return response.json()

    async def delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
        }
        self._ensure_client()
        response = await self._client.delete(url, params=params, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._log_error_response("DELETE", url, exc.response)
            raise
        if not response.content:
            return {}
        return response.json()

    async def execute(
        self,
        registry: OperationRegistry,
        operation_id: str,
        *,
        path_params: Optional[Mapping[str, Any]] = None,
        query_params: Optional[Mapping[str, Any]] = None,
        body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        """Execute a reviewed OpenAPI operation by operation ID."""
        operation = registry.get(operation_id)
        path_values = dict(path_params or {})
        query_values = dict(query_params or {})
        operation.validate_parameters({**path_values, **query_values}, body=body)
        path = operation.expand_path(path_values)
        url = f"{self.base_url}{path}"
        request_headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
        }
        if body is not None:
            request_headers["Content-Type"] = "application/json;charset=UTF-8; qs=0.09"
        if headers:
            request_headers.update(headers)
        self._ensure_client()
        response = await self._client.request(
            operation.method.upper(),
            url,
            params=query_values or None,
            json=body,
            headers=request_headers,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._log_error_response(operation.method.upper(), url, exc.response)
            raise
        if (
            operation.response_limit is not None
            and len(response.content) > operation.response_limit
        ):
            raise ValueError(f"{operation_id} response exceeds configured limit")
        if not response.content:
            return {}
        content_type = response.headers.get("content-type", "")
        if "json" in content_type.lower():
            return response.json()
        return response.content

    async def close(self):
        if self._client and self._own_client:
            await self._client.aclose()
            self._client = None
