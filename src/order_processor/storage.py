from __future__ import annotations

from collections import defaultdict
import os
from typing import Any

from .data_model import (
    CONTAINER_NAMES,
    document_value,
    normalize_document_for_storage,
    partition_key_value,
    queryable_customer_id,
)


class RepositoryError(RuntimeError):
    pass


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

    def upsert(self, container: str, document: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_document_for_storage(container, document)
        self._container(container).upsert_item(normalized)
        return normalized

    def get(self, container: str, document_id: str) -> dict[str, Any] | None:
        query = "SELECT * FROM c WHERE c.id = @id"
        results = list(
            self._container(container).query_items(
                query=query,
                parameters=[{"name": "@id", "value": document_id}],
                enable_cross_partition_query=True,
            )
        )
        return results[0] if results else None

    def list(self, container: str) -> list[dict[str, Any]]:
        return list(self._container(container).query_items(query="SELECT * FROM c", enable_cross_partition_query=True))

    def query_by_tenant(self, container: str, tenant_id: str) -> list[dict[str, Any]]:
        query = "SELECT * FROM c WHERE c.tenantId = @tenantId"
        return list(
            self._container(container).query_items(
                query=query,
                parameters=[{"name": "@tenantId", "value": tenant_id}],
                enable_cross_partition_query=True,
            )
        )

    def query_by_customer(self, container: str, tenant_id: str, customer_id: str) -> list[dict[str, Any]]:
        query = "SELECT * FROM c WHERE c.tenantId = @tenantId AND c.customerId = @customerId"
        return list(
            self._container(container).query_items(
                query=query,
                parameters=[
                    {"name": "@tenantId", "value": tenant_id},
                    {"name": "@customerId", "value": customer_id},
                ],
                enable_cross_partition_query=True,
            )
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
        rows = list(
            self._container("customers").query_items(
                query=query,
                parameters=[
                    {"name": "@tenantId", "value": tenant_id},
                    {"name": "@embedding", "value": embedding},
                ],
                enable_cross_partition_query=True,
            )
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
