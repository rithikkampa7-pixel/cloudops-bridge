"""CloudOps Bridge: enriches raw alerts with operational context.

Run:  uvicorn bridge.main:app --port 8081
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from bridge.alertmanager import AlertmanagerWebhook, process_alert
from bridge.catalog import LookupFailure, catalog_dir, enrich, load_catalog
from bridge.models import EnrichedIncident, IncidentRequest, WebhookResult

WEBHOOK_PATH = "/webhooks/alertmanager"

# The app's own INFO logs, on stdout, one line each. Uvicorn only configures
# its own loggers, so without this "bridge" logs were never shown.
logger = logging.getLogger("bridge")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def kv(**fields) -> str:
    """key=value pairs for greppable logs; values with spaces are quoted."""
    def fmt(value):
        text = "-" if value is None or value == "" else str(value)
        return f'"{text}"' if " " in text else text
    return " ".join(f"{key}={fmt(value)}" for key, value in fields.items())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the catalog once at startup; the service refuses to start if it's invalid.
    app.state.catalog = load_catalog(catalog_dir())
    logger.info("Loaded service catalog: %s", sorted(app.state.catalog))
    yield


app = FastAPI(title="CloudOps Bridge", version="0.1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Log the full error server-side; never send the stack trace to the client.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Same 422 response as FastAPI's default; additionally make rejected
    # webhooks visible in the logs (Alertmanager does not retry a 4xx).
    if request.url.path == WEBHOOK_PATH:
        logger.warning(kv(event="webhook_rejected", reason="invalid_payload",
                          errors=len(exc.errors())))
    return await request_validation_exception_handler(request, exc)


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/ready")
def ready(request: Request):
    """Readiness: can this instance enrich incidents? True only when the
    service catalog is loaded. Liveness (/health) only says the process is up."""
    catalog = getattr(request.app.state, "catalog", None)
    if not catalog:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "reason": "service catalog not loaded"},
        )
    return {"status": "ready", "services": len(catalog)}


@app.get("/services")
def list_services(request: Request):
    """List the services the Bridge knows about."""
    return {"services": sorted(request.app.state.catalog)}


@app.post("/incidents/enrich", response_model=EnrichedIncident)
def enrich_incident(incident: IncidentRequest, request: Request):
    try:
        return enrich(request.app.state.catalog, incident.service,
                      incident.environment, incident.alert)
    except LookupFailure as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@app.post(WEBHOOK_PATH, response_model=WebhookResult)
def alertmanager_webhook(payload: AlertmanagerWebhook, request: Request):
    """Receive an Alertmanager notification and enrich every alert in it.

    Status policy (Alertmanager retries 5xx, never 4xx):
      200  payload valid; per-alert outcome in the body. Alerts the catalog
           cannot map are reported, not failed: retrying cannot fix a catalog
           gap, and failing the batch would also fail its valid alerts.
      422  malformed payload / unsupported version (retrying cannot help).
      503  catalog not loaded (temporary; Alertmanager retries).
    Nothing is stored: the response and these log lines are the only record.
    """
    catalog = getattr(request.app.state, "catalog", None)
    if not catalog:
        logger.error(kv(event="webhook_unavailable", reason="catalog_not_loaded"))
        return JSONResponse(status_code=503, content={"detail": "service catalog not loaded"})

    logger.info(kv(event="webhook_received", receiver=payload.receiver,
                   group_status=payload.status, alerts=len(payload.alerts),
                   truncated=payload.truncatedAlerts))

    results = [process_alert(alert, catalog) for alert in payload.alerts]
    for r in results:
        log = logger.info if r.outcome == "enriched" else logger.warning
        log(kv(event="alert_processed", status=r.status, outcome=r.outcome,
               alertname=r.alertname, service=r.service, environment=r.environment,
               severity=r.severity_label, pod=r.source.pod, instance=r.source.instance,
               runbook=r.incident.runbook if r.incident else None,
               fingerprint=r.fingerprint, reason=r.reason))
        if r.incident:
            # The operational context returned to Alertmanager (which discards
            # response bodies), so the enrichment itself is visible in the logs.
            i = r.incident
            logger.info(kv(event="incident_context", fingerprint=r.fingerprint,
                           severity=i.severity, first_responder=i.first_responder,
                           application_owner=i.application_owner,
                           cloudops_owner=i.cloudops_owner,
                           dependencies=",".join(i.dependencies), health=i.health_endpoint,
                           readiness=i.readiness_endpoint, runbook=i.runbook,
                           checks=len(i.suggested_checks)))

    count = {o: sum(r.outcome == o for r in results) for o in ("enriched", "unmapped", "rejected")}
    return WebhookResult(receiver=payload.receiver, group_key=payload.groupKey,
                         group_status=payload.status, received=len(results),
                         truncated_alerts=payload.truncatedAlerts, results=results, **count)
