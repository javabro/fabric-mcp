"""Auth for Fabric REST and OneLake: Azure CLI by default, service principal if configured."""

from __future__ import annotations

import os
from typing import List, Union

from azure.identity import ClientSecretCredential, DefaultAzureCredential


Scope = Union[str, List[str]]


class AzureIdentityAuthProvider:
    """Mints Entra tokens via azure-identity (az login or client secret)."""

    def __init__(
        self,
        credential,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self._credential = credential
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id

    @property
    def is_service_principal(self) -> bool:
        return bool(self.client_secret)

    def get_access_token(self, scope: Scope) -> str:
        scopes = [scope] if isinstance(scope, str) else list(scope)
        last_error: Exception | None = None
        for item in scopes:
            try:
                return self._credential.get_token(item).token
            except Exception as exc:  # pragma: no cover - live Entra
                last_error = exc
        raise Exception(f"Could not acquire token: {last_error}") from last_error


def create_auth_provider_from_env() -> AzureIdentityAuthProvider:
    client_id = os.getenv("FABRIC_CLIENT_ID")
    client_secret = os.getenv("FABRIC_CLIENT_SECRET")
    tenant_id = os.getenv("FABRIC_TENANT_ID")
    if client_secret:
        if not client_id or not tenant_id:
            raise ValueError(
                "FABRIC_CLIENT_ID and FABRIC_TENANT_ID are required for service principal auth"
            )
        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        return AzureIdentityAuthProvider(
            credential,
            client_id=client_id,
            client_secret=client_secret,
            tenant_id=tenant_id,
        )
    # ponytail: no device-code flow; az login / DefaultAzureCredential is enough
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return AzureIdentityAuthProvider(credential)
