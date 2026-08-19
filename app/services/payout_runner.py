"""
I/O layer for the payout job - wraps payout_logic.py's pure decisions
with actual PocketBase reads/writes and Paystack transfer calls.

This is what scripts/run_payout_job.py calls. Kept separate from that
script so it can also be triggered from an authenticated admin API
endpoint later if needed (e.g. a manual "run today's cycle again" button
for Admin, if that's ever wanted) without duplicating this logic.
"""
from __future__ import annotations

import logging
from datetime import date

from app.pocketbase_client import PocketBaseClient
from app.paystack_client import PaystackClient
from app.services.email_service import EmailService, build_payout_completed_email, build_payout_failed_email
from app.services.payout_logic import (
    PayoutTrigger,
    RestaurantBalance,
    PayoutDecision,
    decide_payouts_for_run,
)

logger = logging.getLogger("munchmap.payout_runner")


async def _load_restaurant_balances(pb: PocketBaseClient) -> list[RestaurantBalance]:
    """Pulls every restaurant with a non-zero available balance. Filtering
    at the PocketBase query level (rather than pulling every restaurant
    and filtering in Python) keeps this cheap as the restaurant count grows."""
    records = await pb.list_records(
        "restaurants", filter_query="wallet_available_balance_cents > 0"
    )
    return [
        RestaurantBalance(
            restaurant_id=r["id"],
            paystack_subaccount_code=r.get("paystack_subaccount_code"),
            paystack_recipient_code=r.get("paystack_recipient_code"),
            available_balance_cents=r.get("wallet_available_balance_cents", 0),
            payout_status=r.get("payout_status", "active"),
        )
        for r in records
    ]


async def _execute_decision(
    decision: PayoutDecision,
    pb: PocketBaseClient,
    paystack: PaystackClient,
    email: EmailService,
) -> None:
    if not decision.should_pay:
        logger.info(
            "Skipping restaurant %s for cycle %s: %s",
            decision.restaurant_id, decision.cycle_date, decision.skip_reason,
        )
        return

    # PocketBase's unique constraint on idempotency_key is the real
    # safety net here - if a payout record with this key already exists,
    # this create call fails and we skip cleanly rather than double-paying.
    already_exists = await pb.record_exists(
        "payouts", f'idempotency_key = "{decision.idempotency_key}"'
    )
    if already_exists:
        logger.info(
            "Payout for %s / %s already exists, skipping (idempotency key %s)",
            decision.restaurant_id, decision.cycle_date, decision.idempotency_key,
        )
        return

    payout_record = await pb.create_record(
        "payouts",
        {
            "restaurant": decision.restaurant_id,
            "cycle_date": decision.cycle_date.isoformat(),
            "amount_cents": decision.amount_cents,
            "status": "processing",
            "idempotency_key": decision.idempotency_key,
        },
    )

    restaurant = await pb.get_record("restaurants", decision.restaurant_id)

    try:
        transfer = await paystack.initiate_transfer(
            recipient_code=restaurant["paystack_recipient_code"],
            amount_cents=decision.amount_cents,
            reason=f"Munchmap payout - {decision.cycle_date.isoformat()}",
            reference=decision.idempotency_key,
        )
        await pb.update_record(
            "payouts",
            payout_record["id"],
            {
                "status": "completed",
                "paystack_transfer_code": transfer.get("transfer_code"),
                "completed_at": date.today().isoformat(),
            },
        )
        # Zero out the available balance now that it's been paid out -
        # new orders since the job started accrue fresh, they are not lost.
        await pb.update_record(
            "restaurants",
            decision.restaurant_id,
            {"wallet_available_balance_cents": 0},
        )

        if restaurant.get("email"):
            amount_display = f"R{decision.amount_cents / 100:,.2f}"
            await email.send(
                build_payout_completed_email(
                    to=restaurant["email"],
                    restaurant_name=restaurant.get("name", "there"),
                    amount_display=amount_display,
                    payout_date_display=decision.cycle_date.strftime("%A, %d %B %Y"),
                )
            )

    except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure here
        # must be recorded, not silently swallowed, and must not crash
        # the whole batch run for every other restaurant.
        logger.exception("Transfer failed for restaurant %s", decision.restaurant_id)
        await pb.update_record(
            "payouts",
            payout_record["id"],
            {"status": "failed", "failure_reason": str(exc)},
        )
        if restaurant.get("email"):
            amount_display = f"R{decision.amount_cents / 100:,.2f}"
            await email.send(
                build_payout_failed_email(
                    to=restaurant["email"],
                    restaurant_name=restaurant.get("name", "there"),
                    amount_display=amount_display,
                )
            )


async def run_payout_cycle(
    trigger: PayoutTrigger,
    trigger_run_date: date,
    pb: PocketBaseClient,
    paystack: PaystackClient,
    email: EmailService,
) -> list[PayoutDecision]:
    """
    The full end-to-end run for one scheduled trigger. Returns the list
    of decisions made, so the caller (the script, or a test) can log or
    assert on what happened without re-querying PocketBase.
    """
    balances = await _load_restaurant_balances(pb)
    decisions = decide_payouts_for_run(balances, trigger, trigger_run_date)

    logger.info(
        "Payout cycle %s (%s trigger): %d restaurants considered, %d eligible",
        trigger.value, trigger_run_date, len(decisions),
        sum(1 for d in decisions if d.should_pay),
    )

    for decision in decisions:
        await _execute_decision(decision, pb, paystack, email)

    return decisions
