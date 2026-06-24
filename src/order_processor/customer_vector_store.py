from __future__ import annotations

from collections import defaultdict
import json
import os
import re
from typing import Any

from .data_model import document_value
from .imports import stable_id
from .models import utc_now


CUSTOMER_VECTOR_STORE_CONTAINER = "customerVectorStores"
CUSTOMER_VECTOR_STORE_REFERENCE_TYPE = "customerListFileSearch"


def customer_vector_store_reference_id(tenant_id: str) -> str:
    return stable_id(tenant_id, CUSTOMER_VECTOR_STORE_REFERENCE_TYPE, "active")


def customer_vector_store_rotation_enabled(payload: dict[str, Any] | None = None) -> bool:
    override = _pick(payload or {}, "rotateCustomerVectorStore", "rotate_customer_vector_store", default=None)
    if override is not None:
        return _truthy(override)
    return _truthy(
        os.environ.get("ORDER_PROCESSOR_ENABLE_CUSTOMER_VECTOR_STORE_ROTATION")
        or os.environ.get("ORDER_PROCESSOR_ENABLE_CUSTOMER_VECTOR_STORES")
    )


def customer_vector_store_manager_from_environment(repository: Any) -> CustomerVectorStoreManager | None:
    if not customer_vector_store_rotation_enabled():
        return None
    return CustomerVectorStoreManager(repository, AzureOpenAICustomerVectorStoreClient())


class CustomerVectorStoreManager:
    def __init__(self, repository: Any, client: Any) -> None:
        self.repository = repository
        self.client = client

    def rotate_after_customer_import(
        self,
        tenant_id: str,
        import_result: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        override = _pick(payload or {}, "rotateCustomerVectorStore", "rotate_customer_vector_store", default=None)
        if override is not None and not _truthy(override):
            return {"enabled": False, "status": "skipped", "reason": "customer vector store rotation is disabled"}

        customers = list(self.repository.query_by_tenant("customers", tenant_id))
        if not customers:
            return {"enabled": True, "status": "skipped", "reason": "no customer records available"}

        aliases = list(self.repository.query_by_tenant("customerAliases", tenant_id))
        reference_id = customer_vector_store_reference_id(tenant_id)
        previous_reference = self.repository.get(CUSTOMER_VECTOR_STORE_CONTAINER, reference_id) or {}
        previous_vector_store_id = str(document_value(previous_reference, "vectorStoreId", "") or "").strip()
        previous_file_id = str(document_value(previous_reference, "fileId", "") or "").strip()

        started_at = utc_now()
        records = customer_vector_store_records(customers, aliases)
        content = customer_vector_store_jsonl(records).encode("utf-8")
        import_run_id = str(_pick(import_result, "importRunId", "import_run_id", default=""))
        name = customer_vector_store_name(tenant_id, import_run_id)
        metadata = {
            "tenantId": tenant_id[:512],
            "sourceImportRunId": import_run_id[:512],
            "contentKind": "customer-list",
            "customerCount": str(len(customers)),
            "recordCount": str(len(records)),
        }

        try:
            created = self.client.create_customer_vector_store(
                name=name,
                filename=f"{name}.txt",
                content=content,
                metadata=metadata,
            )
        except Exception as exc:
            return {
                "enabled": True,
                "status": "failed",
                "error": str(exc),
                "previousVectorStoreId": previous_vector_store_id,
                "previousFileId": previous_file_id,
                "startedAt": started_at,
                "completedAt": utc_now(),
            }

        completed_at = utc_now()
        vector_store_id = str(_pick(created, "vectorStoreId", "vector_store_id", "id", default="")).strip()
        file_id = str(_pick(created, "fileId", "file_id", default="")).strip()
        file_batch_id = str(_pick(created, "fileBatchId", "file_batch_id", "batchId", "batch_id", default="")).strip()
        reference = {
            "id": reference_id,
            "tenantId": tenant_id,
            "referenceType": CUSTOMER_VECTOR_STORE_REFERENCE_TYPE,
            "status": "active",
            "name": name,
            "vectorStoreId": vector_store_id,
            "fileId": file_id,
            "fileBatchId": file_batch_id,
            "sourceImportRunId": import_run_id,
            "sourceRowsBlobUrl": _pick(import_result, "sourceRowsBlobUrl", "source_rows_blob_url", default=""),
            "sourceRowsChecksum": _pick(import_result, "sourceRowsChecksum", "source_rows_checksum", default=""),
            "customerCount": len(customers),
            "aliasCount": len(aliases),
            "recordCount": len(records),
            "contentBytes": len(content),
            "previousVectorStoreId": previous_vector_store_id,
            "previousFileId": previous_file_id,
            "createdAt": completed_at,
            "updatedAt": completed_at,
            "lastRotation": {
                "status": "active",
                "startedAt": started_at,
                "completedAt": completed_at,
                "fileCounts": _pick(created, "fileCounts", "file_counts", default={}),
            },
        }
        self.repository.upsert(CUSTOMER_VECTOR_STORE_CONTAINER, reference)

        cleanup = self._cleanup_previous(previous_vector_store_id, previous_file_id, vector_store_id, file_id)
        if cleanup["attempted"]:
            reference["lastCleanup"] = cleanup
            reference["updatedAt"] = utc_now()
            self.repository.upsert(CUSTOMER_VECTOR_STORE_CONTAINER, reference)

        result = {
            "enabled": True,
            "status": "active",
            "referenceId": reference_id,
            "name": name,
            "vectorStoreId": vector_store_id,
            "fileId": file_id,
            "fileBatchId": file_batch_id,
            "customerCount": len(customers),
            "aliasCount": len(aliases),
            "recordCount": len(records),
            "contentBytes": len(content),
            "previousVectorStoreId": previous_vector_store_id,
            "previousFileId": previous_file_id,
            "cleanup": cleanup,
        }
        return result

    def _cleanup_previous(
        self,
        previous_vector_store_id: str,
        previous_file_id: str,
        active_vector_store_id: str,
        active_file_id: str,
    ) -> dict[str, Any]:
        cleanup = {
            "attempted": False,
            "status": "skipped",
            "deletedFileId": "",
            "deletedVectorStoreId": "",
            "errors": [],
            "completedAt": utc_now(),
        }
        if not previous_vector_store_id and not previous_file_id:
            return cleanup

        cleanup["attempted"] = True
        cleanup["status"] = "completed"
        if previous_file_id and previous_file_id != active_file_id:
            try:
                self.client.delete_file(previous_file_id)
                cleanup["deletedFileId"] = previous_file_id
            except Exception as exc:
                cleanup["status"] = "failed"
                cleanup["errors"].append({"target": "file", "id": previous_file_id, "message": str(exc)})
        if previous_vector_store_id and previous_vector_store_id != active_vector_store_id:
            try:
                self.client.delete_vector_store(previous_vector_store_id)
                cleanup["deletedVectorStoreId"] = previous_vector_store_id
            except Exception as exc:
                cleanup["status"] = "failed"
                cleanup["errors"].append(
                    {"target": "vectorStore", "id": previous_vector_store_id, "message": str(exc)}
                )
        cleanup["completedAt"] = utc_now()
        return cleanup


class AzureOpenAICustomerVectorStoreClient:
    def __init__(
        self,
        endpoint: str | None = None,
        api_version: str | None = None,
        api_key: str | None = None,
        expires_after_days: int | None = None,
        poll_interval_ms: int | None = None,
    ) -> None:
        self.endpoint = endpoint or os.environ.get("AZURE_AI_FOUNDRY_OPENAI_ENDPOINT") or os.environ.get(
            "AZURE_OPENAI_ENDPOINT", ""
        )
        self.api_version = (
            api_version
            or os.environ.get("AZURE_AI_FOUNDRY_OPENAI_API_VERSION")
            or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
        )
        self.api_key = api_key or os.environ.get("AZURE_AI_FOUNDRY_OPENAI_API_KEY") or os.environ.get(
            "AZURE_OPENAI_API_KEY", ""
        )
        self.expires_after_days = expires_after_days or int(
            os.environ.get("ORDER_PROCESSOR_CUSTOMER_VECTOR_STORE_EXPIRES_AFTER_DAYS", "365")
        )
        self.poll_interval_ms = poll_interval_ms or int(
            os.environ.get("ORDER_PROCESSOR_CUSTOMER_VECTOR_STORE_POLL_INTERVAL_MS", "1000")
        )
        if not self.endpoint:
            raise ValueError("AZURE_AI_FOUNDRY_OPENAI_ENDPOINT or AZURE_OPENAI_ENDPOINT is required.")

    def create_customer_vector_store(
        self,
        *,
        name: str,
        filename: str,
        content: bytes,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        client = self._client()
        uploaded_file = client.files.create(
            file=(filename, content, "text/plain"),
            purpose="assistants",
        )
        file_id = _object_value(uploaded_file, "id")
        if not file_id:
            raise RuntimeError("Azure OpenAI file upload did not return a file id.")
        create_kwargs = {
            "name": name,
            "metadata": metadata or {},
            "expires_after": {"anchor": "last_active_at", "days": self.expires_after_days},
        }
        if not hasattr(client.vector_stores, "file_batches"):
            create_kwargs["file_ids"] = [file_id]
        vector_store = client.vector_stores.create(**create_kwargs)
        vector_store_id = _object_value(vector_store, "id")
        if not vector_store_id:
            raise RuntimeError("Azure OpenAI vector store creation did not return a vector store id.")

        file_counts: dict[str, Any] = {}
        file_batch_id = ""
        if hasattr(client.vector_stores, "file_batches"):
            batch = client.vector_stores.file_batches.create_and_poll(
                vector_store_id=vector_store_id,
                file_ids=[file_id],
                poll_interval_ms=self.poll_interval_ms,
            )
            file_batch_id = _object_value(batch, "id")
            file_counts = dict(_object_value(batch, "file_counts", "fileCounts", default={}) or {})
            status = str(_object_value(batch, "status", default="")).lower()
            if status and status not in {"completed", "succeeded"}:
                raise RuntimeError(f"Vector store file batch did not complete: {status}")
        else:
            vector_store = client.vector_stores.retrieve(vector_store_id)
            file_counts = dict(_object_value(vector_store, "file_counts", "fileCounts", default={}) or {})

        return {
            "vectorStoreId": vector_store_id,
            "fileId": file_id,
            "fileBatchId": file_batch_id,
            "fileCounts": file_counts,
        }

    def delete_file(self, file_id: str) -> None:
        if file_id:
            self._client().files.delete(file_id)

    def delete_vector_store(self, vector_store_id: str) -> None:
        if vector_store_id:
            self._client().vector_stores.delete(vector_store_id)

    def _client(self) -> Any:
        try:
            from openai import AzureOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency.
            raise RuntimeError("The openai package is required for Azure OpenAI vector stores.") from exc

        if self.api_key:
            return AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
            )

        try:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency.
            raise RuntimeError("azure-identity is required for managed identity Azure OpenAI auth.") from exc

        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        return AzureOpenAI(
            azure_endpoint=self.endpoint,
            azure_ad_token_provider=token_provider,
            api_version=self.api_version,
        )


def customer_vector_store_name(tenant_id: str, import_run_id: str = "") -> str:
    suffix = re.sub(r"[^a-zA-Z0-9_-]+", "-", import_run_id or utc_now()).strip("-")[:24]
    tenant = re.sub(r"[^a-zA-Z0-9_-]+", "-", tenant_id or "tenant").strip("-")[:40]
    return f"{tenant}-customers-{suffix or 'latest'}"


def customer_vector_store_records(customers: list[dict[str, Any]], aliases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases_by_customer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for alias in aliases:
        customer_id = str(document_value(alias, "customerId", "") or "")
        if customer_id:
            aliases_by_customer[customer_id].append(alias)

    records = []
    for customer in sorted(
        customers,
        key=lambda item: (str(document_value(item, "customerCode", "")), str(document_value(item, "name", ""))),
    ):
        customer_id = str(document_value(customer, "id", "") or "")
        alias_values = _unique(
            [
                *[str(value) for value in _as_list(document_value(customer, "aliases", []))],
                *[str(document_value(alias, "value", "") or "") for alias in aliases_by_customer.get(customer_id, [])],
            ]
        )
        sender_domains = _unique([str(value) for value in _as_list(document_value(customer, "senderDomains", []))])
        known_subject_patterns = _unique(
            [str(value) for value in _as_list(document_value(customer, "knownSubjectPatterns", []))]
        )
        records.append(
            {
                "customer_id": customer_id,
                "cust_code": str(document_value(customer, "customerCode", "") or ""),
                "customer_name": str(document_value(customer, "name", "") or ""),
                "customer_store_number": str(document_value(customer, "storeNumber", "") or ""),
                "route_number": str(document_value(customer, "routeNumber", "") or ""),
                "location_address1": str(document_value(customer, "address1", "") or ""),
                "location_city": str(document_value(customer, "city", "") or ""),
                "location_state": str(document_value(customer, "state", "") or ""),
                "location_zip": str(document_value(customer, "postalCode", "") or ""),
                "phone": str(document_value(customer, "phone", "") or ""),
                "customer_website": str(document_value(customer, "website", "") or ""),
                "customer_email": str(document_value(customer, "customerEmail", "") or ""),
                "csr_email": str(document_value(customer, "csrEmail", "") or ""),
                "csr_folder": str(document_value(customer, "csrFolder", "") or ""),
                "sender_domains": sender_domains,
                "aliases": alias_values,
                "known_subject_patterns": known_subject_patterns,
            }
        )
    return records


def customer_vector_store_jsonl(records: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) for record in records)


def _object_value(value: Any, *keys: str, default: Any = "") -> Any:
    for key in keys:
        if isinstance(value, dict) and key in value:
            return value[key]
        if hasattr(value, key):
            return getattr(value, key)
    return default


def _pick(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return [value]


def _unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
