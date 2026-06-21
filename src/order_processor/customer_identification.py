from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re
from collections.abc import Iterable, Mapping
from email.utils import parseaddr
from typing import Any, Protocol

from .models import (
    CustomerAlias,
    CustomerIdentificationResult,
    CustomerProfile,
    EmailMessage,
    MatchStatus,
)


DEFAULT_CUSTOMER_CONFIDENCE_THRESHOLD = 0.85


class TextEmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]:
        """Return an embedding for customer identification fallback."""


@dataclass(frozen=True, slots=True)
class CustomerVectorCandidate:
    customer: CustomerProfile
    confidence: float
    method: str = "cosmosVectorSearch"
    reason: str = ""


class CustomerVectorSearch(Protocol):
    def search(
        self,
        tenant_id: str,
        query_text: str,
        limit: int = 5,
    ) -> list[CustomerVectorCandidate]:
        """Return scored customer candidates from a vector-capable backing store."""


@dataclass(frozen=True, slots=True)
class CustomerSignals:
    customer_code: str | None = None
    store_number: str | None = None
    route_number: str | None = None
    sender_domain: str | None = None
    subject: str = ""
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "customerCode": self.customer_code,
            "storeNumber": self.store_number,
            "routeNumber": self.route_number,
            "senderDomain": self.sender_domain,
            "subject": self.subject,
        }


@dataclass(frozen=True, slots=True)
class DeterministicCandidate:
    customer: CustomerProfile
    match_method: str
    confidence: float
    reason: str


CUSTOMER_CODE_PATTERNS = [
    re.compile(r"\b(?:cust(?:omer)?[_\s-]*code|customer)\s*[:#-]?\s*([A-Z0-9-]{2,})\b", re.I),
    re.compile(r"\bCUST[:#-]?([A-Z0-9-]{2,})\b", re.I),
]

STORE_NUMBER_PATTERNS = [
    re.compile(r"\b(?:store|location)[_\s-]*(?:no\.?|number|#)?\s*[:#-]?\s*([A-Z0-9-]{2,})\b", re.I),
]

ROUTE_NUMBER_PATTERNS = [
    re.compile(r"\broute\s*[:#-]?\s*([A-Z0-9-]{1,})\b", re.I),
]

ALIAS_CODE_TYPES = {"customercode", "customer_code", "code", "accountnumber", "account_number"}
ALIAS_STORE_TYPES = {"storenumber", "store_number", "store", "location", "locationnumber", "location_number"}
ALIAS_ROUTE_TYPES = {"routenumber", "route_number", "route"}
ALIAS_DOMAIN_TYPES = {"senderdomain", "sender_domain", "domain", "emaildomain", "email_domain"}
ALIAS_SUBJECT_PATTERN_TYPES = {
    "knownsubjectpattern",
    "known_subject_pattern",
    "subjectpattern",
    "subject_pattern",
    "subject",
}


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def normalize_domain(value: str) -> str:
    return (value or "").strip().lower().lstrip("@")


def _combined_text(email: EmailMessage) -> str:
    return "\n".join([email.subject or "", email.sender or "", email.body_text or "", email.body_html or ""])


def _first_match(patterns: Iterable[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def _sender_domain(sender: str) -> str | None:
    _, address = parseaddr(sender or "")
    value = address or sender or ""
    if "@" not in value:
        return None
    return normalize_domain(value.rsplit("@", 1)[1])


def extract_customer_signals(email: EmailMessage) -> CustomerSignals:
    text = _combined_text(email)
    return CustomerSignals(
        customer_code=_first_match(CUSTOMER_CODE_PATTERNS, text),
        store_number=_first_match(STORE_NUMBER_PATTERNS, text),
        route_number=_first_match(ROUTE_NUMBER_PATTERNS, text),
        sender_domain=_sender_domain(email.sender),
        subject=email.subject or "",
        text=text,
    )


def _safe_regex_match(pattern: str, value: str) -> bool:
    try:
        return re.search(pattern, value or "", re.IGNORECASE | re.MULTILINE) is not None
    except re.error:
        return False


def _alias_type(value: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", (value or "").lower())


def _aliases_by_customer(aliases: Iterable[CustomerAlias]) -> dict[str, list[CustomerAlias]]:
    grouped: dict[str, list[CustomerAlias]] = {}
    for alias in aliases:
        grouped.setdefault(alias.customer_id, []).append(alias)
    return grouped


def _raw_list(customer: CustomerProfile, *names: str) -> list[str]:
    for name in names:
        value = customer.raw_source.get(name)
        if value is None:
            continue
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(";") if item.strip()]
    return []


def _customer_codes(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [customer.customer_code, *_raw_list(customer, "customerCodes", "customer_codes")]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_CODE_TYPES:
            values.append(alias.normalized_value or alias.value)
    values.extend(customer.aliases)
    return [value for value in values if value]


def _store_numbers(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [customer.store_number, *_raw_list(customer, "storeNumbers", "store_numbers")]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_STORE_TYPES:
            values.append(alias.normalized_value or alias.value)
    return [value for value in values if value]


def _route_numbers(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [customer.route_number, *_raw_list(customer, "routeNumbers", "route_numbers")]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_ROUTE_TYPES:
            values.append(alias.normalized_value or alias.value)
    return [value for value in values if value]


def _sender_domains(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [*customer.sender_domains, *_raw_list(customer, "senderDomains", "sender_domains")]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_DOMAIN_TYPES:
            values.append(alias.normalized_value or alias.value)
    return [normalize_domain(value) for value in values if normalize_domain(value)]


def _known_subject_patterns(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [
        *customer.known_subject_patterns,
        *_raw_list(customer, "knownSubjectPatterns", "known_subject_patterns", "subjectPatterns", "subject_patterns"),
    ]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_SUBJECT_PATTERN_TYPES:
            values.append(alias.value)
    return [value for value in values if value]


def _candidate_payload(candidate: CustomerProfile, confidence: float, method: str, reason: str) -> dict[str, Any]:
    return {
        "customerId": candidate.id,
        "customerCode": candidate.customer_code,
        "name": candidate.name,
        "routeNumber": candidate.route_number,
        "storeNumber": candidate.store_number,
        "matchMethod": method,
        "confidence": round(confidence, 4),
        "reason": reason,
    }


def _best_unique_candidate(
    candidates: list[DeterministicCandidate],
    confidence_threshold: float,
) -> CustomerIdentificationResult | None:
    if not candidates:
        return None

    ranked = sorted(candidates, key=lambda item: item.confidence, reverse=True)
    top = ranked[0]
    tied = [item for item in ranked if math.isclose(item.confidence, top.confidence, abs_tol=0.0001)]
    candidate_payloads = [
        _candidate_payload(item.customer, item.confidence, item.match_method, item.reason)
        for item in ranked[:5]
    ]

    if top.confidence >= confidence_threshold and len({item.customer.id for item in tied}) == 1:
        return CustomerIdentificationResult(
            status=MatchStatus.MATCHED,
            customer_id=top.customer.id,
            customer_code=top.customer.customer_code,
            route_number=top.customer.route_number,
            match_method=top.match_method,
            confidence=top.confidence,
            candidates=candidate_payloads,
            reasons=[top.reason],
        )

    return CustomerIdentificationResult(
        status=MatchStatus.POSSIBLE_MATCH,
        route_number=top.customer.route_number,
        match_method=top.match_method,
        confidence=top.confidence,
        candidates=candidate_payloads,
        reasons=["deterministic signals produced ambiguous or below-threshold candidates"],
    )


def deterministic_customer_candidates(
    email: EmailMessage,
    customers: list[CustomerProfile],
    aliases: list[CustomerAlias] | None = None,
) -> tuple[CustomerSignals, list[DeterministicCandidate]]:
    signals = extract_customer_signals(email)
    aliases_by_customer = _aliases_by_customer(aliases or [])
    candidates: list[DeterministicCandidate] = []

    for customer in customers:
        customer_aliases = aliases_by_customer.get(customer.id, [])

        if email.customer_id and customer.id == email.customer_id:
            candidates.append(
                DeterministicCandidate(
                    customer=customer,
                    match_method="customerContext",
                    confidence=1.0,
                    reason=f"email already scoped to customer {customer.id}",
                )
            )

        if signals.customer_code:
            extracted_code = normalize_identifier(signals.customer_code)
            for code in _customer_codes(customer, customer_aliases):
                if extracted_code and extracted_code == normalize_identifier(code):
                    candidates.append(
                        DeterministicCandidate(
                            customer=customer,
                            match_method="customerCode",
                            confidence=1.0,
                            reason=f"customer code {signals.customer_code} matched",
                        )
                    )
                    break

        if signals.store_number:
            extracted_store = normalize_identifier(signals.store_number)
            for store_number in _store_numbers(customer, customer_aliases):
                if extracted_store and extracted_store == normalize_identifier(store_number):
                    candidates.append(
                        DeterministicCandidate(
                            customer=customer,
                            match_method="storeNumber",
                            confidence=0.96,
                            reason=f"store number {signals.store_number} matched",
                        )
                    )
                    break

        if signals.route_number:
            extracted_route = normalize_identifier(signals.route_number)
            for route_number in _route_numbers(customer, customer_aliases):
                if extracted_route and extracted_route == normalize_identifier(route_number):
                    candidates.append(
                        DeterministicCandidate(
                            customer=customer,
                            match_method="routeNumber",
                            confidence=0.93,
                            reason=f"route number {signals.route_number} matched",
                        )
                    )
                    break

        if signals.sender_domain and signals.sender_domain in set(_sender_domains(customer, customer_aliases)):
            candidates.append(
                DeterministicCandidate(
                    customer=customer,
                    match_method="senderDomain",
                    confidence=0.9,
                    reason=f"sender domain {signals.sender_domain} matched",
                )
            )

        for pattern in _known_subject_patterns(customer, customer_aliases):
            if _safe_regex_match(pattern, email.subject or ""):
                candidates.append(
                    DeterministicCandidate(
                        customer=customer,
                        match_method="knownSubjectPattern",
                        confidence=0.92,
                        reason=f"subject matched known pattern for {customer.id}",
                    )
                )
                break

    return signals, candidates


class StaticEmbeddingClient:
    """Deterministic local embedder for tests and offline development."""

    def embed(self, text: str) -> list[float]:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        buckets = [0.0] * 32
        for token in tokens:
            buckets[sum(ord(character) for character in token) % len(buckets)] += 1.0
        magnitude = math.sqrt(sum(value * value for value in buckets)) or 1.0
        return [value / magnitude for value in buckets]


class AzureOpenAIEmbeddingClient:
    """Azure OpenAI embedding client using managed identity or an API key."""

    def __init__(
        self,
        endpoint: str | None = None,
        deployment: str | None = None,
        api_version: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        self.deployment = deployment or os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
        self.api_version = api_version or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        self.api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")
        if not self.endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT is required for Azure OpenAI embeddings.")
        if not self.deployment:
            raise ValueError("AZURE_OPENAI_EMBEDDING_DEPLOYMENT is required for Azure OpenAI embeddings.")

    def embed(self, text: str) -> list[float]:
        try:
            from openai import AzureOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency.
            raise RuntimeError("The openai package is required for Azure OpenAI embeddings.") from exc

        if self.api_key:
            client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
            )
        else:
            try:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency.
                raise RuntimeError("azure-identity is required for managed identity Azure OpenAI auth.") from exc

            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
            client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                azure_ad_token_provider=token_provider,
                api_version=self.api_version,
            )

        response = client.embeddings.create(model=self.deployment, input=text or " ")
        return list(response.data[0].embedding)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


class CosmosCustomerVectorSearch:
    """Vector search adapter for Cosmos-backed customer records.

    The deployed repository can expose a native vector query. In-memory/local
    repositories fall back to cosine similarity over customer embeddings.
    """

    def __init__(self, repository: Any, embedding_client: TextEmbeddingClient) -> None:
        self.repository = repository
        self.embedding_client = embedding_client

    def search(
        self,
        tenant_id: str,
        query_text: str,
        limit: int = 5,
    ) -> list[CustomerVectorCandidate]:
        query_embedding = self.embedding_client.embed(query_text)
        native_search = getattr(self.repository, "vector_search_customers", None)
        if callable(native_search):
            docs = native_search(tenant_id, query_embedding, limit=limit)
            return [
                CustomerVectorCandidate(
                    customer=_customer_from_mapping(doc),
                    confidence=float(doc.get("confidence", doc.get("score", 0.0)) or 0.0),
                    reason="Cosmos vector search candidate",
                )
                for doc in docs
            ]

        candidates: list[CustomerVectorCandidate] = []
        for doc in self.repository.query_by_tenant("customers", tenant_id):
            customer = _customer_from_mapping(doc)
            confidence = _cosine_similarity(query_embedding, customer.embedding)
            if confidence > 0:
                candidates.append(
                    CustomerVectorCandidate(
                        customer=customer,
                        confidence=confidence,
                        reason="local cosine similarity over customer embedding",
                    )
                )
        return sorted(candidates, key=lambda item: item.confidence, reverse=True)[:limit]


def _pick_mapping(mapping: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _customer_from_mapping(mapping: Mapping[str, Any]) -> CustomerProfile:
    return CustomerProfile(
        id=str(_pick_mapping(mapping, "id", default="")),
        tenant_id=_pick_mapping(mapping, "tenantId", "tenant_id", default=""),
        customer_code=_pick_mapping(mapping, "customerCode", "customer_code", default=""),
        name=_pick_mapping(mapping, "name", default=""),
        route_number=_pick_mapping(mapping, "routeNumber", "route_number", default=""),
        csr_email=_pick_mapping(mapping, "csrEmail", "csr_email", default=""),
        csr_folder=_pick_mapping(mapping, "csrFolder", "csr_folder", default=""),
        store_number=_pick_mapping(mapping, "storeNumber", "store_number", default=""),
        sender_domains=list(_as_list(_pick_mapping(mapping, "senderDomains", "sender_domains", default=[]))),
        aliases=list(_as_list(_pick_mapping(mapping, "aliases", default=[]))),
        known_subject_patterns=list(
            _as_list(_pick_mapping(mapping, "knownSubjectPatterns", "known_subject_patterns", default=[]))
        ),
        embedding=[float(value) for value in _as_list(_pick_mapping(mapping, "embedding", default=[]))],
        raw_source=dict(_pick_mapping(mapping, "rawSource", "raw_source", default={}) or {}),
    )


def customer_vector_search_from_environment(repository: Any) -> CustomerVectorSearch | None:
    enabled = os.environ.get("ORDER_PROCESSOR_ENABLE_CUSTOMER_VECTOR_SEARCH", "").strip().lower()
    if enabled not in {"1", "true", "yes"}:
        return None
    return CosmosCustomerVectorSearch(repository, AzureOpenAIEmbeddingClient())


def identify_customer(
    email: EmailMessage,
    customers: list[CustomerProfile],
    aliases: list[CustomerAlias] | None = None,
    vector_search: CustomerVectorSearch | None = None,
    confidence_threshold: float = DEFAULT_CUSTOMER_CONFIDENCE_THRESHOLD,
) -> CustomerIdentificationResult:
    signals, candidates = deterministic_customer_candidates(email, customers, aliases)
    deterministic_result = _best_unique_candidate(candidates, confidence_threshold)
    if deterministic_result and deterministic_result.status == MatchStatus.MATCHED:
        deterministic_result.extracted_signals = signals.as_dict()
        return deterministic_result

    vector_candidates: list[CustomerVectorCandidate] = []
    if vector_search:
        vector_candidates = vector_search.search(email.tenant_id, signals.text, limit=5)
        payloads = [
            _candidate_payload(candidate.customer, candidate.confidence, candidate.method, candidate.reason)
            for candidate in vector_candidates
        ]
        if vector_candidates:
            top = vector_candidates[0]
            if top.confidence >= confidence_threshold:
                return CustomerIdentificationResult(
                    status=MatchStatus.MATCHED,
                    customer_id=top.customer.id,
                    customer_code=top.customer.customer_code,
                    route_number=top.customer.route_number,
                    match_method=top.method,
                    confidence=top.confidence,
                    candidates=payloads,
                    reasons=["vector fallback produced a confident customer match"],
                    extracted_signals=signals.as_dict(),
                )
            return CustomerIdentificationResult(
                status=MatchStatus.POSSIBLE_MATCH,
                route_number=top.customer.route_number,
                match_method=top.method,
                confidence=top.confidence,
                candidates=payloads,
                reasons=["vector fallback produced candidates below confidence threshold"],
                extracted_signals=signals.as_dict(),
            )

    if deterministic_result:
        deterministic_result.extracted_signals = signals.as_dict()
        return deterministic_result

    return CustomerIdentificationResult(
        status=MatchStatus.UNRESOLVED,
        route_number=signals.route_number,
        match_method="none",
        confidence=0.0,
        reasons=["no deterministic customer match and no confident vector match"],
        extracted_signals=signals.as_dict(),
    )
