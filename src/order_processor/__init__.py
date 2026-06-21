"""Core contracts and services for the Order Processor platform."""

from .models import (
    AuditEvent,
    CustomerIdentificationResult,
    CustomerProfile,
    EmailAttachment,
    EmailMessage,
    ExceptionTask,
    ItemRecord,
    ItemValidationResult,
    OrderLine,
    OrderRun,
    RoutingDecision,
    RoutingRule,
)

__all__ = [
    "AuditEvent",
    "CustomerIdentificationResult",
    "CustomerProfile",
    "EmailAttachment",
    "EmailMessage",
    "ExceptionTask",
    "ItemRecord",
    "ItemValidationResult",
    "OrderLine",
    "OrderRun",
    "RoutingDecision",
    "RoutingRule",
]
