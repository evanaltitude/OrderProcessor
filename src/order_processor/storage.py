from __future__ import annotations

from collections import defaultdict
import os
import time
from typing import Any

from .data_model import (
    CONTAINER_NAMES,
    container_definition,
    document_value,
    normalize_document_for_storage,
    partition_key_value,
    queryable_customer_id,
)


class RepositoryError(RuntimeError):
    pass


def _safe_cosmos_field(value: Any) -> bool:
    field = str(value or "").strip()
    return bool(field) and field.replace("_", "").isalnum()


def _safe_cosmos_path(value: Any) -> bool:
    parts = str(value or "").strip().split(".")
    return bool(parts) and all(_safe_cosmos_field(part) for part in parts)


class InMemoryRepository:
    """Tiny repository used by local tests and the function facade scaffold."""

    def __init__(self) -> None:
        self._containers: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def upsert(self, container: str, document: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_document_for_storage(container, document)
        document_id = str(normalized["id"])
        self._containers[container][document_id] = normalized
        return normalized

    def get(self, container: str, document_id: str) -> dict[str, Any] | None:
        self._ensure_container(container)
        return self._containers[container].get(document_id)

    def delete(self, container: str, document_id: str) -> bool:
        self._ensure_container(container)
        return self._containers[container].pop(document_id, None) is not None

    def list(self, container: str) -> list[dict[str, Any]]:
        self._ensure_container(container)
        return list(self._containers[container].values())

    def query_by_tenant(self, container: str, tenant_id: str) -> list[dict[str, Any]]:
        self._ensure_container(container)
        return [
            document
            for document in self._containers[container].values()
            if document_value(document, "tenantId") == tenant_id
        ]

    def query_by_tenant_fields(self, container: str, tenant_id: str, fields: list[str]) -> list[dict[str, Any]]:
        return [
            {field: document[field] for field in fields if field in document}
            for document in self.query_by_tenant(container, tenant_id)
        ]

    def query_by_tenant_page(
        self,
        container: str,
        tenant_id: str,
        *,
        fields: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        search_fields: list[str] | None = None,
        order_by: str = "updatedAt",
        descending: bool = True,
        customer_ids: list[str] | None = None,
        include_global: bool = False,
    ) -> dict[str, Any]:
        self._ensure_container(container)
        documents = list(self.query_by_tenant(container, tenant_id))
        if customer_ids is not None:
            allowed = {str(value) for value in customer_ids if str(value)}
            documents = [
                document
                for document in documents
                if (queryable_customer_id(container, document) or document_value(document, "customerId", None)) in allowed
                or (
                    include_global
                    and (queryable_customer_id(container, document) or document_value(document, "customerId", None)) == "_global"
                )
            ]
        search_text = search.strip().lower()
        if search_text:
            searchable = search_fields or fields or []
            documents = [
                document
                for document in documents
                if any(search_text in str(document_value(document, field, "")).lower() for field in searchable)
            ]
        reverse = bool(descending)
        documents.sort(
            key=lambda document: str(document_value(document, order_by, "") or document_value(document, "id", "")),
            reverse=reverse,
        )
        total = len(documents)
        page = documents[max(0, offset) : max(0, offset) + max(1, limit)]
        if fields:
            page = [{field: document[field] for field in fields if field in document} for document in page]
        return {"items": page, "total": total, "limit": max(1, limit), "offset": max(0, offset)}

    def query_by_tenant_stats(self, container: str, tenant_id: str, date_field: str = "lastImportedAt") -> dict[str, Any]:
        documents = self.query_by_tenant(container, tenant_id)
        dates = [
            str(document_value(document, date_field, "") or document_value(document, "updatedAt", ""))
            for document in documents
            if document_value(document, date_field, "") or document_value(document, "updatedAt", "")
        ]
        return {"count": len(documents), "latest": sorted(dates)[-1] if dates else ""}

    def query_by_customer(self, container: str, tenant_id: str, customer_id: str) -> list[dict[str, Any]]:
        self._ensure_container(container)
        return [
            document
            for document in self.query_by_tenant(container, tenant_id)
            if queryable_customer_id(container, document) == customer_id
        ]

    @staticmethod
    def _ensure_container(container: str) -> None:
        if container not in CONTAINER_NAMES:
            raise ValueError(f"Unknown Cosmos container: {container}")


class CosmosRepository:
    """Cosmos DB repository using Entra identity or an injected credential."""

    def __init__(self, endpoint: str, database_name: str, credential: Any | None = None) -> None:
        try:
            from azure.cosmos import CosmosClient
            from azure.identity import DefaultAzureCredential
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on deployed dependencies.
            raise RepositoryError("Azure Cosmos dependencies are not installed.") from exc

        if not endpoint:
            raise ValueError("Cosmos endpoint is required.")
        if not database_name:
            raise ValueError("Cosmos database name is required.")

        self.client = CosmosClient(endpoint, credential=credential or DefaultAzureCredential())
        self.database = self.client.get_database_client(database_name)

    def _container(self, container: str) -> Any:
        if container not in CONTAINER_NAMES:
            raise ValueError(f"Unknown Cosmos container: {container}")
        return self.database.get_container_client(container)

    def _query_items(self, container: str, **kwargs: Any) -> list[dict[str, Any]]:
        last_error: RuntimeError | None = None
        for attempt in range(3):
            try:
                return list(self._container(container).query_items(**kwargs))
            except RuntimeError as exc:
                if "OrderedDict mutated during iteration" not in str(exc):
                    raise
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
        return []

    def upsert(self, container: str, document: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_document_for_storage(container, document)
        self._container(container).upsert_item(normalized)
        return normalized

    def get(self, container: str, document_id: str) -> dict[str, Any] | None:
        query = "SELECT * FROM c WHERE c.id = @id"
        results = self._query_items(
            container,
            query=query,
            parameters=[{"name": "@id", "value": document_id}],
            enable_cross_partition_query=True,
        )
        return results[0] if results else None

    def delete(self, container: str, document_id: str) -> bool:
        document = self.get(container, document_id)
        if document is None:
            return False
        self._container(container).delete_item(document_id, partition_key=existing_partition_key_value(container, document))
        return True

    def list(self, container: str) -> list[dict[str, Any]]:
        return self._query_items(container, query="SELECT * FROM c", enable_cross_partition_query=True)

    def query_by_tenant(self, container: str, tenant_id: str) -> list[dict[str, Any]]:
        query = "SELECT * FROM c WHERE c.tenantId = @tenantId"
        return self._query_items(
            container,
            query=query,
            parameters=[{"name": "@tenantId", "value": tenant_id}],
            enable_cross_partition_query=True,
        )

    def query_by_tenant_fields(self, container: str, tenant_id: str, fields: list[str]) -> list[dict[str, Any]]:
        self._container(container)
        selected_fields = []
        for field in fields:
            field_name = str(field).strip()
            if field_name.replace("_", "").isalnum():
                selected_fields.append(field_name)
        if not selected_fields:
            return self.query_by_tenant(container, tenant_id)
        projection = ", ".join(f'"{field}": c.{field}' for field in selected_fields)
        query = f"SELECT VALUE {{{projection}}} FROM c WHERE c.tenantId = @tenantId"
        return self._query_items(
            container,
            query=query,
            parameters=[{"name": "@tenantId", "value": tenant_id}],
            enable_cross_partition_query=True,
        )

    def query_by_tenant_page(
        self,
        container: str,
        tenant_id: str,
        *,
        fields: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        search_fields: list[str] | None = None,
        order_by: str = "updatedAt",
        descending: bool = True,
        customer_ids: list[str] | None = None,
        include_global: bool = False,
    ) -> dict[str, Any]:
        self._container(container)
        safe_limit = max(1, min(int(limit or 100), 500))
        safe_offset = max(0, int(offset or 0))
        where = ["c.tenantId = @tenantId"]
        parameters: list[dict[str, Any]] = [{"name": "@tenantId", "value": tenant_id}]

        if customer_ids is not None:
            allowed = [str(value) for value in customer_ids if str(value)]
            if include_global and "_global" not in allowed:
                allowed.append("_global")
            if allowed:
                where.append("ARRAY_CONTAINS(@customerIds, c.customerId)")
                parameters.append({"name": "@customerIds", "value": allowed})
            else:
                where.append("1 = 0")

        search_text = str(search or "").strip()
        searchable_fields = [field for field in (search_fields or []) if _safe_cosmos_field(field)]
        if search_text and searchable_fields:
            parameters.append({"name": "@search", "value": search_text})
            where.append(
                "("
                + " OR ".join(
                    f"(IS_DEFINED(c.{field}) AND CONTAINS(c.{field}, @search, true))"
                    for field in searchable_fields
                )
                + ")"
            )

        where_clause = " AND ".join(where)
        selected_fields = [field for field in (fields or []) if _safe_cosmos_field(field)]
        if selected_fields:
            projection = ", ".join(f'"{field}": c.{field}' for field in selected_fields)
            select = f"SELECT VALUE {{{projection}}}"
        else:
            select = "SELECT *"

        order_field = order_by if _safe_cosmos_field(order_by) else "updatedAt"
        direction = "DESC" if descending else "ASC"
        query = (
            f"{select} FROM c WHERE {where_clause} "
            f"ORDER BY c.{order_field} {direction} OFFSET {safe_offset} LIMIT {safe_limit}"
        )
        count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_clause}"
        items = self._query_items(
            container,
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
        counts = self._query_items(
            container,
            query=count_query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
        return {"items": items, "total": int(counts[0] if counts else 0), "limit": safe_limit, "offset": safe_offset}

    def query_by_tenant_stats(self, container: str, tenant_id: str, date_field: str = "lastImportedAt") -> dict[str, Any]:
        field = date_field if _safe_cosmos_path(date_field) else "lastImportedAt"
        parameters = [{"name": "@tenantId", "value": tenant_id}]
        count_rows = self._query_items(
            container,
            query="SELECT VALUE COUNT(1) FROM c WHERE c.tenantId = @tenantId",
            parameters=parameters,
            enable_cross_partition_query=True,
        )

        def max_date(path: str) -> str:
            if not _safe_cosmos_path(path):
                return ""
            rows = self._query_items(
                container,
                query=(
                    f"SELECT VALUE MAX(c.{path}) FROM c "
                    f"WHERE c.tenantId = @tenantId "
                    f"AND IS_DEFINED(c.{path}) "
                    f"AND NOT IS_NULL(c.{path}) "
                    f"AND c.{path} != ''"
                ),
                parameters=parameters,
                enable_cross_partition_query=True,
            )
            return str(rows[0] or "") if rows else ""

        latest = ""
        for paths in (
            [field],
            ["rawSource.importedAt", "sourceMetadata.importedAt"],
            ["updatedAt"],
        ):
            values = [value for value in (max_date(path) for path in paths) if value]
            if values:
                latest = sorted(values)[-1]
                break

        return {
            "count": int(count_rows[0] if count_rows else 0),
            "latest": latest,
        }

    def query_by_customer(self, container: str, tenant_id: str, customer_id: str) -> list[dict[str, Any]]:
        query = "SELECT * FROM c WHERE c.tenantId = @tenantId AND c.customerId = @customerId"
        return self._query_items(
            container,
            query=query,
            parameters=[
                {"name": "@tenantId", "value": tenant_id},
                {"name": "@customerId", "value": customer_id},
            ],
            enable_cross_partition_query=True,
        )

    def partition_key_for(self, container: str, document: dict[str, Any]) -> str | list[str]:
        return partition_key_value(container, document)

    def vector_search_customers(
        self,
        tenant_id: str,
        embedding: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if not embedding:
            return []

        top = max(1, min(int(limit), 20))
        query = (
            f"SELECT TOP {top} c, VectorDistance(c.embedding, @embedding) AS distance "
            "FROM c WHERE c.tenantId = @tenantId AND IS_DEFINED(c.embedding) "
            "ORDER BY VectorDistance(c.embedding, @embedding)"
        )
        rows = self._query_items(
            "customers",
            query=query,
            parameters=[
                {"name": "@tenantId", "value": tenant_id},
                {"name": "@embedding", "value": embedding},
            ],
            enable_cross_partition_query=True,
        )
        results: list[dict[str, Any]] = []
        for row in rows:
            document = dict(row.get("c", row))
            distance = row.get("distance")
            if isinstance(distance, (int, float)):
                document["distance"] = float(distance)
                document["confidence"] = max(0.0, min(1.0, 1.0 - float(distance)))
            results.append(document)
        return results


def repository_from_environment() -> InMemoryRepository | CosmosRepository:
    backend = os.environ.get("ORDER_PROCESSOR_STORAGE_BACKEND", "memory").strip().lower()
    if backend != "cosmos":
        return InMemoryRepository()

    return CosmosRepository(
        endpoint=os.environ.get("COSMOS_ACCOUNT_ENDPOINT", ""),
        database_name=os.environ.get("COSMOS_DATABASE_NAME", "orderProcessor"),
    )


def existing_partition_key_value(container: str, document: dict[str, Any]) -> str | list[str]:
    definition = container_definition(container)
    values: list[str] = []
    for path in definition.partition_key_paths:
        field_name = path.lstrip("/")
        value = document_value(document, field_name, None)
        if value is None or value == "":
            return partition_key_value(container, document)
        values.append(str(value))
    return values[0] if len(values) == 1 else values
