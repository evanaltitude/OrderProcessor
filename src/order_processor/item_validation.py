from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

from .data_model import GLOBAL_CUSTOMER_ID
from .models import ItemRecord, ItemValidationResult, MatchStatus


DEFAULT_CONFIDENCE_THRESHOLD = 0.9
DEFAULT_POSSIBLE_MATCH_THRESHOLD = 0.55
DEFAULT_CANDIDATE_LIMIT = 5
DEFAULT_AMBIGUITY_DELTA = 0.02


ITEM_NUMBER_FIELDS = [
    "providedItemNumber",
    "provided_item_number",
    "itemNumber",
    "item_number",
    "item",
    "sku",
    "supplierCode",
    "suppliercode",
    "vendorItem",
    "vendor_item",
    "ourItemNo",
    "our_item_no",
    "productCode",
    "product_code",
    "Column 1",
]
UPC_FIELDS = ["providedUpc", "provided_upc", "upc", "barcode", "barCode", "gtin", "Column 2"]
DESCRIPTION_FIELDS = [
    "description",
    "itemDescription",
    "item_description",
    "product",
    "productDescription",
    "product_description",
    "Column 4",
    "Column 5",
]


def normalize_item_token(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def normalize_upc(value: str) -> str:
    return re.sub(r"[^0-9]", "", value or "")


def normalize_description(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _description_score(a: str, b: str) -> float:
    left = normalize_description(a)
    right = normalize_description(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 0.88
    return SequenceMatcher(None, left, right).ratio()


def score_item_match(
    item: ItemRecord,
    provided_item_number: str = "",
    provided_upc: str = "",
    description: str = "",
) -> tuple[float, str]:
    candidate = score_item_candidate(item, provided_item_number, provided_upc, description)
    return float(candidate["confidence"]), str(candidate["method"])


def score_item_candidate(
    item: ItemRecord,
    provided_item_number: str = "",
    provided_upc: str = "",
    description: str = "",
) -> dict[str, Any]:
    item_number = normalize_item_token(provided_item_number)
    upc = normalize_upc(provided_upc)
    item_upc = normalize_upc(item.upc)

    searchable_numbers = {
        normalize_item_token(item.internal_item_number),
        *(normalize_item_token(value) for value in item.alt_parts_combined),
        *(normalize_item_token(value) for value in item.customer_item_numbers),
        *(normalize_item_token(value) for value in item.aliases),
    }
    searchable_numbers.discard("")

    scores: list[tuple[float, str, str]] = []
    if item_number and item_number in searchable_numbers:
        scores.append((1.0, "itemNumberExact", "provided item number matched an item number or alias"))

    if upc and item_upc and upc == item_upc:
        scores.append((0.98, "upcExact", "provided UPC matched the canonical item UPC"))

    if item_number and searchable_numbers:
        fuzzy_scores = [
            SequenceMatcher(None, item_number, candidate).ratio()
            for candidate in searchable_numbers
            if candidate
        ]
        best_fuzzy = max(fuzzy_scores, default=0.0)
        if best_fuzzy >= 0.9:
            scores.append((best_fuzzy, "itemNumberFuzzy", "provided item number closely matched an item number or alias"))

    description_match = _description_score(description, item.description)
    if description_match >= 0.86:
        scores.append((description_match, "descriptionFuzzy", "description closely matched the item description"))

    if not scores:
        scores.append((max(description_match, 0.0), "noMatch", "no strong item number, UPC, or description match"))

    confidence, method, reason = max(scores, key=lambda score: score[0])
    return {
        "itemId": item.id,
        "internalItemNumber": item.internal_item_number,
        "description": item.description,
        "upc": item.upc,
        "altPartsCombined": list(item.alt_parts_combined),
        "customerItemNumbers": list(item.customer_item_numbers),
        "aliases": list(item.aliases),
        "confidence": round(confidence, 4),
        "method": method,
        "reason": reason,
    }


def validate_item(
    tenant_id: str,
    customer_id: str,
    provided_item_number: str = "",
    provided_upc: str = "",
    description: str = "",
    items: list[ItemRecord] | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    row_context: dict[str, Any] | None = None,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    possible_match_threshold: float = DEFAULT_POSSIBLE_MATCH_THRESHOLD,
    ambiguity_delta: float = DEFAULT_AMBIGUITY_DELTA,
) -> ItemValidationResult:
    items = items or []
    row_context = row_context or {}
    provided_item_number, provided_upc, description = _coalesce_input_fields(
        provided_item_number,
        provided_upc,
        description,
        row_context,
    )

    if not customer_id:
        return ItemValidationResult(
            status=MatchStatus.UNRESOLVED,
            candidates=[],
            unresolved_reason="customer ID is required for item validation",
        )

    if not any([normalize_item_token(provided_item_number), normalize_upc(provided_upc), normalize_description(description)]):
        return ItemValidationResult(
            status=MatchStatus.UNRESOLVED,
            candidates=[],
            unresolved_reason="provided item number, UPC, or description is required",
        )

    scoped_items = [
        item
        for item in items
        if item.tenant_id == tenant_id and item.customer_id in {customer_id, GLOBAL_CUSTOMER_ID}
    ]
    if not scoped_items:
        return ItemValidationResult(
            status=MatchStatus.UNRESOLVED,
            candidates=[],
            unresolved_reason="no candidate items in customer or master item list",
        )

    candidates = [
        candidate
        for candidate in (
            score_item_candidate(item, provided_item_number, provided_upc, description)
            for item in scoped_items
        )
        if candidate["confidence"] > 0
    ]
    candidates.sort(key=lambda candidate: candidate["confidence"], reverse=True)
    limited_candidates = candidates[: max(1, int(candidate_limit or DEFAULT_CANDIDATE_LIMIT))]

    if not limited_candidates:
        return ItemValidationResult(
            status=MatchStatus.UNRESOLVED,
            candidates=[],
            unresolved_reason="no candidate items matched provided values",
        )

    best = limited_candidates[0]
    second = limited_candidates[1] if len(limited_candidates) > 1 else None
    if _is_ambiguous(best, second, confidence_threshold, ambiguity_delta):
        return ItemValidationResult(
            status=MatchStatus.POSSIBLE_MATCH,
            match_method=best["method"],
            confidence=best["confidence"],
            candidates=limited_candidates,
            unresolved_reason="multiple candidate items are within the ambiguity threshold",
        )

    if best["confidence"] >= confidence_threshold:
        return ItemValidationResult(
            status=MatchStatus.MATCHED,
            matched_item_id=best["itemId"],
            matched_internal_item_number=best["internalItemNumber"],
            match_method=best["method"],
            confidence=best["confidence"],
            candidates=limited_candidates,
        )

    if best["confidence"] >= possible_match_threshold:
        return ItemValidationResult(
            status=MatchStatus.POSSIBLE_MATCH,
            match_method=best["method"],
            confidence=best["confidence"],
            candidates=limited_candidates,
            unresolved_reason="best candidate below confidence threshold",
        )

    return ItemValidationResult(
        status=MatchStatus.UNRESOLVED,
        match_method=best["method"],
        confidence=best["confidence"],
        candidates=limited_candidates,
        unresolved_reason="no candidate met the possible-match threshold",
    )


def _coalesce_input_fields(
    provided_item_number: str,
    provided_upc: str,
    description: str,
    row_context: dict[str, Any],
) -> tuple[str, str, str]:
    item_number = provided_item_number or _first_context_value(row_context, ITEM_NUMBER_FIELDS)
    upc = provided_upc or _first_context_value(row_context, UPC_FIELDS)
    item_description = description or _first_context_value(row_context, DESCRIPTION_FIELDS)
    return str(item_number or ""), str(upc or ""), str(item_description or "")


def _first_context_value(row_context: dict[str, Any], fields: list[str]) -> str:
    normalized = {_normalize_field_name(key): value for key, value in row_context.items()}
    for field in fields:
        value = normalized.get(_normalize_field_name(field))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _is_ambiguous(
    best: dict[str, Any],
    second: dict[str, Any] | None,
    confidence_threshold: float,
    ambiguity_delta: float,
) -> bool:
    if not second:
        return False
    if best["confidence"] < confidence_threshold or second["confidence"] < confidence_threshold:
        return False
    if best["internalItemNumber"] == second["internalItemNumber"]:
        return False
    return abs(float(best["confidence"]) - float(second["confidence"])) <= ambiguity_delta
