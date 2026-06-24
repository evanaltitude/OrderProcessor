from __future__ import annotations

from dataclasses import dataclass
import json
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
DEFAULT_CUSTOMER_AI_ATTEMPTS = 3
DEFAULT_CUSTOMER_AI_CANDIDATE_LIMIT = 8


CUSTOMER_AI_RECORD_SCHEMA = {
    "type": "object",
    "properties": {
        "customer_name": {"type": "string"},
        "customer_store_number": {"type": ["string", "null"]},
        "location_address1": {"type": "string"},
        "location_city": {"type": "string"},
        "location_state": {"type": "string"},
        "location_zip": {"type": "string"},
        "phone": {"type": ["string", "null"]},
        "customer_website": {"type": ["string", "null"]},
        "customer_email": {"type": ["string", "null"]},
        "cust_code": {"type": "string"},
    },
    "required": [
        "customer_name",
        "customer_store_number",
        "location_address1",
        "location_city",
        "location_state",
        "location_zip",
        "phone",
        "customer_website",
        "customer_email",
        "cust_code",
    ],
    "additionalProperties": False,
}


CUSTOMER_AI_SYSTEM_PROMPT = """You are an AI assistant helping Frontier Distributing identify the single best matching customer record from a daily customer vector candidate set.

You will receive a customer information object, email context, and candidate customer records as input. Use the candidate records to find the most likely matching customer record and return exactly one customer record.

Goal
Select the single best existing customer record from the candidate records and return that record only.

Critical Rules
- Return exactly one customer record.
- Return values from one single candidate record only.
- Never combine, average, or merge fields from multiple records.
- Never invent values that are not present in the selected record.
- If multiple candidate records are similar, choose the best single record using the ranking rules below.
- If a field in the selected record is null, blank, or missing, return an empty string "" for that field.
- Base the answer only on records provided in candidateRecords.
- Do not return explanations, reasoning, confidence scores, notes, SQL, arrays, or extra keys.
- Do not create a synthetic record.
- If no candidate record is clear, return the required schema with every field set to "" and cust_code set to "".

Matching Priority
Use the following evidence in order of strength when available:

0. Exact internal-customer-code signal
- If a 6-digit numeric string appears anywhere in the input and it exactly matches a record cust_code, treat that as a very strong match indicator.
- The 6-digit numeric string may appear in customerStoreNumber, customerName, customerKeyword, customerKeyword2, senderEmailKeyword, address, city, zip, phone, subject, body, or any other input text.
- If a 6-digit numeric string exactly matches a record cust_code, strongly prefer that record unless there are clear counter-indicators.
- Clear counter-indicators include strong conflicts such as a clearly different city, zip, phone, or street address.
- If the 6-digit cust_code match also aligns with name, city, zip, address, or phone, treat it as the best match.

1. Exact or near-exact store identity
- customerStoreNumber
- store number embedded in the customer name
- store number embedded in the matched record name

2. Exact location evidence
- zip
- street number and street name from address
- city
- state when available
- state inferred from zip when appropriate

3. Direct contact evidence
- phone
- senderEmailKeyword compared to email, email domain, website, or website domain
- website/domain

4. Customer name evidence
- customerName
- customerKeyword
- customerKeyword2

Tie-Breaking Rules
- Prefer a specific store/location record over a master account when store-level evidence exists.
- Prefer records whose address, zip, city, or phone directly match the input over records that only match the brand name.
- Prefer exact store-number agreement over general name similarity.
- Prefer exact zip or address agreement over shared website, shared phone, or shared chain name.
- A matching 6-digit cust_code is stronger than fuzzy name similarity, shared website, shared chain name, or nearby-address similarity.
- Do not reject an exact 6-digit cust_code match unless there are clear counter-indicators such as a conflicting city, zip, phone, or street address.
- Do not choose a master account or A/R account if a specific store record better matches the location or store number.

Normalization Rules
- Matching should be tolerant of minor formatting differences such as:
  - upper/lower case
  - punctuation
  - extra spaces
  - ST versus STREET
  - RD versus ROAD
  - TWP versus TOWNSHIP
  - #3 versus 3 in the customer name
  - phone-number punctuation differences such as 586.991.6301 versus 586-991-6301
- Use fuzzy matching, but remain conservative.
- Similar brand names alone are not enough when another candidate record has stronger location, store, phone, or cust_code evidence.

Output Rules
- Return the selected customer record in the required JSON schema only.
- Populate every required field from the selected candidate record.
- If a selected field is null, blank, or missing in the selected record, return "" for that field.

Input Example
{
  "customerName": "CHOW HOUND",
  "customerStoreNumber": "3",
  "customerKeyword": "CHOW",
  "customerKeyword2": "HOUND",
  "senderEmailKeyword": "ORDERS",
  "address": "660 CHICAGO ROAD",
  "city": "HOLLAND",
  "zip": "49423",
  "phone": ""
}"""


CUSTOMER_AI_IDENTIFIER_A_PROMPT = """Identifier A: independently choose the single best customer record from candidateRecords. Follow the matching priority exactly and do not guess outside the candidate records."""


CUSTOMER_AI_IDENTIFIER_B_PROMPT = """Identifier B: independently validate the single best customer record from candidateRecords. Look for contradictions before choosing and do not guess outside the candidate records."""


CUSTOMER_AI_DECIDER_SYSTEM_PROMPT = """You are assisting in matching customer records based on keyword information and two potential AI-generated customer records. Your objective is to compare both customer records against the provided keyword data and determine which record is the better match. You will analyze the alignment of key fields such as name, address, city, zip, and other relevant details with the keyword data. After your comparison, return the entire record of the better match.

If only one of the two customer records is a valid object with a cust_code value, return that one and ignore the other.

Evaluation Criteria:
Customer Name Match: Check if the customer_name field closely aligns with the customerName from the keywords. Look for similar patterns or keywords within both names.
Address Match: Compare the location_address1 with the address field from the keyword data. Minor formatting differences such as Rd vs Road should still count as a match.
City Match: Compare the location_city with the city field. If the cities match, this significantly increases relevance.
ZIP Code Match: If the location_zip matches the zip from the keyword data, treat it as a strong indicator.
Keyword and Email Domain Match: Check if the customer name keywords such as PREMIER or SUPPLY, or senderEmailKeyword, are contained in either customer_name or customer_email fields.
Confidence Heuristic: If one record matches on most fields such as name, address, city, and zip, select it as the better match. If both records are similar, prioritize the record that aligns more closely with the customerName and city fields.

Output: Return the entire customer record of the better match based on the above criteria. If neither record is clearly right, return the required schema with every field set to "" and cust_code set to "".

Output Schema:
{
  "customer_name": "string",
  "customer_store_number": "string or null",
  "location_address1": "string",
  "location_city": "string",
  "location_state": "string",
  "location_zip": "string",
  "phone": "string or null",
  "customer_website": "string or null",
  "customer_email": "string or null",
  "cust_code": "string"
}"""


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
class CustomerAiConsensusResult:
    customer_id: str | None = None
    customer_code: str | None = None
    route_number: str | None = None
    match_method: str = "foundryCustomerConsensus"
    confidence: float = 0.0
    candidates: list[dict[str, Any]] | None = None
    choices: list[dict[str, Any]] | None = None
    reasons: list[str] | None = None


class CustomerAiIdentifier(Protocol):
    def identify(
        self,
        email: EmailMessage,
        signals: "CustomerSignals",
        candidate_records: list[dict[str, Any]],
    ) -> CustomerAiConsensusResult:
        """Run consensus customer identification over candidate records."""


class CustomerAiJsonClient(Protocol):
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_payload: Mapping[str, Any],
        schema: Mapping[str, Any],
        schema_name: str,
        temperature: float,
    ) -> dict[str, Any]:
        """Return one JSON object from a Foundry-compatible chat model."""


@dataclass(frozen=True, slots=True)
class CustomerSignals:
    customer_code: str | None = None
    store_number: str | None = None
    route_number: str | None = None
    sender_email: str | None = None
    sender_domain: str | None = None
    subject: str = ""
    text: str = ""
    attachment_names: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "customerCode": self.customer_code,
            "storeNumber": self.store_number,
            "routeNumber": self.route_number,
            "senderEmail": self.sender_email,
            "senderDomain": self.sender_domain,
            "subject": self.subject,
            "attachmentNames": self.attachment_names or [],
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
ALIAS_SENDER_EMAIL_TYPES = {"senderemail", "sender_email", "email", "emailaddress", "email_address"}
ALIAS_DOMAIN_TYPES = {"senderdomain", "sender_domain", "domain", "emaildomain", "email_domain"}
ALIAS_SUBJECT_PATTERN_TYPES = {
    "knownsubjectpattern",
    "known_subject_pattern",
    "subjectpattern",
    "subject_pattern",
    "subject",
}
ALIAS_BODY_PATTERN_TYPES = {"bodypattern", "body_pattern", "bodyregex", "body_regex", "body"}
ALIAS_FILE_NAME_PATTERN_TYPES = {
    "filenamepattern",
    "file_name_pattern",
    "filenameregex",
    "file_name_regex",
    "attachmentnamepattern",
    "attachment_name_pattern",
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


def _sender_email(sender: str) -> str | None:
    _, address = parseaddr(sender or "")
    value = (address or sender or "").strip().lower()
    return value if "@" in value else None


def extract_customer_signals(email: EmailMessage) -> CustomerSignals:
    text = _combined_text(email)
    return CustomerSignals(
        customer_code=_first_match(CUSTOMER_CODE_PATTERNS, text),
        store_number=_first_match(STORE_NUMBER_PATTERNS, text),
        route_number=_first_match(ROUTE_NUMBER_PATTERNS, text),
        sender_email=_sender_email(email.sender),
        sender_domain=_sender_domain(email.sender),
        subject=email.subject or "",
        text=text,
        attachment_names=[attachment.name for attachment in email.attachments if attachment.name],
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


def _sender_emails(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [*_raw_list(customer, "senderEmails", "sender_emails")]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_SENDER_EMAIL_TYPES:
            values.append(alias.normalized_value or alias.value)
    return [value.strip().lower() for value in values if value.strip()]


def _known_subject_patterns(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [
        *customer.known_subject_patterns,
        *_raw_list(customer, "knownSubjectPatterns", "known_subject_patterns", "subjectPatterns", "subject_patterns"),
    ]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_SUBJECT_PATTERN_TYPES:
            values.append(alias.value)
    return [value for value in values if value]


def _known_body_patterns(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [*_raw_list(customer, "bodyPatterns", "body_patterns", "bodyRegex", "body_regex")]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_BODY_PATTERN_TYPES:
            values.append(alias.value)
    return [value for value in values if value]


def _known_file_name_patterns(customer: CustomerProfile, aliases: list[CustomerAlias]) -> list[str]:
    values = [
        *_raw_list(
            customer,
            "fileNamePatterns",
            "file_name_patterns",
            "attachmentNamePatterns",
            "attachment_name_patterns",
        )
    ]
    for alias in aliases:
        if _alias_type(alias.alias_type) in ALIAS_FILE_NAME_PATTERN_TYPES:
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

        if signals.sender_email and signals.sender_email in set(_sender_emails(customer, customer_aliases)):
            candidates.append(
                DeterministicCandidate(
                    customer=customer,
                    match_method="senderEmail",
                    confidence=0.94,
                    reason=f"sender email {signals.sender_email} matched",
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

        for pattern in _known_body_patterns(customer, customer_aliases):
            if _safe_regex_match(pattern, signals.text):
                candidates.append(
                    DeterministicCandidate(
                        customer=customer,
                        match_method="bodyPattern",
                        confidence=0.91,
                        reason=f"body matched known pattern for {customer.id}",
                    )
                )
                break

        attachment_names = "\n".join(signals.attachment_names or [])
        for pattern in _known_file_name_patterns(customer, customer_aliases):
            if _safe_regex_match(pattern, attachment_names):
                candidates.append(
                    DeterministicCandidate(
                        customer=customer,
                        match_method="fileNamePattern",
                        confidence=0.91,
                        reason=f"attachment name matched known pattern for {customer.id}",
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
        address1=_pick_mapping(mapping, "address1", "locationAddress1", "location_address1", default=""),
        city=_pick_mapping(mapping, "city", "locationCity", "location_city", default=""),
        state=_pick_mapping(mapping, "state", "locationState", "location_state", default=""),
        postal_code=_pick_mapping(mapping, "postalCode", "postal_code", "locationZip", "location_zip", default=""),
        phone=_pick_mapping(mapping, "phone", default=""),
        website=_pick_mapping(mapping, "website", "customerWebsite", "customer_website", default=""),
        customer_email=_pick_mapping(mapping, "customerEmail", "customer_email", default=""),
        sender_domains=list(_as_list(_pick_mapping(mapping, "senderDomains", "sender_domains", default=[]))),
        aliases=list(_as_list(_pick_mapping(mapping, "aliases", default=[]))),
        known_subject_patterns=list(
            _as_list(_pick_mapping(mapping, "knownSubjectPatterns", "known_subject_patterns", default=[]))
        ),
        embedding=[float(value) for value in _as_list(_pick_mapping(mapping, "embedding", default=[]))],
        custom_fields=dict(_pick_mapping(mapping, "customFields", "custom_fields", default={}) or {}),
        raw_source=dict(_pick_mapping(mapping, "rawSource", "raw_source", default={}) or {}),
    )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int_from_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _truncate(value: str, limit: int) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[:limit]


def _json_object_from_text(content: Any) -> dict[str, Any]:
    if isinstance(content, Mapping):
        return dict(content)
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("customer identification model returned non-object JSON")
    return parsed


class FoundryCustomerAiJsonClient:
    """Azure AI Foundry/Azure OpenAI chat client for customer ID prompts."""

    def __init__(
        self,
        endpoint: str | None = None,
        deployment: str | None = None,
        api_version: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.endpoint = (
            endpoint
            or os.environ.get("AZURE_AI_FOUNDRY_OPENAI_ENDPOINT")
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
            or os.environ.get("AZURE_AI_FOUNDRY_ENDPOINT", "")
        )
        self.deployment = (
            deployment
            or os.environ.get("AZURE_AI_FOUNDRY_CUSTOMER_ID_DEPLOYMENT")
            or os.environ.get("AZURE_OPENAI_CUSTOMER_ID_DEPLOYMENT")
            or os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "")
        )
        self.api_version = (
            api_version
            or os.environ.get("AZURE_AI_FOUNDRY_OPENAI_API_VERSION")
            or os.environ.get("AZURE_OPENAI_API_VERSION")
            or "2024-08-01-preview"
        )
        self.api_key = api_key or os.environ.get("AZURE_AI_FOUNDRY_API_KEY") or os.environ.get(
            "AZURE_OPENAI_API_KEY",
            "",
        )
        if not self.endpoint:
            raise ValueError("AZURE_AI_FOUNDRY_OPENAI_ENDPOINT or AZURE_OPENAI_ENDPOINT is required.")
        if not self.deployment:
            raise ValueError("AZURE_AI_FOUNDRY_CUSTOMER_ID_DEPLOYMENT or AZURE_OPENAI_CUSTOMER_ID_DEPLOYMENT is required.")

    def _client(self) -> Any:
        try:
            from openai import AzureOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency.
            raise RuntimeError("The openai package is required for Foundry customer ID prompts.") from exc

        if self.api_key:
            return AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
            )

        try:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency.
            raise RuntimeError("azure-identity is required for managed identity Foundry auth.") from exc

        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        return AzureOpenAI(
            azure_endpoint=self.endpoint,
            azure_ad_token_provider=token_provider,
            api_version=self.api_version,
        )

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_payload: Mapping[str, Any],
        schema: Mapping[str, Any],
        schema_name: str,
        temperature: float,
    ) -> dict[str, Any]:
        client = self._client()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True, default=str)},
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": dict(schema),
            },
        }
        try:
            response = client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                response_format=response_format,
                temperature=temperature,
            )
        except Exception as exc:
            message = str(exc).lower()
            if "response_format" not in message and "json_schema" not in message:
                raise
            response = client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=temperature,
            )
        content = response.choices[0].message.content
        return _json_object_from_text(content)


def _flow_record_from_customer(customer: CustomerProfile) -> dict[str, Any]:
    return {
        "customer_name": customer.name or "",
        "customer_store_number": customer.store_number or "",
        "location_address1": customer.address1 or "",
        "location_city": customer.city or "",
        "location_state": customer.state or "",
        "location_zip": customer.postal_code or "",
        "phone": customer.phone or "",
        "customer_website": customer.website or "",
        "customer_email": customer.customer_email or "",
        "cust_code": customer.customer_code or "",
    }


def _customer_ai_record(
    customer: CustomerProfile,
    confidence: float,
    method: str,
    reason: str,
) -> dict[str, Any]:
    record = _flow_record_from_customer(customer)
    record.update(
        {
            "customerId": customer.id,
            "customerCode": customer.customer_code,
            "routeNumber": customer.route_number,
            "senderDomains": list(customer.sender_domains),
            "sourceConfidence": round(confidence, 4),
            "sourceMethod": method,
            "sourceReason": reason,
        }
    )
    return record


def _customer_ai_candidate_records(
    deterministic_candidates: list[DeterministicCandidate],
    vector_candidates: list[CustomerVectorCandidate],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(deterministic_candidates, key=lambda candidate: candidate.confidence, reverse=True):
        if item.customer.id in seen:
            continue
        seen.add(item.customer.id)
        records.append(_customer_ai_record(item.customer, item.confidence, item.match_method, item.reason))
    for item in vector_candidates:
        if item.customer.id in seen:
            continue
        seen.add(item.customer.id)
        records.append(_customer_ai_record(item.customer, item.confidence, item.method, item.reason))
    return records


def _ai_candidate_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "customerId": record.get("customerId"),
        "customerCode": record.get("cust_code") or record.get("customerCode"),
        "name": record.get("customer_name") or record.get("name"),
        "routeNumber": record.get("routeNumber", ""),
        "storeNumber": record.get("customer_store_number", ""),
        "matchMethod": record.get("sourceMethod", "foundryCandidate"),
        "confidence": float(record.get("sourceConfidence", 0.0) or 0.0),
        "reason": record.get("sourceReason", ""),
    }


def _record_output_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {name: record.get(name, "") for name in CUSTOMER_AI_RECORD_SCHEMA["properties"]}


def _records_by_code(candidate_records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_code: dict[str, dict[str, Any]] = {}
    for record in candidate_records:
        code = normalize_identifier(str(record.get("cust_code") or record.get("customerCode") or ""))
        if code and code not in by_code:
            by_code[code] = record
    return by_code


def _choice_from_response(
    prompt_name: str,
    response: Mapping[str, Any],
    candidate_records: list[dict[str, Any]],
) -> dict[str, Any]:
    code = normalize_identifier(str(response.get("cust_code") or ""))
    matched = _records_by_code(candidate_records).get(code)
    return {
        "prompt": prompt_name,
        "customerCode": matched.get("cust_code") if matched else (str(response.get("cust_code") or "").strip() or None),
        "customerId": matched.get("customerId") if matched else None,
        "valid": bool(matched),
        "record": _record_output_payload(response),
    }


def _first_regex_value(pattern: str, text: str) -> str:
    match = re.search(pattern, text or "", re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _customer_keywords(text: str) -> tuple[str, str]:
    stop_words = {
        "ORDER",
        "ORDERS",
        "PURCHASE",
        "RECEIPT",
        "PLEASE",
        "PROCESS",
        "FRONTIER",
        "DISTRIBUTING",
        "CUSTOMER",
        "EMAIL",
        "ATTACHED",
        "ATTACHMENT",
        "FROM",
        "FOR",
        "THE",
        "AND",
        "WITH",
        "THIS",
    }
    tokens: list[str] = []
    for token in re.findall(r"[A-Z0-9]+", (text or "").upper()):
        if len(token) < 3 or token in stop_words:
            continue
        if token.isdigit():
            continue
        if token not in tokens:
            tokens.append(token)
    first = tokens[0] if tokens else ""
    second = tokens[1] if len(tokens) > 1 else ""
    return first, second


def _customer_search_criteria(email: EmailMessage, signals: CustomerSignals) -> dict[str, Any]:
    keyword, keyword2 = _customer_keywords(f"{email.subject or ''}\n{email.body_text or ''}")
    phone = _first_regex_value(r"(\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})", signals.text)
    zip_code = _first_regex_value(r"\b(\d{5}(?:-\d{4})?)\b", signals.text)
    sender_keyword = ""
    if signals.sender_domain and signals.sender_domain != "frontierdistributing.com":
        sender_keyword = signals.sender_email or signals.sender_domain
    return {
        "customerName": _truncate(email.subject or "", 180),
        "customerStoreNumber": signals.store_number or "",
        "customerKeyword": keyword,
        "customerKeyword2": keyword2,
        "senderEmailKeyword": sender_keyword,
        "address": "",
        "city": "",
        "zip": zip_code,
        "phone": phone,
        "cust_code": signals.customer_code or "",
    }


def _customer_ai_user_payload(
    email: EmailMessage,
    signals: CustomerSignals,
    candidate_records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "customerInformation": _customer_search_criteria(email, signals),
        "emailContext": {
            "subject": email.subject or "",
            "sender": email.sender or "",
            "bodyText": _truncate(email.body_text or email.body_html or "", 6000),
            "attachmentNames": signals.attachment_names or [],
        },
        "candidateRecords": candidate_records,
    }


class FoundryCustomerAiConsensusIdentifier:
    """Run the Power Automate two-pass customer ID consensus flow on Foundry."""

    def __init__(
        self,
        chat_client: CustomerAiJsonClient | None = None,
        attempts: int = DEFAULT_CUSTOMER_AI_ATTEMPTS,
        candidate_limit: int = DEFAULT_CUSTOMER_AI_CANDIDATE_LIMIT,
        temperature: float = 0.3,
    ) -> None:
        self.chat_client = chat_client or FoundryCustomerAiJsonClient()
        self.attempts = max(1, attempts)
        self.candidate_limit = max(1, candidate_limit)
        self.temperature = temperature

    def identify(
        self,
        email: EmailMessage,
        signals: CustomerSignals,
        candidate_records: list[dict[str, Any]],
    ) -> CustomerAiConsensusResult:
        records = candidate_records[: self.candidate_limit]
        candidate_payloads = [_ai_candidate_payload(record) for record in records]
        if not records:
            return CustomerAiConsensusResult(
                confidence=0.0,
                candidates=[],
                choices=[],
                reasons=["foundry customer consensus had no candidate records"],
            )

        base_payload = _customer_ai_user_payload(email, signals, records)
        choices: list[dict[str, Any]] = []
        for attempt in range(1, self.attempts + 1):
            first = self._run_identifier(
                "identifierA",
                CUSTOMER_AI_IDENTIFIER_A_PROMPT,
                base_payload,
                records,
                attempt,
            )
            second = self._run_identifier(
                "identifierB",
                CUSTOMER_AI_IDENTIFIER_B_PROMPT,
                base_payload,
                records,
                attempt,
            )
            choices.extend([first, second])
            if (
                first.get("valid")
                and second.get("valid")
                and first.get("customerCode")
                and normalize_identifier(str(first.get("customerCode"))) == normalize_identifier(str(second.get("customerCode")))
            ):
                return self._matched_result(
                    first,
                    records,
                    candidate_payloads,
                    choices,
                    confidence=0.94,
                    method="foundryCustomerConsensus",
                    reasons=[f"foundry customer identifiers agreed on {first.get('customerCode')}"],
                )

        decider = self._run_decider(base_payload, records, choices)
        choices.append(decider)
        if decider.get("valid") and decider.get("customerCode"):
            return self._matched_result(
                decider,
                records,
                candidate_payloads,
                choices,
                confidence=0.88,
                method="foundryCustomerDecider",
                reasons=[f"foundry customer decider selected {decider.get('customerCode')} after identifier disagreement"],
            )

        return CustomerAiConsensusResult(
            confidence=0.0,
            candidates=candidate_payloads,
            choices=choices,
            reasons=["foundry customer identifiers did not agree and decider returned no customer code"],
        )

    def _run_identifier(
        self,
        prompt_name: str,
        role_prompt: str,
        base_payload: Mapping[str, Any],
        records: list[dict[str, Any]],
        attempt: int,
    ) -> dict[str, Any]:
        payload = dict(base_payload)
        payload["attempt"] = {"number": attempt, "role": prompt_name, "instructions": role_prompt}
        response = self.chat_client.complete_json(
            system_prompt=f"{CUSTOMER_AI_SYSTEM_PROMPT}\n\n{role_prompt}",
            user_payload=payload,
            schema=CUSTOMER_AI_RECORD_SCHEMA,
            schema_name="customer_info",
            temperature=self.temperature,
        )
        return _choice_from_response(prompt_name, response, records)

    def _run_decider(
        self,
        base_payload: Mapping[str, Any],
        records: list[dict[str, Any]],
        choices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = dict(base_payload)
        payload["identifierAnswers"] = choices
        response = self.chat_client.complete_json(
            system_prompt=CUSTOMER_AI_DECIDER_SYSTEM_PROMPT,
            user_payload=payload,
            schema=CUSTOMER_AI_RECORD_SCHEMA,
            schema_name="customer_info",
            temperature=self.temperature,
        )
        return _choice_from_response("decider", response, records)

    def _matched_result(
        self,
        choice: Mapping[str, Any],
        records: list[dict[str, Any]],
        candidate_payloads: list[dict[str, Any]],
        choices: list[dict[str, Any]],
        confidence: float,
        method: str,
        reasons: list[str],
    ) -> CustomerAiConsensusResult:
        code = normalize_identifier(str(choice.get("customerCode") or ""))
        record = _records_by_code(records).get(code, {})
        return CustomerAiConsensusResult(
            customer_id=str(record.get("customerId") or ""),
            customer_code=str(record.get("cust_code") or ""),
            route_number=str(record.get("routeNumber") or ""),
            match_method=method,
            confidence=confidence,
            candidates=candidate_payloads,
            choices=list(choices),
            reasons=reasons,
        )


def customer_vector_search_from_environment(repository: Any) -> CustomerVectorSearch | None:
    enabled = os.environ.get("ORDER_PROCESSOR_ENABLE_CUSTOMER_VECTOR_SEARCH", "").strip().lower()
    if enabled not in {"1", "true", "yes"}:
        return None
    return CosmosCustomerVectorSearch(repository, AzureOpenAIEmbeddingClient())


def customer_ai_identifier_from_environment() -> CustomerAiIdentifier | None:
    if not _truthy(os.environ.get("ORDER_PROCESSOR_ENABLE_CUSTOMER_AI_CONSENSUS")):
        return None
    attempts = _int_from_env("ORDER_PROCESSOR_CUSTOMER_AI_MAX_ATTEMPTS", DEFAULT_CUSTOMER_AI_ATTEMPTS, 1, 10)
    candidate_limit = _int_from_env(
        "ORDER_PROCESSOR_CUSTOMER_AI_CANDIDATE_LIMIT",
        DEFAULT_CUSTOMER_AI_CANDIDATE_LIMIT,
        1,
        25,
    )
    return FoundryCustomerAiConsensusIdentifier(attempts=attempts, candidate_limit=candidate_limit)


def identify_customer(
    email: EmailMessage,
    customers: list[CustomerProfile],
    aliases: list[CustomerAlias] | None = None,
    vector_search: CustomerVectorSearch | None = None,
    ai_identifier: CustomerAiIdentifier | None = None,
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
        if ai_identifier and vector_candidates:
            ai_candidate_records = _customer_ai_candidate_records(candidates, vector_candidates)
            try:
                ai_result = ai_identifier.identify(email, signals, ai_candidate_records)
            except Exception as exc:
                return CustomerIdentificationResult(
                    status=MatchStatus.UNRESOLVED,
                    route_number=signals.route_number,
                    match_method="foundryCustomerConsensus",
                    confidence=0.0,
                    candidates=payloads,
                    reasons=[f"foundry customer consensus failed: {exc}"],
                    extracted_signals=signals.as_dict(),
                )
            if ai_result.customer_id and ai_result.customer_code:
                return CustomerIdentificationResult(
                    status=MatchStatus.MATCHED,
                    customer_id=ai_result.customer_id,
                    customer_code=ai_result.customer_code,
                    route_number=ai_result.route_number,
                    match_method=ai_result.match_method,
                    confidence=ai_result.confidence,
                    candidates=ai_result.candidates or payloads,
                    reasons=ai_result.reasons or ["foundry customer consensus produced a customer match"],
                    extracted_signals={
                        **signals.as_dict(),
                        "foundryCustomerConsensus": {
                            "choices": ai_result.choices or [],
                        },
                    },
                )
            top = vector_candidates[0]
            return CustomerIdentificationResult(
                status=MatchStatus.POSSIBLE_MATCH,
                route_number=top.customer.route_number,
                match_method="foundryCustomerConsensus",
                confidence=0.0,
                candidates=ai_result.candidates or payloads,
                reasons=ai_result.reasons or ["foundry customer consensus returned no customer code"],
                extracted_signals={
                    **signals.as_dict(),
                    "foundryCustomerConsensus": {
                        "choices": ai_result.choices or [],
                    },
                },
            )

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

    if ai_identifier and candidates:
        ai_candidate_records = _customer_ai_candidate_records(candidates, [])
        try:
            ai_result = ai_identifier.identify(email, signals, ai_candidate_records)
        except Exception as exc:
            return CustomerIdentificationResult(
                status=MatchStatus.UNRESOLVED,
                route_number=signals.route_number,
                match_method="foundryCustomerConsensus",
                confidence=0.0,
                candidates=[
                    _candidate_payload(item.customer, item.confidence, item.match_method, item.reason)
                    for item in candidates[:5]
                ],
                reasons=[f"foundry customer consensus failed: {exc}"],
                extracted_signals=signals.as_dict(),
            )
        if ai_result.customer_id and ai_result.customer_code:
            return CustomerIdentificationResult(
                status=MatchStatus.MATCHED,
                customer_id=ai_result.customer_id,
                customer_code=ai_result.customer_code,
                route_number=ai_result.route_number,
                match_method=ai_result.match_method,
                confidence=ai_result.confidence,
                candidates=ai_result.candidates
                or [
                    _candidate_payload(item.customer, item.confidence, item.match_method, item.reason)
                    for item in candidates[:5]
                ],
                reasons=ai_result.reasons or ["foundry customer consensus produced a customer match"],
                extracted_signals={
                    **signals.as_dict(),
                    "foundryCustomerConsensus": {
                        "choices": ai_result.choices or [],
                    },
                },
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
