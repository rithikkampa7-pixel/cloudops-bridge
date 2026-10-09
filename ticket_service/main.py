"""Demo Ticket Service: a fictional ticketing API used to simulate incidents.

Run:  uvicorn ticket_service.main:app --port 8080
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ticket_service import metrics
from ticket_service.inventory import (
    EVENT_NAME,
    TICKET_PRICE,
    SoldOutError,
    inventory,
)

logger = logging.getLogger("ticket_service")

app = FastAPI(title="Demo Ticket Service", version="0.1.0")

MAX_TICKETS_PER_ORDER = 10


class PurchaseRequest(BaseModel):
    quantity: int = Field(..., ge=1, le=MAX_TICKETS_PER_ORDER)


@app.middleware("http")
async def count_requests(request: Request, call_next):
    """Record every request for /metrics. Uses the route template (e.g.
    /tickets/purchase) so unknown URLs don't create unbounded label values."""
    try:
        response = await call_next(request)
    except Exception:
        # Log the full error server-side; never send the stack trace to the client.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    metrics.record_request(request.method, path, response.status_code)
    return response


@app.get("/health")
def health():
    """Liveness: is the process up and able to respond?"""
    return {"status": "healthy"}


@app.get("/ready")
def ready():
    """Readiness: can this instance serve traffic? The only dependency is
    the in-memory inventory, which is always loaded."""
    return {"status": "ready"}


@app.get("/tickets")
def get_tickets():
    return {
        "event": EVENT_NAME,
        "price": TICKET_PRICE,
        "available": inventory.available,
    }


@app.post("/tickets/purchase", status_code=201)
def purchase_tickets(order: PurchaseRequest):
    try:
        remaining = inventory.purchase(order.quantity)
    except SoldOutError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "status": "confirmed",
        "event": EVENT_NAME,
        "quantity": order.quantity,
        "total_price": round(order.quantity * TICKET_PRICE, 2),
        "remaining": remaining,
    }


@app.get("/metrics", response_class=PlainTextResponse)
def get_metrics():
    return PlainTextResponse(
        metrics.render(inventory.available, inventory.sold),
        media_type="text/plain; version=0.0.4",
    )
