"""Onshape API client for REST API communication."""

import base64
import httpx
from typing import Any, Dict, Optional
from pydantic import BaseModel
from loguru import logger


class OnshapeCredentials(BaseModel):
    """Onshape API credentials."""

    access_key: str
    secret_key: str
    base_url: str = "https://cad.onshape.com"


class OnshapeClient:
    """Client for interacting with Onshape REST API.

    Use as an async context manager to ensure proper cleanup:
        async with OnshapeClient(credentials) as client:
            result = await client.get("/api/v9/documents")
    """

    def __init__(self, credentials: OnshapeCredentials):
        """Initialize the Onshape client.

        Args:
            credentials: Onshape API credentials (access key and secret key)
        """
        self.credentials = credentials
        self.base_url = credentials.base_url
        self._client: Optional[httpx.AsyncClient] = None
        self._own_client = False

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = httpx.AsyncClient(timeout=30.0)
        self._own_client = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensures cleanup."""
        await self.close()
        return False

    def _ensure_client(self):
        """Ensure HTTP client is initialized."""
        if self._client is None:
            # Create client if not using context manager (backwards compatibility)
            self._client = httpx.AsyncClient(timeout=30.0)
            self._own_client = True

    def _get_auth_header(self) -> str:
        """Generate Basic Auth header from credentials.

        Returns:
            Authorization header value
        """
        auth_string = f"{self.credentials.access_key}:{self.credentials.secret_key}"
        encoded = base64.b64encode(auth_string.encode()).decode()
        return f"Basic {encoded}"

    def _sanitize_for_logging(self, data: Any, max_length: int = 200) -> str:
        """Sanitize sensitive data for logging.

        Args:
            data: Data to sanitize
            max_length: Maximum length of output string

        Returns:
            Sanitized string safe for logging
        """
        if isinstance(data, dict):
            sanitized = {}
            for k, v in data.items():
                if k.lower() in {
                    "authorization",
                    "api_key",
                    "secret",
                    "password",
                    "token",
                    "access_key",
                    "secret_key",
                }:
                    sanitized[k] = "***REDACTED***"
                else:
                    sanitized[k] = v
            return str(sanitized)[:max_length]

        result = str(data)
        if len(result) > max_length:
            return result[:max_length] + "... (truncated)"
        return result

    def _log_error_response(self, method: str, url: str, response: httpx.Response) -> None:
        """Log an upstream error response with status, request id, and sanitized body.

        The request id is Onshape's ``x-request-id`` correlation header; it is what
        ties a client-side failure to Onshape's own logs.
        """
        req_id = response.headers.get("x-request-id", "")
        id_suffix = f" request_id={req_id}" if req_id else ""
        try:
            body = self._sanitize_for_logging(response.json(), max_length=500)
        except Exception:
            body = self._sanitize_for_logging(response.text, max_length=500)
        logger.error(f"{method} {url} failed: HTTP {response.status_code}{id_suffix} body={body}")

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a GET request to Onshape API.

        Args:
            path: API endpoint path (e.g., "/api/v9/documents")
            params: Query parameters

        Returns:
            JSON response data
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
        }

        self._ensure_client()
        logger.debug(f"GET {url} with params: {self._sanitize_for_logging(params)}")
        response = await self._client.get(url, params=params, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._log_error_response("GET", url, e.response)
            raise
        result = response.json()
        logger.debug(f"GET {url} response: {self._sanitize_for_logging(result, max_length=500)}")
        return result

    async def post(
        self,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make a POST request to Onshape API.

        Args:
            path: API endpoint path
            data: JSON body data
            params: Query parameters

        Returns:
            JSON response data
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
            "Content-Type": "application/json;charset=UTF-8; qs=0.09",
        }

        self._ensure_client()
        logger.debug(f"POST {url} with params: {self._sanitize_for_logging(params)}")
        logger.debug(f"POST {url} data: {self._sanitize_for_logging(data, max_length=1000)}")
        response = await self._client.post(url, json=data, params=params, headers=headers)

        # Log error details if request failed
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._log_error_response("POST", url, e.response)
            raise
        if not response.content:
            logger.debug(f"POST {url} returned empty body (status {response.status_code})")
            return {}
        result = response.json()
        logger.debug(f"POST {url} response: {self._sanitize_for_logging(result, max_length=500)}")
        return result

    async def delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a DELETE request to Onshape API.

        Args:
            path: API endpoint path
            params: Query parameters

        Returns:
            JSON response data
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
        }

        self._ensure_client()
        response = await self._client.delete(url, params=params, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._log_error_response("DELETE", url, e.response)
            raise
        if not response.content:
            return {}
        return response.json()

    async def close(self):
        """Close the HTTP client and clean up resources."""
        if self._client and self._own_client:
            await self._client.aclose()
            self._client = None
