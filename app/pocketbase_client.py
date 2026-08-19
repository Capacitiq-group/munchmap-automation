"""
Thin async wrapper around the PocketBase REST API. Deliberately thin -
this does not try to be a full ORM. It exposes exactly the operations
the payout, link, and QR services need, so the surface area stays small
and auditable.

Auth model: this service authenticates as a PocketBase admin (not a
per-user token), because it acts on behalf of the platform (running
scheduled jobs, resolving public redirects) rather than on behalf of
any single logged-in user.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class PocketBaseError(RuntimeError):
    """Raised when PocketBase returns a non-2xx response. Carries the
    raw response body so calling code (and logs) can see exactly what
    PocketBase objected to, rather than a generic HTTP error."""

    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"PocketBase returned {status_code}: {body}")


class PocketBaseClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._base_url = settings.pocketbase_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._admin_token: str | None = None

    async def _authenticate(self) -> str:
        """Logs in as the admin account and caches the token for the
        lifetime of this client instance. PocketBase admin tokens are
        short-lived, so a long-running process (like the FastAPI app)
        should expect to re-authenticate periodically - see
        _authenticated_headers, which re-authenticates on a 401."""
        response = await self._client.post(
            f"{self._base_url}/api/admins/auth-with-password",
            json={
                "identity": self._settings.pocketbase_admin_email,
                "password": self._settings.pocketbase_admin_password,
            },
        )
        if response.status_code != 200:
            raise PocketBaseError(response.status_code, response.text)
        token = response.json()["token"]
        self._admin_token = token
        return token

    async def _headers(self) -> dict[str, str]:
        if self._admin_token is None:
            await self._authenticate()
        return {"Authorization": self._admin_token}  # type: ignore[dict-item]

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = await self._headers()
        response = await self._client.request(
            method, f"{self._base_url}{path}", headers=headers, **kwargs
        )
        if response.status_code == 401:
            # Token expired mid-run - re-authenticate once and retry.
            self._admin_token = None
            headers = await self._headers()
            response = await self._client.request(
                method, f"{self._base_url}{path}", headers=headers, **kwargs
            )
        if response.status_code >= 400:
            raise PocketBaseError(response.status_code, response.text)
        return response

    # ---- generic collection helpers ----

    async def list_records(
        self, collection: str, filter_query: str | None = None, per_page: int = 200
    ) -> list[dict[str, Any]]:
        params = {"perPage": per_page}
        if filter_query:
            params["filter"] = filter_query
        response = await self._request("GET", f"/api/collections/{collection}/records", params=params)
        return response.json().get("items", [])

    async def get_record(self, collection: str, record_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/api/collections/{collection}/records/{record_id}")
        return response.json()

    async def create_record(self, collection: str, data: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("POST", f"/api/collections/{collection}/records", json=data)
        return response.json()

    async def update_record(
        self, collection: str, record_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._request(
            "PATCH", f"/api/collections/{collection}/records/{record_id}", json=data
        )
        return response.json()

    async def record_exists(self, collection: str, filter_query: str) -> bool:
        records = await self.list_records(collection, filter_query=filter_query, per_page=1)
        return len(records) > 0

    async def close(self) -> None:
        await self._client.aclose()
