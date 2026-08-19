"""
The API Lovable talks to. Deliberately small surface area:

  POST /links              create a short link
  GET  /r/{code}           public redirect (this is what stickers/short
                            links actually point at)
  POST /qr                 generate a QR PNG for a short link code
  GET  /payouts/status/{restaurant_id}   read-only payout status for
                            the restaurant dashboard - never triggers
                            a payout
  POST /webhooks/paystack   Paystack's webhook receiver

Lovable never generates QR codes or short links itself, and never
triggers a payout - all three only ever happen through this service.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.pocketbase_client import PocketBaseClient
from app.paystack_client import PaystackClient
from app.services.email_service import EmailService
from app.services.link_logic import LinkCreationRequest, LinkType
from app.services.link_service import LinkService, LinkServiceError
from app.services.qr_service import generate_qr_png

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("munchmap.api")

app = FastAPI(title="Munchmap Automation", version="1.0.0")

settings = get_settings()
_pb = PocketBaseClient(settings)
_paystack = PaystackClient(settings)
_email = EmailService(settings)
_links = LinkService(settings, _pb)


@app.on_event("shutdown")
async def shutdown() -> None:
    await _pb.close()
    await _paystack.close()
    await _email.close()


# ---- short links ----

@app.post("/links")
async def create_link(payload: dict) -> dict:
    """
    Expected payload:
    {
        "link_type": "restaurant_ordering" | "order_collection",
        "target_url": "https://...",
        "restaurant_id": "..."   (required if link_type is restaurant_ordering)
        "order_id": "..."        (required if link_type is order_collection)
    }
    """
    try:
        link_type = LinkType(payload["link_type"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="invalid or missing link_type")

    request = LinkCreationRequest(
        link_type=link_type,
        target_url=payload.get("target_url", ""),
        restaurant_id=payload.get("restaurant_id"),
        order_id=payload.get("order_id"),
    )

    try:
        result = await _links.create_short_link(request)
    except LinkServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result


@app.get("/r/{code}")
async def resolve_link(code: str):
    """Public redirect endpoint - this is the actual URL printed on
    stickers, table cards, and encoded into QR codes."""
    target_url = await _links.resolve(code)
    if target_url is None:
        raise HTTPException(status_code=404, detail="link not found")
    return RedirectResponse(url=target_url, status_code=302)


# ---- QR codes ----

@app.post("/qr")
async def create_qr(payload: dict) -> Response:
    """
    Expected payload: {"data": "https://mm.synkra.co.za/r/a7k2m9"}

    Returns the PNG image directly. If the caller wants this persisted
    to the qr_codes collection with a stable URL rather than regenerated
    on every request, that's a small addition here once the main build's
    file-storage approach for PocketBase is confirmed - deliberately not
    guessing at that yet.
    """
    data = payload.get("data")
    if not data:
        raise HTTPException(status_code=400, detail="missing 'data' field")

    png_bytes = generate_qr_png(data)
    return Response(content=png_bytes, media_type="image/png")


# ---- payout status (read-only) ----

@app.get("/payouts/status/{restaurant_id}")
async def payout_status(restaurant_id: str) -> dict:
    """
    Read-only. Powers the restaurant dashboard's pending/available/next-
    payout display. This endpoint never initiates anything - the only
    thing that ever triggers a real payout is the scheduled job in
    scripts/run_payout_job.py.
    """
    restaurant = await _pb.get_record("restaurants", restaurant_id)
    recent_payouts = await _pb.list_records(
        "payouts",
        filter_query=f'restaurant = "{restaurant_id}"',
        per_page=10,
    )
    return {
        "available_balance_cents": restaurant.get("wallet_available_balance_cents", 0),
        "pending_balance_cents": restaurant.get("wallet_pending_balance_cents", 0),
        "payout_status": restaurant.get("payout_status", "active"),
        "recent_payouts": recent_payouts,
    }


# ---- Paystack webhook ----

@app.post("/webhooks/paystack")
async def paystack_webhook(request: Request) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    if not _paystack.verify_webhook_signature(raw_body, signature):
        logger.warning("Rejected Paystack webhook with invalid signature.")
        raise HTTPException(status_code=401, detail="invalid signature")

    event = await request.json()
    event_type = event.get("event")
    logger.info("Received verified Paystack webhook: %s", event_type)

    # Transfer success/failure events reconcile against payouts already
    # recorded by the scheduled job - they do not create new payout
    # attempts. This is a safety net for cases where our own polling
    # (verify_transfer) hasn't caught a status change yet, not the
    # primary mechanism.
    if event_type in ("transfer.success", "transfer.failed", "transfer.reversed"):
        reference = event.get("data", {}).get("reference")
        if reference:
            matches = await _pb.list_records(
                "payouts", filter_query=f'idempotency_key = "{reference}"', per_page=1
            )
            if matches:
                new_status = {
                    "transfer.success": "completed",
                    "transfer.failed": "failed",
                    "transfer.reversed": "failed",
                }[event_type]
                await _pb.update_record("payouts", matches[0]["id"], {"status": new_status})

    return {"received": True}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
