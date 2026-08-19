#!/usr/bin/env python3
"""
Entrypoint for the scheduled payout run. This is what cron (or a GitHub
Actions scheduled workflow, or any other external scheduler) actually
calls - it is NOT meant to run as a long-lived background process.

Usage:
    python scripts/run_payout_job.py

Determines which trigger fired (Monday or Wednesday) from today's actual
weekday - it does not take the trigger as an argument, deliberately, so
a misconfigured cron entry that fires on the wrong day raises a loud
error (see payout_logic.target_payout_date) instead of silently
producing a wrong payout date.

Exit code is 0 only if the run completed without an unhandled exception.
Individual restaurant transfer failures do NOT fail the whole run (they
are recorded as failed payouts and retried next cycle) - only an
infrastructure-level problem (PocketBase unreachable, bad credentials,
wrong weekday) should produce a non-zero exit code, since that's the
signal you'd want your scheduler's alerting to catch.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date

from app.config import get_settings
from app.pocketbase_client import PocketBaseClient
from app.paystack_client import PaystackClient
from app.services.email_service import EmailService
from app.services.payout_logic import PayoutTrigger
from app.services.payout_runner import run_payout_cycle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("munchmap.run_payout_job")

_WEEKDAY_TRIGGER = {
    0: PayoutTrigger.MONDAY,     # Monday
    2: PayoutTrigger.WEDNESDAY,  # Wednesday
}


async def main() -> int:
    today = date.today()
    trigger = _WEEKDAY_TRIGGER.get(today.weekday())

    if trigger is None:
        logger.error(
            "run_payout_job.py was triggered on %s, which is neither a Monday "
            "nor a Wednesday. Refusing to run - check the cron/scheduler "
            "configuration rather than guessing which cycle this should be.",
            today.strftime("%A"),
        )
        return 1

    settings = get_settings()
    pb = PocketBaseClient(settings)
    paystack = PaystackClient(settings)
    email = EmailService(settings)

    try:
        decisions = await run_payout_cycle(trigger, today, pb, paystack, email)
        paid = [d for d in decisions if d.should_pay]
        skipped = [d for d in decisions if not d.should_pay]
        logger.info(
            "Payout run complete. %d paid, %d skipped.",
            len(paid), len(skipped),
        )
        return 0
    except Exception:
        logger.exception("Payout run failed with an unhandled error.")
        return 1
    finally:
        await pb.close()
        await paystack.close()
        await email.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
