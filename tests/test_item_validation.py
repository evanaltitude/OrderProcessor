from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.item_validation import normalize_item_token, validate_item
from order_processor.models import ItemRecord, MatchStatus


class ItemValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            ItemRecord(
                id="item-1",
                tenant_id="altitude",
                customer_id="pilot-customer",
                internal_item_number="10001",
                description="Dog Food 25 lb",
                upc="012345678905",
                customer_item_numbers=["PILOT123"],
            )
        ]

    def test_normalizes_item_tokens(self) -> None:
        self.assertEqual(normalize_item_token(" pilot-123 "), "PILOT123")

    def test_matches_customer_item_number(self) -> None:
        result = validate_item(
            tenant_id="altitude",
            customer_id="pilot-customer",
            provided_item_number="PILOT-123",
            provided_upc="",
            description="",
            items=self.items,
        )

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.matched_item_id, "item-1")
        self.assertEqual(result.matched_internal_item_number, "10001")
        self.assertEqual(result.match_method, "itemNumberExact")
        self.assertEqual(result.candidates[0]["itemId"], "item-1")

    def test_uses_row_context_when_explicit_fields_are_missing(self) -> None:
        result = validate_item(
            tenant_id="altitude",
            customer_id="pilot-customer",
            items=self.items,
            row_context={"Vendor Item": "PILOT-123"},
        )

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.matched_internal_item_number, "10001")

    def test_possible_match_when_candidate_is_below_confidence_threshold(self) -> None:
        result = validate_item(
            tenant_id="altitude",
            customer_id="pilot-customer",
            provided_item_number="",
            provided_upc="",
            description="Dog Food",
            items=self.items,
            confidence_threshold=0.9,
        )

        self.assertEqual(result.status, MatchStatus.POSSIBLE_MATCH)
        self.assertEqual(result.unresolved_reason, "best candidate below confidence threshold")

    def test_ambiguous_exact_matches_require_review(self) -> None:
        result = validate_item(
            tenant_id="altitude",
            customer_id="pilot-customer",
            provided_item_number="PILOT-123",
            provided_upc="",
            description="",
            items=[
                *self.items,
                ItemRecord(
                    id="item-2",
                    tenant_id="altitude",
                    customer_id="pilot-customer",
                    internal_item_number="10002",
                    description="Dog Food Alternate",
                    customer_item_numbers=["PILOT123"],
                ),
            ],
        )

        self.assertEqual(result.status, MatchStatus.POSSIBLE_MATCH)
        self.assertEqual(result.unresolved_reason, "multiple candidate items are within the ambiguity threshold")
        self.assertEqual(len(result.candidates), 2)

    def test_unresolved_when_customer_scope_has_no_items(self) -> None:
        result = validate_item(
            tenant_id="altitude",
            customer_id="other-customer",
            provided_item_number="PILOT123",
            provided_upc="",
            description="Dog Food 25 lb",
            items=self.items,
        )

        self.assertEqual(result.status, MatchStatus.UNRESOLVED)


if __name__ == "__main__":
    unittest.main()
