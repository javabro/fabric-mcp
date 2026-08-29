"""Microsoft Fabric REST API client."""

from typing import Any, Dict, Optional

import httpx

from fabric_auth import AzureIdentityAuthProvider

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = ["https://api.fabric.microsoft.com/.default"]


class FabricAPIClient:
    def __init__(self, auth_provider: AzureIdentityAuthProvider) -> None:
        self.auth_provider = auth_provider
        self._base_url = FABRIC_API_BASE

    def _get_access_token(self) -> str:
        return self.auth_provider.get_access_token(FABRIC_SCOPE)

    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        token = self._get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        timeout = kwargs.pop("timeout", 60.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code == 200:
                return response.json()
            raise Exception(f"Fabric API Error {response.status_code}: {response.text}")

    async def get(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(kwargs.pop("params", None) or {})
        first = await self._make_request("GET", endpoint, params=params or None, **kwargs)
        if "value" not in first:
            return first
        values = list(first.get("value") or [])
        token: Optional[str] = first.get("continuationToken")
        while token:
            page_params = dict(params)
            page_params["continuationToken"] = token
            page = await self._make_request("GET", endpoint, params=page_params, **kwargs)
            values.extend(page.get("value") or [])
            token = page.get("continuationToken")
        first["value"] = values
        first.pop("continuationToken", None)
        first.pop("continuationUri", None)
        return first

    async def post(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        return await self._make_request("POST", endpoint, **kwargs)

    async def put(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        return await self._make_request("PUT", endpoint, **kwargs)

    async def delete(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        return await self._make_request("DELETE", endpoint, **kwargs)
