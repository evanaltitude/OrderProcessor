from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.customer_identification import (
    CustomerVectorCandidate,
    StaticEmbeddingClient,
    identify_customer,
)
from order_processor.models import CustomerAlias, CustomerProfile, EmailMessage, MatchStatus


class FakeVectorSearch:
    def __init__(self, candidates: list[CustomerVectorCandidate]) -> None:
        self.candidates = candidates
        self.calls = 0

    def search(
        self,
        tenant_id: str,
        query_text: str,
        limit: int = 5,
    ) -> list[CustomerVectorCandidate]:
        self.calls += 1
        return self.candidates[:limit]


class CustomerIdentificationTests(unittest.TestCase):
    def test_matches_customer_code_before_ai_fallback(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@unknown.example",
            subject="Customer: PILOT order",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="pilot-customer",
                tenant_id="altitude",
                customer_code="PILOT",
                name="Pilot Customer",
            )
        ]

        result = identify_customer(email, customers)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.customer_id, "pilot-customer")
        self.assertEqual(result.match_method, "customerCode")
        self.assertEqual(result.extracted_signals["customerCode"], "PILOT")

    def test_matches_sender_domain(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@pilot.example",
            subject="PO 12345",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="pilot-customer",
                tenant_id="altitude",
                customer_code="PILOT",
                name="Pilot Customer",
                sender_domains=["pilot.example"],
            )
        ]

        result = identify_customer(email, customers)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.match_method, "senderDomain")

    def test_deterministic_match_prevents_vector_fallback(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@pilot.example",
            subject="PO 12345",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="pilot-customer",
                tenant_id="altitude",
                customer_code="PILOT",
                name="Pilot Customer",
                sender_domains=["pilot.example"],
            )
        ]
        vector_search = FakeVectorSearch(
            [
                CustomerVectorCandidate(
                    customer=CustomerProfile(
                        id="other-customer",
                        tenant_id="altitude",
                        customer_code="OTHER",
                        name="Other Customer",
                    ),
                    confidence=0.99,
                )
            ]
        )

        result = identify_customer(email, customers, vector_search=vector_search)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.customer_id, "pilot-customer")
        self.assertEqual(vector_search.calls, 0)

    def test_matches_store_number(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="Store #101 order",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="pilot-customer",
                tenant_id="altitude",
                customer_code="PILOT",
                name="Pilot Customer",
                store_number="101",
            )
        ]

        result = identify_customer(email, customers)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.match_method, "storeNumber")

    def test_matches_route_number(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="Route: R12 order",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="pilot-customer",
                tenant_id="altitude",
                customer_code="PILOT",
                name="Pilot Customer",
                route_number="R12",
            )
        ]

        result = identify_customer(email, customers)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.match_method, "routeNumber")

    def test_matches_known_subject_pattern(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="DOROTHY LANE MARKET order 123",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="dorothy-lane",
                tenant_id="altitude",
                customer_code="DLM",
                name="Dorothy Lane",
                known_subject_patterns=[r"DOROTHY\s+LANE"],
            )
        ]

        result = identify_customer(email, customers)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.match_method, "knownSubjectPattern")

    def test_matches_customer_alias_records(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="Customer: PFC order",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="pet-food-center",
                tenant_id="altitude",
                customer_code="PETFOOD",
                name="Pet Food Center",
            )
        ]
        aliases = [
            CustomerAlias(
                id="alias-1",
                tenant_id="altitude",
                customer_id="pet-food-center",
                alias_type="customerCode",
                value="PFC",
                normalized_value="PFC",
            )
        ]

        result = identify_customer(email, customers, aliases=aliases)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.customer_id, "pet-food-center")
        self.assertEqual(result.match_method, "customerCode")

    def test_ambiguous_route_number_requires_human_review(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="Route 10 order",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="customer-1",
                tenant_id="altitude",
                customer_code="C1",
                name="Customer 1",
                route_number="10",
            ),
            CustomerProfile(
                id="customer-2",
                tenant_id="altitude",
                customer_code="C2",
                name="Customer 2",
                route_number="10",
            ),
        ]

        result = identify_customer(email, customers)

        self.assertEqual(result.status, MatchStatus.POSSIBLE_MATCH)
        self.assertEqual(result.match_method, "routeNumber")
        self.assertEqual(len(result.candidates), 2)

    def test_vector_fallback_can_resolve_ambiguous_deterministic_candidates(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="Route 10 order",
            body_text="Pilot Customer weekly order",
            received_at="2026-06-19T12:00:00Z",
        )
        customers = [
            CustomerProfile(
                id="customer-1",
                tenant_id="altitude",
                customer_code="C1",
                name="Customer 1",
                route_number="10",
            ),
            CustomerProfile(
                id="pilot-customer",
                tenant_id="altitude",
                customer_code="PILOT",
                name="Pilot Customer",
                route_number="10",
            ),
        ]
        vector_search = FakeVectorSearch(
            [
                CustomerVectorCandidate(
                    customer=customers[1],
                    confidence=0.91,
                )
            ]
        )

        result = identify_customer(email, customers, vector_search=vector_search)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.customer_id, "pilot-customer")
        self.assertEqual(result.match_method, "cosmosVectorSearch")
        self.assertEqual(vector_search.calls, 1)

    def test_confident_vector_fallback_matches_customer(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="Please process the weekly order for Pilot Customer",
            received_at="2026-06-19T12:00:00Z",
        )
        customer = CustomerProfile(
            id="pilot-customer",
            tenant_id="altitude",
            customer_code="PILOT",
            name="Pilot Customer",
        )
        vector_search = FakeVectorSearch([CustomerVectorCandidate(customer=customer, confidence=0.91)])

        result = identify_customer(email, [customer], vector_search=vector_search)

        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.customer_id, "pilot-customer")
        self.assertEqual(result.match_method, "cosmosVectorSearch")
        self.assertEqual(vector_search.calls, 1)

    def test_low_confidence_vector_fallback_stays_possible_match(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="Please process this order",
            received_at="2026-06-19T12:00:00Z",
        )
        customer = CustomerProfile(
            id="pilot-customer",
            tenant_id="altitude",
            customer_code="PILOT",
            name="Pilot Customer",
        )
        vector_search = FakeVectorSearch([CustomerVectorCandidate(customer=customer, confidence=0.72)])

        result = identify_customer(email, [customer], vector_search=vector_search)

        self.assertEqual(result.status, MatchStatus.POSSIBLE_MATCH)
        self.assertIsNone(result.customer_id)
        self.assertEqual(result.candidates[0]["customerId"], "pilot-customer")

    def test_static_embedding_client_is_deterministic(self) -> None:
        embedder = StaticEmbeddingClient()

        self.assertEqual(embedder.embed("Pilot Customer"), embedder.embed("Pilot Customer"))
        self.assertNotEqual(embedder.embed("Pilot Customer"), embedder.embed("Other Customer"))


if __name__ == "__main__":
    unittest.main()
