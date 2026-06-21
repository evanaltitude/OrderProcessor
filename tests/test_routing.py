from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.models import EmailAttachment, EmailMessage, RoutingOutcome, RoutingRule
from order_processor.routing import default_order_signal, evaluate_routing, rule_matches


class RoutingTests(unittest.TestCase):
    def test_known_order_rule_matches_sender_subject_and_attachment(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="Buyer <buyer@pilot.example>",
            subject="PO 12345",
            received_at="2026-06-19T12:00:00Z",
            attachments=[EmailAttachment(name="order.csv", content_type="text/csv")],
        )
        rule = RoutingRule(
            id="rule-1",
            tenant_id="altitude",
            name="pilot csv",
            outcome=RoutingOutcome.KNOWN_ORDER,
            customer_id="pilot-customer",
            sender_domains=["pilot.example"],
            subject_regex=[r"\bPO\b"],
            attachment_extensions=["csv"],
            required_attachment=True,
        )

        decision = evaluate_routing(email, [rule])

        self.assertEqual(decision.outcome, RoutingOutcome.KNOWN_ORDER)
        self.assertEqual(decision.customer_id, "pilot-customer")
        self.assertEqual(decision.confidence, 1.0)

    def test_no_rule_defaults_to_customer_identification(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="question",
            received_at="2026-06-19T12:00:00Z",
        )

        decision = evaluate_routing(email, [])

        self.assertEqual(decision.outcome, RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION)

    def test_customer_scoped_mailbox_context_without_rule_needs_review(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="pilot-orders@example.com",
            mailbox_account_id="mailbox-1",
            customer_id="pilot-customer",
            message_id="message-1",
            sender="buyer@example.com",
            subject="PO 12345",
            received_at="2026-06-19T12:00:00Z",
            attachments=[EmailAttachment(name="order.xlsx")],
        )

        decision = evaluate_routing(email, [])

        self.assertEqual(decision.outcome, RoutingOutcome.NEEDS_HUMAN_REVIEW)
        self.assertEqual(decision.customer_id, "pilot-customer")
        self.assertEqual(decision.mailbox_account_id, "mailbox-1")
        self.assertTrue(default_order_signal(email))

    def test_rule_can_match_mailbox_and_content_type(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="Pilot-Orders@Example.com",
            mailbox_account_id="mailbox-1",
            customer_id="pilot-customer",
            message_id="message-1",
            sender="buyer@example.com",
            subject="New order",
            received_at="2026-06-19T12:00:00Z",
            attachments=[
                EmailAttachment(
                    name="incoming-order.dat",
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet; charset=utf-8",
                )
            ],
        )
        rule = RoutingRule(
            id="rule-1",
            tenant_id="altitude",
            name="pilot xlsx content type",
            outcome=RoutingOutcome.KNOWN_ORDER,
            customer_id="pilot-customer",
            mailbox_account_ids=["mailbox-1"],
            mailbox_addresses=["pilot-orders@example.com"],
            attachment_content_types=[
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ],
            required_attachment=True,
        )

        decision = evaluate_routing(email, [rule])

        self.assertEqual(decision.outcome, RoutingOutcome.KNOWN_ORDER)
        self.assertIn("mailbox account matched", decision.reasons)
        self.assertIn("attachment content type matched", decision.reasons)

    def test_known_customer_non_order_rule_uses_prior_processed_subject_pattern(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="pilot-orders@example.com",
            mailbox_account_id="mailbox-1",
            customer_id="pilot-customer",
            message_id="message-1",
            sender="buyer@example.com",
            subject="RE: Processed PO 12345",
            received_at="2026-06-19T12:00:00Z",
        )
        rule = RoutingRule(
            id="rule-1",
            tenant_id="altitude",
            name="already processed reply",
            outcome=RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER,
            customer_id="pilot-customer",
            prior_processed_subject_regex=[r"\bprocessed\b"],
        )

        decision = evaluate_routing(email, [rule])

        self.assertEqual(decision.outcome, RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER)
        self.assertIn("prior processed subject pattern matched", decision.reasons)

    def test_ignored_rule_can_match_known_webstore_pattern(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="pilot-orders@example.com",
            message_id="message-1",
            sender="notifications@webstore.example",
            subject="Marketplace shipment notice",
            body_text="This is a shipment confirmation, not a purchase order.",
            received_at="2026-06-19T12:00:00Z",
        )
        rule = RoutingRule(
            id="rule-1",
            tenant_id="altitude",
            name="webstore shipment notices",
            outcome=RoutingOutcome.IGNORED,
            known_webstore_patterns=[r"shipment confirmation"],
            sender_domains=["webstore.example"],
        )

        decision = evaluate_routing(email, [rule])

        self.assertEqual(decision.outcome, RoutingOutcome.IGNORED)
        self.assertIn("known webstore pattern matched", decision.reasons)

    def test_invalid_regex_does_not_crash_rule_evaluation(self) -> None:
        email = EmailMessage(
            id="email-1",
            tenant_id="altitude",
            mailbox="orders@example.com",
            message_id="message-1",
            sender="buyer@example.com",
            subject="PO 12345",
            received_at="2026-06-19T12:00:00Z",
        )
        rule = RoutingRule(
            id="rule-1",
            tenant_id="altitude",
            name="bad regex",
            outcome=RoutingOutcome.KNOWN_ORDER,
            subject_regex=["["],
        )

        matches, reasons = rule_matches(email, rule)

        self.assertFalse(matches)
        self.assertEqual(reasons, ["subject did not match configured patterns"])


if __name__ == "__main__":
    unittest.main()
