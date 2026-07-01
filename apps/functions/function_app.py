from __future__ import annotations

import json
import hmac
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import parse

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

ITEM_IMPORT_TYPE = "items"
DEFAULT_ITEM_IMPORT_JOB_CHUNK_SIZE = 50
ORDER_REPROCESS_QUEUE_NAME = os.environ.get("ORDER_PROCESSOR_REPROCESS_JOB_QUEUE", "order-reprocess-jobs")

for logger_name in ("azure", "azure.core.pipeline.policies.http_logging_policy"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

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


def _payload_with_headers_and_query(req: Any) -> dict[str, Any]:
    payload = _payload_with_headers(req)
    payload["queryParams"] = dict(getattr(req, "params", {}) or {})
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
        logging.exception("Unhandled function request error: %s", type(exc).__name__)
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


def _plain_text_response(value: str, status_code: int = 200) -> Any:
    if func is None:
        return value
    return func.HttpResponse(value, status_code=status_code, mimetype="text/plain")


def _import_response_mode(req: Any, payload: dict[str, Any]) -> str:
    params = dict(getattr(req, "params", {}) or {})
    headers = dict(getattr(req, "headers", {}) or {})
    return str(
        payload.get("responseMode")
        or payload.get("response_mode")
        or params.get("responseMode")
        or params.get("response_mode")
        or headers.get("x-order-processor-response-mode")
        or headers.get("X-Order-Processor-Response-Mode")
        or "queued"
    ).strip().lower()


def _import_job_chunk_size(import_type: str) -> int:
    if import_type != ITEM_IMPORT_TYPE:
        return 0
    value = (
        os.environ.get("ORDER_PROCESSOR_ITEM_IMPORT_JOB_CHUNK_SIZE")
        or os.environ.get("ORDER_PROCESSOR_IMPORT_JOB_CHUNK_SIZE")
        or str(DEFAULT_ITEM_IMPORT_JOB_CHUNK_SIZE)
    )
    try:
        return max(0, int(str(value).strip()))
    except ValueError:
        return DEFAULT_ITEM_IMPORT_JOB_CHUNK_SIZE


def _chunk_import_payloads(import_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    chunk_size = _import_job_chunk_size(import_type)
    rows = payload.get("rows", [])
    if (
        chunk_size <= 0
        or not isinstance(rows, list)
        or len(rows) <= chunk_size
        or isinstance(payload.get("importChunk"), dict)
    ):
        return [payload]

    batch_id = uuid.uuid4().hex
    total_rows = len(rows)
    chunk_count = (total_rows + chunk_size - 1) // chunk_size
    chunks: list[dict[str, Any]] = []
    for chunk_index, start in enumerate(range(0, total_rows, chunk_size), start=1):
        chunk_rows = rows[start : start + chunk_size]
        source_metadata = dict(payload.get("sourceMetadata") or payload.get("source_metadata") or {})
        chunk_details = {
            "batchId": batch_id,
            "chunkIndex": chunk_index,
            "chunkCount": chunk_count,
            "rowStart": start,
            "rowEndExclusive": start + len(chunk_rows),
            "rowCount": len(chunk_rows),
            "totalRows": total_rows,
        }
        source_metadata["importChunk"] = chunk_details
        chunks.append(
            {
                **payload,
                "rows": chunk_rows,
                "sourceMetadata": source_metadata,
                "importBatchId": batch_id,
                "importChunk": chunk_details,
            }
        )
    return chunks


def _import_blob_service_client() -> Any:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account_url = (
        os.environ.get("IMPORTS_STORAGE_ACCOUNT_URL")
        or os.environ.get("BLOB_SERVICE_ENDPOINT")
        or os.environ.get("SOURCE_ROWS_STORAGE_ACCOUNT_URL")
        or ""
    ).strip()
    if not account_url:
        account_name = os.environ.get("STORAGE_ACCOUNT_NAME", "").strip()
        if account_name:
            account_url = f"https://{account_name}.blob.core.windows.net"
    if not account_url:
        raise ValueError("IMPORTS_STORAGE_ACCOUNT_URL, BLOB_SERVICE_ENDPOINT, or STORAGE_ACCOUNT_NAME is required.")
    return BlobServiceClient(account_url.rstrip("/"), credential=DefaultAzureCredential())


def _queue_account_url() -> str:
    account_url = (
        os.environ.get("AzureWebJobsStorage__queueServiceUri")
        or os.environ.get("QUEUE_SERVICE_ENDPOINT")
        or ""
    ).strip()
    if account_url:
        return account_url.rstrip("/")
    account_name = (
        os.environ.get("AzureWebJobsStorage__accountName")
        or os.environ.get("STORAGE_ACCOUNT_NAME")
        or ""
    ).strip()
    if account_name:
        return f"https://{account_name}.queue.core.windows.net"
    raise ValueError("AzureWebJobsStorage__queueServiceUri or AzureWebJobsStorage__accountName is required.")


def _enqueue_storage_queue_message(queue_name: str, message: str) -> None:
    from azure.identity import DefaultAzureCredential
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy

    client = QueueClient(
        account_url=_queue_account_url(),
        queue_name=queue_name,
        credential=DefaultAzureCredential(),
        message_encode_policy=TextBase64EncodePolicy(),
        retry_total=1,
        connection_timeout=2,
        read_timeout=5,
    )
    try:
        client.create_queue(timeout=5)
    except ResourceExistsError:
        pass
    client.send_message(message, timeout=5)


def _store_import_job_payload(import_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    from azure.storage.blob import ContentSettings

    tenant_id = str(payload.get("tenantId") or payload.get("tenant_id") or "default").strip() or "default"
    job_id = uuid.uuid4().hex
    received_at = datetime.now(UTC).isoformat()
    container_name = os.environ.get("IMPORTS_CONTAINER_NAME", "imports")
    blob_name = f"import-jobs/{tenant_id}/{import_type}/{job_id}.json"
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    blob_client = _import_blob_service_client().get_container_client(container_name).get_blob_client(blob_name)
    blob_client.upload_blob(
        content,
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )
    return {
        "jobId": job_id,
        "tenantId": tenant_id,
        "importType": import_type,
        "blobContainerName": container_name,
        "blobName": blob_name,
        "blobUrl": blob_client.url,
        "receivedAt": received_at,
        "contentLength": len(content.encode("utf-8")),
    }


def _load_import_job_payload(job: dict[str, Any]) -> dict[str, Any]:
    container_name = str(job.get("blobContainerName") or os.environ.get("IMPORTS_CONTAINER_NAME", "imports"))
    blob_name = str(job.get("blobName") or "")
    if not blob_name:
        raise ValueError("Import job message did not include blobName.")
    blob_client = _import_blob_service_client().get_container_client(container_name).get_blob_client(blob_name)
    raw = blob_client.download_blob().readall()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Import job payload must be a JSON object.")
    return payload


def _handle_import_request(req: Any, import_type: str, queued: Any, callback: Any) -> Any:
    unauthorized = _shared_key_response(req)
    if unauthorized is not None:
        return unauthorized

    payload = _payload_with_headers_and_query(req)
    response_mode = _import_response_mode(req, payload)
    if response_mode in {"inline", "sync", "synchronous", "wait"}:
        return _handle(req, callback)

    payload_chunks = _chunk_import_payloads(import_type, payload)
    jobs = [_store_import_job_payload(import_type, chunk) for chunk in payload_chunks]
    messages = [json.dumps(job, separators=(",", ":")) for job in jobs]
    queued.set(messages[0] if len(messages) == 1 else messages)
    first_job = jobs[0]
    return _response(
        {
            "accepted": True,
            "queued": True,
            "status": "queued",
            "importType": import_type,
            "tenantId": first_job["tenantId"],
            "jobId": first_job["jobId"],
            "jobIds": [job["jobId"] for job in jobs],
            "chunked": len(jobs) > 1,
            "chunkCount": len(jobs),
            "chunkSize": _import_job_chunk_size(import_type) if len(jobs) > 1 else 0,
            "receivedAt": first_job["receivedAt"],
            "message": "Import payload was accepted and queued for background processing.",
        },
        status_code=202,
    )


def _handle_console_exception_resolution(req: Any, exception_id: str) -> Any:
    unauthorized = _shared_key_response(req)
    if unauthorized is not None:
        return unauthorized

    payload = _payload_with_headers(req)
    prepared = order_api.console_prepare_async_exception_resolution(exception_id, payload)
    if prepared.get("error") or not prepared.get("queued"):
        return _response(order_api.console_resolve_exception(exception_id, payload))

    job = {
        "kind": "consoleExceptionResolution",
        "exceptionId": exception_id,
        "payload": payload,
        "acceptedAt": datetime.now(UTC).isoformat(),
    }
    _enqueue_storage_queue_message(ORDER_REPROCESS_QUEUE_NAME, json.dumps(job, separators=(",", ":")))
    return _response(
        {
            **prepared,
            "accepted": True,
            "queued": True,
            "status": "queued",
            "queueName": ORDER_REPROCESS_QUEUE_NAME,
            "message": "Exception resolution was accepted and queued for background processing.",
        },
        status_code=202,
    )


if func is not None:
    app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

    @app.route(route="emails/ingest", methods=["POST"])
    def emails_ingest(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.ingest_email(_payload_with_headers(req)))

    @app.route(route="orders/{orderRunId}/process", methods=["POST"])
    def orders_process(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.process_order(req.route_params["orderRunId"], _payload_with_headers(req)))

    @app.route(route="normalize-spreadsheet", methods=["POST"])
    def normalize_spreadsheet(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.normalize_spreadsheet(_payload_with_headers(req)))

    @app.route(route="extract-order-lines", methods=["POST"])
    def extract_order_lines(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.extract_spreadsheet_order_lines(_payload_with_headers(req)))

    @app.route(route="extract-email-body-order", methods=["POST"])
    def extract_email_body_order(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.extract_email_body_order(_payload_with_headers(req)))

    @app.route(route="extract-google-document-ai-order", methods=["POST"])
    def extract_google_document_ai_order(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.extract_google_document_ai_order(_payload_with_headers(req)))

    @app.route(route="customers/identify", methods=["POST"])
    def customers_identify(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.identify_customer(_payload_with_headers(req)))

    @app.route(route="items/validate", methods=["POST"])
    def items_validate(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.validate_item(_payload_with_headers(req)))

    @app.route(route="imports/customers", methods=["POST"])
    @app.queue_output(
        arg_name="queued",
        queue_name="%ORDER_PROCESSOR_IMPORT_JOB_QUEUE%",
        connection="AzureWebJobsStorage",
    )
    def imports_customers(req: func.HttpRequest, queued: func.Out[str]) -> func.HttpResponse:
        return _handle_import_request(
            req,
            "customers",
            queued,
            lambda: order_api.import_customers(_payload_with_headers(req)),
        )

    @app.route(route="imports/items", methods=["POST"])
    @app.queue_output(
        arg_name="queued",
        queue_name="%ORDER_PROCESSOR_IMPORT_JOB_QUEUE%",
        connection="AzureWebJobsStorage",
    )
    def imports_items(req: func.HttpRequest, queued: func.Out[str]) -> func.HttpResponse:
        return _handle_import_request(
            req,
            "items",
            queued,
            lambda: order_api.import_items(_payload_with_headers(req)),
        )

    @app.route(route="mailboxes", methods=["POST"])
    def mailboxes_upsert(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.upsert_mailbox(_payload_with_headers(req)))

    @app.route(route="mailboxes/{id}/test-connection", methods=["POST"])
    def mailboxes_test_connection(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.test_mailbox_connection(req.route_params["id"], _payload_with_headers(req)))

    @app.route(route="mailboxes/poll", methods=["POST"])
    def mailboxes_poll(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.poll_mailboxes(_payload_with_headers(req)))

    @app.route(route="mailboxes/subscriptions/sync", methods=["POST"])
    def mailboxes_subscriptions_sync(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.sync_mailbox_subscriptions(_payload_with_headers(req)))

    @app.route(route="mailboxes/categories/sync", methods=["POST"])
    def mailboxes_categories_sync(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.sync_mailbox_categories(_payload_with_headers(req)))

    @app.route(route="graph/notifications", methods=["POST"])
    def graph_notifications(req: func.HttpRequest) -> func.HttpResponse:
        params = dict(getattr(req, "params", {}) or {})
        validation_token = params.get("validationToken") or params.get("validationtoken")
        if validation_token:
            return _plain_text_response(parse.unquote(str(validation_token)))

        payload = _payload_with_headers_and_query(req)
        notifications = payload.get("value", [])
        queue_name = os.environ.get("ORDER_PROCESSOR_GRAPH_NOTIFICATION_QUEUE", "graph-mailbox-notifications")
        _enqueue_storage_queue_message(
            queue_name,
            json.dumps(
                {
                    "source": "microsoftGraphWebhook",
                    "receivedAt": datetime.now(UTC).isoformat(),
                    "notifications": notifications if isinstance(notifications, list) else [],
                    "headers": payload.get("headers", {}),
                    "queryParams": payload.get("queryParams", {}),
                },
                separators=(",", ":"),
            ),
        )
        return _response(
            {"accepted": len(notifications) if isinstance(notifications, list) else 0, "queued": True},
            status_code=202,
        )

    @app.queue_trigger(
        arg_name="msg",
        queue_name="%ORDER_PROCESSOR_GRAPH_NOTIFICATION_QUEUE%",
        connection="AzureWebJobsStorage",
    )
    def graph_notifications_queue(msg: func.QueueMessage) -> None:
        payload = json.loads(msg.get_body().decode("utf-8"))
        order_api.process_graph_notifications(payload)

    @app.queue_trigger(
        arg_name="msg",
        queue_name="%ORDER_PROCESSOR_IMPORT_JOB_QUEUE%",
        connection="AzureWebJobsStorage",
    )
    def import_jobs_queue(msg: func.QueueMessage) -> None:
        job = json.loads(msg.get_body().decode("utf-8"))
        payload = _load_import_job_payload(job)
        import_type = str(job.get("importType") or "").strip().lower()
        if import_type == "customers":
            order_api.import_customers(payload)
            return
        if import_type == "items":
            order_api.import_items(payload)
            return
        raise ValueError(f"Unknown import job type: {import_type}")

    @app.queue_trigger(
        arg_name="msg",
        queue_name=ORDER_REPROCESS_QUEUE_NAME,
        connection="AzureWebJobsStorage",
    )
    def order_reprocess_jobs_queue(msg: func.QueueMessage) -> None:
        job = json.loads(msg.get_body().decode("utf-8"))
        kind = str(job.get("kind") or "").strip()
        if kind == "consoleExceptionResolution":
            exception_id = str(job.get("exceptionId") or "").strip()
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if not exception_id:
                raise ValueError("Queued exception resolution did not include exceptionId.")
            order_api.resolve_exception(exception_id, payload)
            return
        raise ValueError(f"Unknown order reprocess job type: {kind}")

    @app.timer_trigger(
        arg_name="timer",
        schedule="%ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_RENEWAL_CRON%",
        run_on_startup=False,
        use_monitor=True,
    )
    def graph_subscription_renewal_timer(timer: func.TimerRequest) -> None:
        order_api.renew_mailbox_subscriptions({"source": "timer", "pastDue": bool(getattr(timer, "past_due", False))})

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

    @app.route(route="console/data/{section}", methods=["POST"])
    def console_data(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.console_data(req.route_params["section"], _payload_with_headers(req)))

    @app.route(route="costs/events", methods=["POST"])
    def costs_events(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.record_ai_cost_event(_payload_with_headers(req)))

    @app.route(route="costs/summary", methods=["POST"])
    def costs_summary(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(req, lambda: order_api.cost_summary(_payload_with_headers(req)))

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
        return _handle_console_exception_resolution(req, req.route_params["id"])

    @app.route(route="console/monitor/active/{id}/clear", methods=["POST"])
    def console_monitor_active_clear(req: func.HttpRequest) -> func.HttpResponse:
        return _handle(
            req,
            lambda: order_api.console_clear_active_processing_run(req.route_params["id"], _payload_with_headers(req)),
        )

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
