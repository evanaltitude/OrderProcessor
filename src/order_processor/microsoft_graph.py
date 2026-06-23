from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError


DEFAULT_GRAPH_SCOPES = [
    "openid",
    "profile",
    "offline_access",
    "User.Read",
    "Mail.ReadWrite.Shared",
    "Mail.Send.Shared",
]


class MicrosoftGraphError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details


@dataclass(frozen=True, slots=True)
class MicrosoftGraphAuthConfig:
    client_id: str
    tenant_authority: str
    redirect_uri: str
    scopes: list[str]
    client_secret: str = ""
    client_secret_name: str = ""

    @property
    def authority_base_url(self) -> str:
        authority = self.tenant_authority.strip() or "organizations"
        if authority.startswith("https://"):
            return authority.rstrip("/")
        return f"https://login.microsoftonline.com/{authority}"

    @property
    def authorize_url(self) -> str:
        return f"{self.authority_base_url}/oauth2/v2.0/authorize"

    @property
    def token_url(self) -> str:
        return f"{self.authority_base_url}/oauth2/v2.0/token"


class InMemorySecretStore:
    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def get_secret(self, name: str) -> str:
        return self._secrets.get(name, "")

    def set_secret(self, name: str, value: str) -> None:
        self._secrets[name] = value


class KeyVaultSecretStore:
    def __init__(self, vault_uri: str) -> None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency path.
            raise MicrosoftGraphError("Azure Key Vault dependencies are not installed.") from exc

        self.client = SecretClient(vault_url=vault_uri, credential=DefaultAzureCredential())

    def get_secret(self, name: str) -> str:
        if not name:
            return ""
        return str(self.client.get_secret(name).value or "")

    def set_secret(self, name: str, value: str) -> None:
        self.client.set_secret(name, value)


def secret_store_from_environment() -> InMemorySecretStore | KeyVaultSecretStore:
    vault_uri = os.environ.get("KEY_VAULT_URI", "").strip()
    if vault_uri:
        return KeyVaultSecretStore(vault_uri)
    return InMemorySecretStore()


def config_from_environment(redirect_uri: str = "") -> MicrosoftGraphAuthConfig:
    scopes = [
        item.strip()
        for item in os.environ.get("ORDER_PROCESSOR_MICROSOFT_AUTH_SCOPES", " ".join(DEFAULT_GRAPH_SCOPES)).replace(
            ",", " "
        ).split()
        if item.strip()
    ]
    return MicrosoftGraphAuthConfig(
        client_id=os.environ.get("ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_SECRET", "").strip(),
        client_secret_name=os.environ.get(
            "ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_SECRET_NAME",
            "microsoft-graph-oauth-client-secret",
        ).strip(),
        tenant_authority=os.environ.get("ORDER_PROCESSOR_MICROSOFT_AUTH_TENANT_ID", "organizations").strip(),
        redirect_uri=(
            redirect_uri
            or os.environ.get("ORDER_PROCESSOR_MICROSOFT_AUTH_REDIRECT_URI", "").strip()
        ),
        scopes=scopes or DEFAULT_GRAPH_SCOPES,
    )


def state_secret_from_environment(fallback: str = "") -> str:
    return (
        os.environ.get("ORDER_PROCESSOR_MICROSOFT_AUTH_STATE_SECRET")
        or os.environ.get("ORDER_PROCESSOR_FUNCTION_SHARED_KEY")
        or fallback
        or "local-development-state-secret"
    )


def build_authorization_url(
    config: MicrosoftGraphAuthConfig,
    state: str,
    *,
    prompt: str = "",
    login_hint: str = "",
) -> str:
    if not config.client_id:
        raise MicrosoftGraphError("Microsoft Graph OAuth client id is not configured.")
    if not config.redirect_uri:
        raise MicrosoftGraphError("Microsoft Graph OAuth redirect URI is not configured.")
    query = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
        "response_mode": "query",
        "scope": " ".join(config.scopes),
        "state": state,
    }
    if prompt:
        query["prompt"] = prompt
    if login_hint:
        query["login_hint"] = login_hint
    return f"{config.authorize_url}?{parse.urlencode(query)}"


def sign_state(payload: dict[str, Any], secret: str, ttl_seconds: int = 900) -> str:
    body = dict(payload)
    body.setdefault("iat", int(time.time()))
    body.setdefault("exp", int(time.time()) + ttl_seconds)
    encoded = _b64url(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64url(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_state(value: str, secret: str) -> dict[str, Any]:
    try:
        encoded, signature = value.split(".", 1)
    except ValueError as exc:
        raise MicrosoftGraphError("Microsoft auth state is malformed.") from exc
    expected = _b64url(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise MicrosoftGraphError("Microsoft auth state signature is invalid.")
    payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise MicrosoftGraphError("Microsoft auth state has expired.")
    return payload


def exchange_authorization_code(
    config: MicrosoftGraphAuthConfig,
    code: str,
    client_secret: str,
) -> dict[str, Any]:
    if not client_secret:
        raise MicrosoftGraphError("Microsoft Graph OAuth client secret is not configured.")
    return _token_request(
        config.token_url,
        {
            "client_id": config.client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "scope": " ".join(config.scopes),
        },
    )


def refresh_access_token(
    config: MicrosoftGraphAuthConfig,
    refresh_token: str,
    client_secret: str,
) -> dict[str, Any]:
    if not refresh_token:
        raise MicrosoftGraphError("Microsoft Graph refresh token is not available.")
    if not client_secret:
        raise MicrosoftGraphError("Microsoft Graph OAuth client secret is not configured.")
    return _token_request(
        config.token_url,
        {
            "client_id": config.client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(config.scopes),
        },
    )


def graph_get(access_token: str, url: str) -> dict[str, Any]:
    req = request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MicrosoftGraphError("Microsoft Graph request failed.", status_code=exc.code, details=detail) from exc
    except URLError as exc:
        raise MicrosoftGraphError(f"Microsoft Graph request failed: {exc.reason}") from exc
    return json.loads(body) if body else {}


def test_shared_mailbox_access(access_token: str, mailbox_address: str) -> dict[str, Any]:
    encoded_mailbox = parse.quote(mailbox_address.strip())
    url = (
        f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/mailFolders/inbox/messages"
        "?$top=1&$select=id,subject,receivedDateTime"
    )
    try:
        result = graph_get(access_token, url)
    except MicrosoftGraphError as exc:
        return {
            "canAccess": False,
            "status": "failed",
            "statusCode": exc.status_code,
            "message": str(exc),
            "details": exc.details,
            "checkedAt": datetime.now(UTC).isoformat(),
        }
    return {
        "canAccess": True,
        "status": "active",
        "message": "Authorized user can access the shared mailbox through Microsoft Graph.",
        "sampleCount": len(result.get("value", [])) if isinstance(result.get("value"), list) else 0,
        "checkedAt": datetime.now(UTC).isoformat(),
    }


def token_expiry(expires_in: Any) -> str:
    seconds = int(expires_in or 3600)
    return (datetime.now(UTC) + timedelta(seconds=max(0, seconds - 60))).isoformat()


def secret_name(*parts: str) -> str:
    raw = "-".join(part for part in parts if part)
    normalized = re.sub(r"[^0-9A-Za-z-]", "-", raw).strip("-").lower()
    return normalized[:120] or "microsoft-graph-token"


def _token_request(url: str, fields: dict[str, str]) -> dict[str, Any]:
    data = parse.urlencode(fields).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MicrosoftGraphError("Microsoft token request failed.", status_code=exc.code, details=detail) from exc
    except URLError as exc:
        raise MicrosoftGraphError(f"Microsoft token request failed: {exc.reason}") from exc
    return json.loads(body) if body else {}


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))
