from __future__ import annotations

from dataclasses import dataclass
from typing import Any


GLOBAL_CUSTOMER_ID = "_global"
UNASSIGNED_CUSTOMER_ID = "_unassigned"


@dataclass(frozen=True, slots=True)
class ContainerDefinition:
    name: str
    partition_key_paths: tuple[str, ...]
    model_name: str
    description: str
    customer_scoped: bool = False
    customer_id_required: bool = False
    default_customer_id: str | None = None
    vector_embedding_path: str | None = None
    vector_dimensions: int | None = None

    @property
    def uses_hierarchical_partition_key(self) -> bool:
        return len(self.partition_key_paths) > 1


CONTAINER_DEFINITIONS: tuple[ContainerDefinition, ...] = (
    ContainerDefinition("tenants", ("/tenantId",), "Tenant", "Tenant-level settings and environment flags."),
    ContainerDefinition(
        "customers",
        ("/tenantId",),
        "CustomerProfile",
        "Canonical customer profiles, aliases, routing metadata, and embeddings.",
        vector_embedding_path="/embedding",
        vector_dimensions=1536,
    ),
    ContainerDefinition(
        "customerVectorStores",
        ("/tenantId",),
        "CustomerVectorStoreReference",
        "Active Azure OpenAI vector store references built from imported customer lists.",
    ),
    ContainerDefinition(
        "customerAliases",
        ("/tenantId", "/customerId"),
        "CustomerAlias",
        "Alternate names, codes, domains, and deterministic customer matching keys.",
        customer_scoped=True,
        customer_id_required=True,
    ),
    ContainerDefinition(
        "items",
        ("/tenantId", "/customerId"),
        "ItemRecord",
        "Canonical item records, customer item numbers, UPCs, aliases, and embeddings.",
        customer_scoped=True,
        customer_id_required=True,
        vector_embedding_path="/embedding",
        vector_dimensions=1536,
    ),
    ContainerDefinition(
        "routingRules",
        ("/tenantId", "/customerId"),
        "RoutingRule",
        "Data-driven email routing rules. Tenant-wide rules use the global customer partition.",
        customer_scoped=True,
        default_customer_id=GLOBAL_CUSTOMER_ID,
    ),
    ContainerDefinition(
        "processorProfiles",
        ("/tenantId", "/customerId"),
        "ProcessorProfile",
        "Parser configuration by customer and source type.",
        customer_scoped=True,
        default_customer_id=GLOBAL_CUSTOMER_ID,
    ),
    ContainerDefinition(
        "outputProfiles",
        ("/tenantId", "/customerId"),
        "OutputProfile",
        "Output format and delivery adapter configuration.",
        customer_scoped=True,
        default_customer_id=GLOBAL_CUSTOMER_ID,
    ),
    ContainerDefinition(
        "mailboxAccounts",
        ("/tenantId", "/customerId"),
        "MailboxAccount",
        "Tenant-scoped monitored mailbox configuration and ingest status.",
        customer_scoped=True,
        default_customer_id=GLOBAL_CUSTOMER_ID,
    ),
    ContainerDefinition(
        "microsoftAuthConnections",
        ("/tenantId", "/customerId"),
        "MicrosoftAuthConnection",
        "Microsoft Graph and Power Automate connection metadata; secrets stay in Key Vault.",
        customer_scoped=True,
        default_customer_id=GLOBAL_CUSTOMER_ID,
    ),
    ContainerDefinition(
        "consoleUsers",
        ("/tenantId",),
        "ConsoleUser",
        "Microsoft login users allowed into the console.",
    ),
    ContainerDefinition(
        "customerUserAssignments",
        ("/tenantId", "/customerId"),
        "CustomerUserAssignment",
        "Customer-scoped user and role assignments for console authorization.",
        customer_scoped=True,
        customer_id_required=True,
    ),
    ContainerDefinition(
        "emailMessages",
        ("/tenantId",),
        "EmailMessage",
        "Ingested email metadata, attachments, status, and routing result.",
    ),
    ContainerDefinition(
        "orderRuns",
        ("/tenantId",),
        "OrderRun",
        "Order lifecycle, status, output artifacts, and parser errors.",
    ),
    ContainerDefinition(
        "orderLines",
        ("/tenantId", "/customerId"),
        "OrderLine",
        "Searchable line-level order detail when split from order run documents.",
        customer_scoped=True,
        default_customer_id=UNASSIGNED_CUSTOMER_ID,
    ),
    ContainerDefinition(
        "exceptionTasks",
        ("/tenantId",),
        "ExceptionTask",
        "Human review queue for routing, customer ID, item validation, and parser failures.",
    ),
    ContainerDefinition(
        "auditEvents",
        ("/tenantId",),
        "AuditEvent",
        "Timeline events, decisions, confidence scores, and user interventions.",
    ),
)

CONTAINER_BY_NAME = {definition.name: definition for definition in CONTAINER_DEFINITIONS}
CONTAINER_NAMES = tuple(definition.name for definition in CONTAINER_DEFINITIONS)


def snake_to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def keys_to_camel(value: Any) -> Any:
    if isinstance(value, list):
        return [keys_to_camel(item) for item in value]
    if isinstance(value, dict):
        return {snake_to_camel(str(key)): keys_to_camel(item) for key, item in value.items()}
    return value


def document_value(document: dict[str, Any], camel_name: str, default: Any = None) -> Any:
    if camel_name in document:
        return document[camel_name]

    snake_name = ""
    for character in camel_name:
        if character.isupper():
            snake_name += f"_{character.lower()}"
        else:
            snake_name += character
    return document.get(snake_name, default)


def container_definition(container_name: str) -> ContainerDefinition:
    try:
        return CONTAINER_BY_NAME[container_name]
    except KeyError as exc:
        raise ValueError(f"Unknown Cosmos container: {container_name}") from exc


def normalize_document_for_storage(container_name: str, document: dict[str, Any]) -> dict[str, Any]:
    definition = container_definition(container_name)
    normalized = keys_to_camel(dict(document))

    if not normalized.get("id"):
        raise ValueError(f"{container_name} document must include id.")

    if not normalized.get("tenantId"):
        if container_name == "tenants":
            normalized["tenantId"] = normalized["id"]
        else:
            raise ValueError(f"{container_name} document {normalized['id']} must include tenantId.")

    if container_name == "items":
        normalized["customerId"] = GLOBAL_CUSTOMER_ID

    if definition.customer_scoped and not normalized.get("customerId"):
        if definition.customer_id_required:
            raise ValueError(f"{container_name} document {normalized['id']} must include customerId.")
        normalized["customerId"] = definition.default_customer_id or UNASSIGNED_CUSTOMER_ID

    return normalized


def partition_key_value(container_name: str, document: dict[str, Any]) -> str | list[str]:
    definition = container_definition(container_name)
    normalized = normalize_document_for_storage(container_name, document)
    values = []
    for path in definition.partition_key_paths:
        field_name = path.lstrip("/")
        value = normalized.get(field_name)
        if value is None or value == "":
            raise ValueError(f"{container_name} document {normalized['id']} is missing partition field {field_name}.")
        values.append(str(value))
    return values[0] if len(values) == 1 else values


def queryable_customer_id(container_name: str, document: dict[str, Any]) -> str | None:
    definition = container_definition(container_name)
    customer_id = document_value(document, "customerId", None)
    if customer_id:
        return str(customer_id)
    if definition.customer_scoped:
        return definition.default_customer_id
    return None


def container_manifest() -> list[dict[str, Any]]:
    return [
        {
            "name": definition.name,
            "partitionKeyPaths": list(definition.partition_key_paths),
            "modelName": definition.model_name,
            "customerScoped": definition.customer_scoped,
            "customerIdRequired": definition.customer_id_required,
            "defaultCustomerId": definition.default_customer_id,
            "vectorEmbeddingPath": definition.vector_embedding_path,
            "vectorDimensions": definition.vector_dimensions,
            "description": definition.description,
        }
        for definition in CONTAINER_DEFINITIONS
    ]
