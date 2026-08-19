"""
Payout scheduling logic. This module contains ONLY pure functions - no
network calls, no PocketBase, no Paystack. Everything here can be
unit-tested with plain pytest, no mocking required.

The actual I/O (talking to PocketBase and Paystack) lives in
payout_runner.py, which imports these functions and uses them to decide
what to do.

Why the split matters: the payout job moves real money. The part most
likely to have a subtle, expensive bug is the date/eligibility logic,
not the HTTP calls. Isolating it here means that logic gets tested
directly, with no live credentials and no mocking, so nothing can hide
behind "the mock made it pass."
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


class PayoutTrigger(str, Enum):
    MONDAY = "monday"
    WEDNESDAY = "wednesday"


class PayoutStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class RestaurantBalance:
    """Minimal shape the payout logic needs from a restaurant record.
    Deliberately not the full PocketBase record - keeps this module
    decoupled from PocketBase's schema."""
    restaurant_id: str
    paystack_subaccount_code: str | None
    paystack_recipient_code: str | None
    available_balance_cents: int
    payout_status: str  # "active" | "held"


@dataclass(frozen=True)
class PayoutDecision:
    """What the runner should do for one restaurant on one trigger run."""
    restaurant_id: str
    should_pay: bool
    amount_cents: int
    cycle_date: date
    idempotency_key: str
    skip_reason: str | None = None


def target_payout_date(trigger: PayoutTrigger, trigger_run_date: date) -> date:
    """
    Given which trigger fired and the calendar date it fired on, return the
    payout day it's targeting.

    Monday's run targets the next Tuesday.
    Wednesday's run targets the next Friday.

    This is intentionally explicit rather than "+1 day" / "+2 days" math,
    because the trigger day is guaranteed by the scheduler (cron only ever
    fires this on a Monday or a Wednesday) - encoding the day-of-week
    relationship directly makes the intent unambiguous and makes a
    misconfigured cron entry (e.g. firing on a Tuesday by mistake) loud
    and easy to catch in review rather than silently producing a wrong date.
    """
    if trigger_run_date.weekday() != _WEEKDAY_FOR_TRIGGER[trigger]:
        raise ValueError(
            f"{trigger.value} payout trigger fired on {trigger_run_date.isoformat()}, "
            f"which is not a {trigger.value}. Refusing to guess a target date - "
            f"check the scheduler configuration."
        )

    if trigger is PayoutTrigger.MONDAY:
        return trigger_run_date + timedelta(days=1)  # Tuesday
    if trigger is PayoutTrigger.WEDNESDAY:
        return trigger_run_date + timedelta(days=2)  # Friday
    raise ValueError(f"Unhandled trigger: {trigger}")


_WEEKDAY_FOR_TRIGGER = {
    PayoutTrigger.MONDAY: 0,     # date.weekday(): Monday == 0
    PayoutTrigger.WEDNESDAY: 2,  # Wednesday == 2
}


def build_idempotency_key(restaurant_id: str, cycle_date: date) -> str:
    """
    The key that makes re-running the payout job safe. If the job is
    triggered twice for the same restaurant and the same target cycle
    date (e.g. an accidental duplicate cron fire, or a manual re-run
    after a partial failure), this key is what PocketBase's unique
    constraint on payouts.idempotency_key uses to reject the duplicate
    insert before any transfer is requested from Paystack.
    """
    return f"{restaurant_id}:{cycle_date.isoformat()}"


MINIMUM_PAYOUT_CENTS = 100  # R1.00 - avoid initiating transfers for near-zero amounts


def decide_payout(
    balance: RestaurantBalance,
    trigger: PayoutTrigger,
    trigger_run_date: date,
) -> PayoutDecision:
    """
    The core eligibility decision for a single restaurant on a single
    scheduled run. Pure function: same inputs always produce the same
    decision, nothing here talks to the network.
    """
    cycle_date = target_payout_date(trigger, trigger_run_date)
    idempotency_key = build_idempotency_key(balance.restaurant_id, cycle_date)

    if balance.payout_status == "held":
        return PayoutDecision(
            restaurant_id=balance.restaurant_id,
            should_pay=False,
            amount_cents=0,
            cycle_date=cycle_date,
            idempotency_key=idempotency_key,
            skip_reason="payout_held",
        )

    if not balance.paystack_subaccount_code:
        return PayoutDecision(
            restaurant_id=balance.restaurant_id,
            should_pay=False,
            amount_cents=0,
            cycle_date=cycle_date,
            idempotency_key=idempotency_key,
            skip_reason="no_paystack_subaccount",
        )

    if balance.available_balance_cents < MINIMUM_PAYOUT_CENTS:
        return PayoutDecision(
            restaurant_id=balance.restaurant_id,
            should_pay=False,
            amount_cents=0,
            cycle_date=cycle_date,
            idempotency_key=idempotency_key,
            skip_reason="below_minimum_balance",
        )

    return PayoutDecision(
        restaurant_id=balance.restaurant_id,
        should_pay=True,
        amount_cents=balance.available_balance_cents,
        cycle_date=cycle_date,
        idempotency_key=idempotency_key,
        skip_reason=None,
    )


def decide_payouts_for_run(
    balances: list[RestaurantBalance],
    trigger: PayoutTrigger,
    trigger_run_date: date,
) -> list[PayoutDecision]:
    """Batch version - this is what the runner actually calls."""
    return [decide_payout(b, trigger, trigger_run_date) for b in balances]
