from __future__ import annotations

import json
import hmac
import os
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "src").exists():
    ROOT = APP_ROOT
for package_root in (
    APP_ROOT / ".python_packages" / "lib" / "site-packages",
    ROOT / ".python_packages" / "lib" / "site-packages",
):
    if package_root.exists() and str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from order_processor import api as order_api  # noqa: E402

try:
    import azure.functions as func
except ModuleNotFoundError:  # pragma: no cover - local tests do not require Azure Functions runtime.
    if os.environ.get("FUNCTIONS_WORKER_RUNTIME"):
        raise
    func = None

try:
    from azure.monitor.opentelemetry import configure_azure_monitor
except ModuleNotFoundError:  # pragma: no cover - local tests do not require Azure Monitor runtime.
    configure_azure_monitor = None

if configure_azure_monitor is not None and os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    try:
        configure_azure_monitor()
    except Exception:
        # Telemetry must never prevent the Functions worker from indexing routes.
        pass


def _payload(req: Any) -> dict[str, Any]:
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


def _payload_with_headers(req: Any) -> dict[str, Any]:
    payload = _payload(req)
    payload["headers"] = dict(getattr(req, "headers", {}) or {})
    return payload


def _response(payload: dict[str, Any], status_code: int = 200) -> Any:
    if func is None:
        return payload
    return func.HttpResponse(
        json.dumps(payload, indent=2),
        status_code=status_code,
        mimetype="application/json",
    )


def _shared_key_response(req: Any) -> Any | None:
    expected = os.environ.get("ORDER_PROCESSOR_FUNCTION_SHARED_KEY", "")
    if not expected:
        return None

    headers = getattr(req, "headers", {}) or {}
    supplied = (
        headers.get("x-order-processor-function-key")
        or headers.get("X-Order-Processor-Function-Key")
    )
    if supplied and hmac.compare_digest(str(supplied), expected):
        return None
    return _response({"error": "Unauthorized"}, status_code=401)


def _handle(req: Any, callback: Any) -> Any:
    unauthorized = _shared_key_response(req)
    if unauthorized is not None:
        return unauthorized
    try:
        return _response(callback())
    except Exception as exc:
        if os.environ.get("ORDER_PROCESSOR_DEBUG_ERRORS", "").lower() == "true":
            return _response(
                {
                    "error": "InternalServerError",
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                status_code=500,
            )
        raise


if func is not None:
    app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

    @app.route(route="emails/ingest", methods=["POST"])
    def emails_ingest(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.ingest_email(_payload_with_headers(req)))

    @app.route(route="orders/{orderRunId}/process", methods=["POST"])
    def orders_process(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.process_order(req.route_params["orderRunId"], _payload_with_headers(req)))

    @app.route(route="customers/identify", methods=["POST"])
    def customers_identify(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.identify_customer(_payload_with_headers(req)))

    @app.route(route="items/validate", methods=["POST"])
    def items_validate(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.validate_item(_payload_with_headers(req)))

    @app.route(route="imports/customers", methods=["POST"])
    def imports_customers(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.import_customers(_payload_with_headers(req)))

    @app.route(route="imports/items", methods=["POST"])
    def imports_items(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.import_items(_payload_with_headers(req)))

    @app.route(route="mailboxes", methods=["POST"])
    def mailboxes_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.upsert_mailbox(_payload_with_headers(req)))

    @app.route(route="mailboxes/{id}/test-connection", methods=["POST"])
    def mailboxes_test_connection(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.test_mailbox_connection(req.route_params["id"], _payload_with_headers(req)))

    @app.route(route="orders/{orderRunId}/timeline", methods=["POST"])
    def orders_timeline(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(
            req,
            lambda: order_api.order_observability_timeline(req.route_params["orderRunId"], _payload_with_headers(req)),
        )

    @app.route(route="console/session", methods=["POST"])
    def console_session(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_session(_payload_with_headers(req)))

    @app.route(route="console/dashboard", methods=["POST"])
    def console_dashboard(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_dashboard(_payload_with_headers(req)))

    @app.route(route="console/artifacts/download", methods=["POST"])
    def console_artifacts_download(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_output_artifact(_payload_with_headers(req)))

    @app.route(route="console/routing-rules", methods=["POST"])
    def console_routing_rules_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_upsert_routing_rule(_payload_with_headers(req)))

    @app.route(route="console/customers", methods=["POST"])
    def console_customers_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_upsert_customer_config(_payload_with_headers(req)))

    @app.route(route="console/mailboxes", methods=["POST"])
    def console_mailboxes_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_upsert_mailbox(_payload_with_headers(req)))

    @app.route(route="console/mailboxes/{id}/test-connection", methods=["POST"])
    def console_mailboxes_test_connection(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_test_mailbox_connection(req.route_params["id"], _payload_with_headers(req)))

    @app.route(route="console/microsoft-auth/start", methods=["POST"])
    def console_microsoft_auth_start(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_start_microsoft_auth(_payload_with_headers(req)))

    @app.route(route="console/microsoft-auth/callback", methods=["POST"])
    def console_microsoft_auth_callback(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_complete_microsoft_auth(_payload_with_headers(req)))

    @app.route(route="console/tenants", methods=["POST"])
    def console_tenants_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_upsert_tenant_config(_payload_with_headers(req)))

    @app.route(route="console/customer-identification-rules", methods=["POST"])
    def console_customer_identification_rules_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_upsert_customer_identification_rule(_payload_with_headers(req)))

    @app.route(route="console/processor-profiles", methods=["POST"])
    def console_processor_profiles_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_upsert_processor_profile(_payload_with_headers(req)))

    @app.route(route="console/output-profiles", methods=["POST"])
    def console_output_profiles_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_upsert_output_profile(_payload_with_headers(req)))

    @app.route(route="console/users", methods=["POST"])
    def console_users_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_upsert_console_user(_payload_with_headers(req)))

    @app.route(route="console/customers/{customerId}/users", methods=["POST"])
    def console_customer_users_assign(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(
            req,
            lambda: order_api.console_assign_customer_user(req.route_params["customerId"], _payload_with_headers(req)),
        )

    @app.route(route="console/exceptions/{id}/resolve", methods=["POST"])
    def console_exceptions_resolve(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_resolve_exception(req.route_params["id"], _payload_with_headers(req)))

    @app.route(route="console/orders/{orderRunId}/reprocess", methods=["POST"])
    def console_orders_reprocess(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(
            req,
            lambda: order_api.console_reprocess_order(req.route_params["orderRunId"], _payload_with_headers(req)),
        )

    @app.route(route="console/orders/{orderRunId}/timeline", methods=["POST"])
    def console_orders_timeline(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(
            req,
            lambda: order_api.console_order_timeline(req.route_params["orderRunId"], _payload_with_headers(req)),
        )

    @app.route(route="customers/{customerId}/users", methods=["POST"])
    def customer_users_assign(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.assign_customer_user(req.route_params["customerId"], _payload_with_headers(req)))

    @app.route(route="exceptions/{id}/resolve", methods=["POST"])
    def exceptions_resolve(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.resolve_exception(req.route_params["id"], _payload_with_headers(req)))

    @app.route(route="orders/{orderRunId}/reprocess", methods=["POST"])
    def orders_reprocess(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.reprocess_order(req.route_params["orderRunId"], _payload_with_headers(req)))
else:
    app = None
