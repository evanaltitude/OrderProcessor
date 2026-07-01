from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import re
from typing import Any, Protocol
from urllib import request as urlrequest

from .data_model import CONTAINER_NAMES
from .models import OrderLine


DEFAULT_GOOGLE_AUTH_DOCUMENT_ID = "third-party-service-authentication"
DEFAULT_GOOGLE_PROCESS_ENDPOINT = (
    "https://us-documentai.googleapis.com/v1/"
    "projects/317683569811/locations/us/processors/d3e3bcfffcbad47c:process"
)
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


class DocumentAiClient(Protocol):
    def process_pdf(
        self,
        payload: dict[str, Any],
        *,
        repository: Any,
        tenant_id: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a raw Google Document AI process response."""


@dataclass(slots=True)
class GoogleDocumentAiClient:
    token_endpoint: str = GOOGLE_TOKEN_ENDPOINT
    process_endpoint: str = DEFAULT_GOOGLE_PROCESS_ENDPOINT

    def process_pdf(
        self,
        payload: dict[str, Any],
        *,
        repository: Any,
        tenant_id: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        jwt = google_document_ai_jwt_from_repository(repository, tenant_id, settings)
        token = self._access_token(jwt)
        source = google_document_ai_source(payload, settings)
        endpoint = (
            settings.get("googleDocumentAiProcessEndpoint")
            or settings.get("googleDocumentAiEndpoint")
            or os.environ.get("GOOGLE_DOCUMENT_AI_PROCESS_ENDPOINT")
            or self.process_endpoint
        )
        request_body = {
            "rawDocument": {
                "content": base64.b64encode(source["content"]).decode("ascii"),
                "mimeType": source["mimeType"],
            }
        }
        req = urlrequest.Request(
            str(endpoint),
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=_timeout(settings)) as response:  # noqa: S310 - configured Google endpoint.
            body = response.read().decode("utf-8")
        parsed = json.loads(body or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("Google Document AI returned a non-object response.")
        return parsed

    def _access_token(self, jwt: str) -> str:
        req = urlrequest.Request(
            self.token_endpoint,
            data=json.dumps(
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": jwt,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=30) as response:  # noqa: S310 - Google OAuth token endpoint.
            body = json.loads(response.read().decode("utf-8") or "{}")
        token = str(body.get("access_token") or "").strip()
        if not token:
            raise ValueError("Google OAuth token response did not include access_token.")
        return token


def google_document_ai_jwt_from_repository(
    repository: Any,
    tenant_id: str,
    settings: dict[str, Any] | None = None,
) -> str:
    settings = settings or {}
    direct = (
        settings.get("googleDocumentAiJwt")
        or settings.get("googleJwt")
        or os.environ.get("GOOGLE_DOCUMENT_AI_JWT")
        or ""
    )
    if str(direct).strip():
        return str(direct).strip()

    document_id = str(
        settings.get("googleAuthDocumentId")
        or settings.get("thirdPartyServiceAuthDocumentId")
        or os.environ.get("GOOGLE_DOCUMENT_AI_AUTH_DOCUMENT_ID")
        or DEFAULT_GOOGLE_AUTH_DOCUMENT_ID
    )
    containers = _candidate_auth_containers(repository, settings)
    candidate_ids = _candidate_auth_document_ids(document_id, tenant_id)

    for container in containers:
        for candidate_id in candidate_ids:
            document = _repository_get(repository, container, candidate_id)
            jwt = _jwt_from_auth_document(document)
            if jwt:
                return jwt
    jwt = _jwt_from_external_auth_cosmos(settings, document_id)
    if jwt:
        return jwt

    raise ValueError(
        "Google Document AI JWT was not found. Expected a third-party-service-authentication document "
        "with authentications containing id/serviceId 'google', or a direct google authentication document."
    )


def google_document_ai_source(payload: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    attachment = _selected_pdf_attachment(payload)
    source: Any = None
    encoding = ""
    mime_type = str(
        _pick(payload, "mimeType", "contentType", "content_type", default="")
        or settings.get("mimeType")
        or "application/pdf"
    )
    if attachment:
        source = _pick(attachment, "contentBytes", "contentBase64", "sourceContentBase64", default=None)
        encoding = "base64"
        mime_type = str(_pick(attachment, "mimeType", "contentType", "content_type", default=mime_type) or mime_type)
        if source is None:
            source = _pick(attachment, "content", "sourceContent", default=None)
            encoding = str(_pick(attachment, "sourceEncoding", "encoding", default="") or "")
    if source is None:
        source = _pick(payload, "sourceContentBase64", "source_content_base64", "contentBase64", "content_base64", default=None)
        encoding = "base64"
    if source is None:
        source = _pick(payload, "sourceContent", "source_content", "content", default=b"")
        encoding = str(_pick(payload, "sourceEncoding", "source_encoding", default=settings.get("sourceEncoding", "")) or "")
    content = _as_bytes(source, encoding)
    if not content:
        raise ValueError("PDF source content is required for Google Document AI processing.")
    return {"content": content, "mimeType": mime_type or "application/pdf"}


def extract_order_from_google_document_ai_response(
    response: dict[str, Any],
    payload: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    settings = settings or {}
    document = _as_dict(response.get("document"))
    text = str(document.get("text") or "")
    entities = [entity for entity in _as_list(document.get("entities")) if isinstance(entity, dict)]
    headers = _header_entities(entities)
    lines = _line_items(entities)
    purchase_order = _purchase_order(headers)
    customer_identification = _customer_identification(headers, text, payload)
    return {
        "schemaVersion": "google-document-ai-order-v1",
        "status": "ready" if lines else "needsReview",
        "purchaseOrder": purchase_order,
        "lineCount": len(lines),
        "lines": lines,
        "customerIdentification": customer_identification,
        "headers": headers,
        "rawDocument": {
            "mimeType": document.get("mimeType", ""),
            "textPreview": text[:2500],
            "pageCount": len(_as_list(document.get("pages"))),
            "entityCount": len(entities),
        },
        "googleDocumentAi": {
            "processEndpoint": settings.get("googleDocumentAiProcessEndpoint")
            or settings.get("googleDocumentAiEndpoint")
            or os.environ.get("GOOGLE_DOCUMENT_AI_PROCESS_ENDPOINT")
            or DEFAULT_GOOGLE_PROCESS_ENDPOINT,
            "processor": "googleDocumentAi",
            "entityTypes": sorted({str(entity.get("type") or "") for entity in entities if entity.get("type")}),
        },
        "warnings": [],
        "errors": [] if lines else [{"code": "noOrderLines", "message": "No valid line_item entities with quantity were extracted."}],
        "requiresHumanReview": not bool(lines),
    }


def order_lines_from_google_extraction(extraction: dict[str, Any]) -> list[OrderLine]:
    lines: list[OrderLine] = []
    for item in _as_list(extraction.get("lines")):
        if not isinstance(item, dict):
            continue
        line = OrderLine(
            line_number=_parse_int(item.get("lineNumber"), len(lines) + 1) or len(lines) + 1,
            quantity=_parse_decimal(item.get("quantity")),
            provided_item_number=str(item.get("providedItemNumber") or ""),
            provided_upc=str(item.get("providedUpc") or ""),
            description=str(item.get("description") or ""),
            unit=str(item.get("unit") or "EA"),
            unit_price=_parse_decimal(item.get("unitPrice")),
            source_row_index=_parse_int(item.get("sourceRowIndex"), None),
            raw=dict(item.get("raw") or {}),
        )
        lines.append(line)
    return lines


def _repository_get(repository: Any, container: str, document_id: str) -> dict[str, Any] | None:
    if repository is None:
        return None
    try:
        document = repository.get(container, document_id)
    except (AttributeError, ValueError):
        document = None
    if isinstance(document, dict):
        return document
    database = getattr(repository, "database", None)
    if database is None:
        return None
    try:
        container_client = database.get_container_client(container)
        rows = list(
            container_client.query_items(
                query="SELECT * FROM c WHERE c.id = @id",
                parameters=[{"name": "@id", "value": document_id}],
                enable_cross_partition_query=True,
            )
        )
    except Exception:
        return None
    document = rows[0] if rows else None
    return document if isinstance(document, dict) else None


def _candidate_auth_containers(repository: Any, settings: dict[str, Any]) -> list[str]:
    configured = str(
        settings.get("googleAuthContainer")
        or settings.get("thirdPartyServiceAuthContainer")
        or os.environ.get("GOOGLE_DOCUMENT_AI_AUTH_CONTAINER")
        or "tenants"
    ).strip()
    containers: list[str] = []
    for name in (configured, "tenants", "aiCostSources", *CONTAINER_NAMES, *_cosmos_container_names(repository)):
        if name and name not in containers:
            containers.append(name)
    return containers


def _candidate_auth_document_ids(document_id: str, tenant_id: str) -> list[str]:
    ids: list[str] = []
    for value in (document_id, "google", tenant_id):
        candidate = str(value or "").strip()
        if candidate and candidate not in ids:
            ids.append(candidate)
    return ids


def _cosmos_container_names(repository: Any) -> list[str]:
    database = getattr(repository, "database", None)
    if database is None:
        return []
    try:
        containers = database.list_containers()
    except Exception:
        return []
    names: list[str] = []
    for item in containers:
        name = ""
        if isinstance(item, dict):
            name = str(item.get("id") or item.get("name") or "").strip()
        else:
            name = str(getattr(item, "id", "") or getattr(item, "name", "") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _jwt_from_auth_document(document: dict[str, Any] | None) -> str:
    if not isinstance(document, dict):
        return ""
    authentications = document.get("authentications")
    if isinstance(authentications, list):
        for item in authentications:
            jwt = _jwt_from_auth_item(item)
            if jwt:
                return jwt
    if isinstance(authentications, dict):
        for key, item in authentications.items():
            if isinstance(item, dict):
                candidate = dict(item)
                candidate.setdefault("id", key)
            else:
                candidate = {"id": key, "serviceId": key, "jwt": item}
            jwt = _jwt_from_auth_item(candidate)
            if jwt:
                return jwt
    settings = document.get("settings")
    if isinstance(settings, dict):
        jwt = _jwt_from_auth_document(settings)
        if jwt:
            return jwt
    if str(document.get("id") or "").lower() == "google" and str(document.get("serviceId") or document.get("service_id") or "").lower() == "google":
        return str(document.get("jwt") or "").strip()
    return ""


def _jwt_from_external_auth_cosmos(settings: dict[str, Any], document_id: str) -> str:
    endpoint = str(
        settings.get("googleAuthCosmosEndpoint")
        or settings.get("thirdPartyServiceAuthCosmosEndpoint")
        or os.environ.get("GOOGLE_DOCUMENT_AI_AUTH_COSMOS_ENDPOINT")
        or ""
    ).strip()
    if not endpoint:
        return ""
    database_name = str(
        settings.get("googleAuthCosmosDatabase")
        or settings.get("thirdPartyServiceAuthCosmosDatabase")
        or os.environ.get("GOOGLE_DOCUMENT_AI_AUTH_COSMOS_DATABASE")
        or "third-party-authentications"
    ).strip()
    container_name = str(
        settings.get("googleAuthCosmosContainer")
        or settings.get("thirdPartyServiceAuthCosmosContainer")
        or os.environ.get("GOOGLE_DOCUMENT_AI_AUTH_COSMOS_CONTAINER")
        or "authentications"
    ).strip()
    if not database_name or not container_name:
        return ""
    try:
        from azure.cosmos import CosmosClient
        from azure.identity import DefaultAzureCredential
    except ModuleNotFoundError:
        return ""

    credential: Any = DefaultAzureCredential()
    key = str(
        settings.get("googleAuthCosmosKey")
        or settings.get("thirdPartyServiceAuthCosmosKey")
        or os.environ.get("GOOGLE_DOCUMENT_AI_AUTH_COSMOS_KEY")
        or ""
    ).strip()
    if key:
        credential = key
    try:
        container = CosmosClient(endpoint, credential=credential).get_database_client(database_name).get_container_client(container_name)
        for candidate_id in ("google", document_id):
            rows = list(
                container.query_items(
                    query="SELECT * FROM c WHERE c.id = @id OR c.serviceId = @serviceId OR c.service_id = @serviceId",
                    parameters=[
                        {"name": "@id", "value": candidate_id},
                        {"name": "@serviceId", "value": "google"},
                    ],
                    enable_cross_partition_query=True,
                )
            )
            for row in rows:
                jwt = _jwt_from_auth_document(row if isinstance(row, dict) else None)
                if jwt:
                    return jwt
    except Exception:
        return ""
    return ""


def _jwt_from_auth_item(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    item_id = str(item.get("id") or "").lower()
    service_id = str(item.get("serviceId") or item.get("service_id") or "").lower()
    if item_id == "google" and service_id == "google":
        return str(item.get("jwt") or "").strip()
    return ""


def _selected_pdf_attachment(payload: dict[str, Any]) -> dict[str, Any] | None:
    for item in _as_list(_pick(payload, "attachments", default=[])):
        if not isinstance(item, dict):
            continue
        name = str(_pick(item, "name", "fileName", "file_name", default="") or "").lower()
        content_type = str(_pick(item, "contentType", "content_type", "mimeType", default="") or "").lower()
        if name.endswith(".pdf") or content_type == "application/pdf":
            return item
    return None


def _header_entities(entities: list[dict[str, Any]]) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    aliases = {
        "purchase_order": "purchaseOrder",
        "remit_to_name": "remitToName",
        "remit_to_address": "remitToAddress",
        "ship_to_name": "shipToName",
        "ship_to_address": "shipToAddress",
    }
    for entity in entities:
        entity_type = str(entity.get("type") or "")
        key = aliases.get(entity_type)
        if not key:
            continue
        value = _entity_text(entity)
        if value and not headers.get(key):
            headers[key] = value
        headers.setdefault("entities", []).append(_entity_summary(entity))
    return headers


def _purchase_order(headers: dict[str, Any]) -> str:
    value = str(headers.get("purchaseOrder") or "").strip()
    today = datetime.now(UTC).strftime("%Y%m%d")
    if not value:
        return today
    if value.upper() == "NOLABEL":
        return f"PO{today}"
    return value


def _line_items(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for entity in entities:
        if str(entity.get("type") or "") != "line_item":
            continue
        properties = [item for item in _as_list(entity.get("properties")) if isinstance(item, dict)]
        values: dict[str, str] = {}
        property_summaries: list[dict[str, Any]] = []
        for item in properties:
            property_type = str(item.get("type") or "")
            text = _entity_text(item)
            property_summaries.append(_entity_summary(item))
            if property_type in {"description", "item_number", "upc_number", "quantity", "unit_price", "unit"} and text:
                values[property_type] = text
        quantity = _parse_decimal(str(values.get("quantity", "")).replace(" ", ""))
        if quantity is None or quantity <= 0:
            continue
        line = {
            "lineNumber": len(lines) + 1,
            "sourceRowIndex": len(lines) + 1,
            "providedItemNumber": _clean_identifier(values.get("item_number", "")),
            "providedUpc": _clean_identifier(values.get("upc_number", "")),
            "description": _clean_text(values.get("description", "")),
            "quantity": quantity,
            "unit": _clean_text(values.get("unit", "")) or "EA",
            "unitPrice": _parse_decimal(values.get("unit_price")),
            "raw": {
                "googleEntityId": entity.get("id", ""),
                "mentionText": entity.get("mentionText", ""),
                "confidence": entity.get("confidence", None),
                "properties": property_summaries,
            },
        }
        lines.append(line)
    return lines


def _customer_identification(headers: dict[str, Any], document_text: str, payload: dict[str, Any]) -> dict[str, Any]:
    remit_to_name = str(headers.get("remitToName") or "")
    ship_to_name = str(headers.get("shipToName") or "")
    if "mutts &" in remit_to_name.lower() and ship_to_name:
        ship_to_name = f"{remit_to_name} - {ship_to_name}"
    ship_to_address = str(headers.get("shipToAddress") or "")
    remit_to_address = str(headers.get("remitToAddress") or "")
    ship_to = f"Name: {ship_to_name}\nAddress: {ship_to_address}".strip()
    bill_to = f"Name: {remit_to_name}\nAddress: {remit_to_address}".strip()
    signals = {
        "shipTo": ship_to,
        "billTo": bill_to,
        "shipToName": ship_to_name,
        "shipToAddress": ship_to_address,
        "billToName": remit_to_name,
        "billToAddress": remit_to_address,
        "sender": _pick(payload, "sender", default=""),
        "subject": _pick(payload, "subject", default=""),
        "documentTextPreview": _clean_text(document_text)[:1500],
    }
    search_parts = [
        "shipTo:\n" + ship_to if ship_to.strip() else "",
        "billTo:\n" + bill_to if bill_to.strip() else "",
        f"email sender: {signals['sender']}" if signals.get("sender") else "",
        f"email subject: {signals['subject']}" if signals.get("subject") else "",
        "document text:\n" + _clean_text(document_text)[:8000] if document_text.strip() else "",
    ]
    return {
        "customerSearchText": "\n".join(dict.fromkeys(part for part in search_parts if part.strip()))[:12000],
        "signals": {key: value for key, value in signals.items() if _has_value(value)},
        "instructions": [
            "Use exact ship-to location before bill-to/remit-to when identifying the customer.",
            "Use bill-to/remit-to only when ship-to is incomplete or absent.",
        ],
    }


def _entity_text(entity: dict[str, Any]) -> str:
    mention = str(entity.get("mentionText") or "").strip()
    if mention:
        return mention
    normalized = entity.get("normalizedValue")
    if isinstance(normalized, dict):
        return str(normalized.get("text") or "").strip()
    anchor = entity.get("textAnchor")
    if isinstance(anchor, dict):
        return str(anchor.get("content") or "").strip()
    return ""


def _entity_summary(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entity.get("id", ""),
        "type": entity.get("type", ""),
        "mentionText": entity.get("mentionText", ""),
        "normalizedText": _pick(_as_dict(entity.get("normalizedValue")), "text", default=""),
        "confidence": entity.get("confidence", None),
        "pageRefs": _as_list(_pick(_as_dict(entity.get("pageAnchor")), "pageRefs", default=[]))[:3],
    }


def _as_bytes(value: Any, encoding: str = "") -> bytes:
    if isinstance(value, bytes):
        return value
    text = str(value or "")
    if str(encoding).lower() == "base64":
        return base64.b64decode(text)
    if _looks_base64_pdf(text):
        return base64.b64decode(text)
    return text.encode("utf-8")


def _looks_base64_pdf(value: str) -> bool:
    text = value.strip()
    if not text or len(text) % 4:
        return False
    try:
        return base64.b64decode(text[:64] + ("=" * (-len(text[:64]) % 4))).startswith(b"%PDF")
    except Exception:
        return False


def _clean_identifier(value: Any) -> str:
    return re.sub(r"[\s-]+", "", str(value or "")).strip(" ,;:")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", " ")).strip()


def _parse_decimal(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None


def _parse_int(value: Any, default: int | None = 0) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _timeout(settings: dict[str, Any]) -> int:
    try:
        return max(1, int(settings.get("googleDocumentAiTimeoutSeconds") or 120))
    except (TypeError, ValueError):
        return 120


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _pick(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return default


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []
