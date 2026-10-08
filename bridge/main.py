"""CloudOps Bridge: enriches raw alerts with operational context.

Run:  uvicorn bridge.main:app --port 8081
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from bridge.catalog import catalog_dir, load_catalog
from bridge.models import EnrichedIncident, IncidentRequest

logger = logging.getLogger("bridge")


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
    entry = request.app.state.catalog.get(incident.service)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{incident.service}' not found in service catalog",
        )

    if incident.environment not in entry.environments:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Environment '{incident.environment}' is not defined for "
                f"'{entry.service}'. Known: {entry.environments}"
            ),
        )

    alert = entry.alerts.get(incident.alert)
    if alert is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Alert '{incident.alert}' is not defined for '{entry.service}'. "
                f"Known: {sorted(entry.alerts)}"
            ),
        )

    checks = list(alert.suggested_checks)
    if alert.runbook is None:
        checks.append(
            "No runbook exists for this alert: document the resolution "
            "in runbooks/ afterward"
        )

    return EnrichedIncident(
        service=entry.service,
        environment=incident.environment,
        alert=incident.alert,
        summary=alert.summary,
        # Fall back to "warning" if the catalog doesn't list a severity for this environment.
        severity=alert.severity.get(incident.environment, "warning"),
        first_responder=alert.first_responder,
        application_owner=entry.owners.application,
        cloudops_owner=entry.owners.cloudops,
        dependencies=entry.dependencies,
        health_endpoint=entry.endpoints.health,
        readiness_endpoint=entry.endpoints.readiness,
        runbook=alert.runbook,
        suggested_checks=checks,
    )
