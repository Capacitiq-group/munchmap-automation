import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date
from app.services.payout_logic import (
    PayoutTrigger,
    RestaurantBalance,
    target_payout_date,
    build_idempotency_key,
    decide_payout,
    decide_payouts_for_run,
    MINIMUM_PAYOUT_CENTS,
)


# ---- target_payout_date ----

def test_monday_trigger_targets_next_day_tuesday():
    monday = date(2026, 8, 24)  # confirmed Monday
    assert monday.weekday() == 0
    result = target_payout_date(PayoutTrigger.MONDAY, monday)
    assert result == date(2026, 8, 25)  # Tuesday
    assert result.weekday() == 1


def test_wednesday_trigger_targets_friday_two_days_later():
    wednesday = date(2026, 8, 26)
    assert wednesday.weekday() == 2
    result = target_payout_date(PayoutTrigger.WEDNESDAY, wednesday)
    assert result == date(2026, 8, 28)  # Friday
    assert result.weekday() == 4


def test_monday_trigger_on_wrong_weekday_raises():
    not_a_monday = date(2026, 8, 25)  # Tuesday
    try:
        target_payout_date(PayoutTrigger.MONDAY, not_a_monday)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not a monday" in str(e).lower()


def test_wednesday_trigger_on_wrong_weekday_raises():
    not_a_wednesday = date(2026, 8, 27)  # Thursday
    try:
        target_payout_date(PayoutTrigger.WEDNESDAY, not_a_wednesday)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_target_date_across_month_boundary():
    # Monday 31 Aug 2026 -> Tuesday 1 Sep 2026
    monday = date(2026, 8, 31)
    assert monday.weekday() == 0
    result = target_payout_date(PayoutTrigger.MONDAY, monday)
    assert result == date(2026, 9, 1)


# ---- build_idempotency_key ----

def test_idempotency_key_format():
    key = build_idempotency_key("rest_abc123", date(2026, 8, 25))
    assert key == "rest_abc123:2026-08-25"


def test_idempotency_key_is_stable_for_same_inputs():
    k1 = build_idempotency_key("rest_abc123", date(2026, 8, 25))
    k2 = build_idempotency_key("rest_abc123", date(2026, 8, 25))
    assert k1 == k2


def test_idempotency_key_differs_for_different_restaurants():
    k1 = build_idempotency_key("rest_aaa", date(2026, 8, 25))
    k2 = build_idempotency_key("rest_bbb", date(2026, 8, 25))
    assert k1 != k2


def test_idempotency_key_differs_for_different_dates():
    k1 = build_idempotency_key("rest_aaa", date(2026, 8, 25))
    k2 = build_idempotency_key("rest_aaa", date(2026, 8, 28))
    assert k1 != k2


# ---- decide_payout ----

MONDAY = date(2026, 8, 24)


def _healthy_balance(**overrides):
    defaults = dict(
        restaurant_id="rest_1",
        paystack_subaccount_code="ACCT_xxx",
        paystack_recipient_code="RCP_xxx",
        available_balance_cents=50_000,  # R500.00
        payout_status="active",
    )
    defaults.update(overrides)
    return RestaurantBalance(**defaults)


def test_healthy_restaurant_gets_paid():
    decision = decide_payout(_healthy_balance(), PayoutTrigger.MONDAY, MONDAY)
    assert decision.should_pay is True
    assert decision.amount_cents == 50_000
    assert decision.skip_reason is None
    assert decision.cycle_date == date(2026, 8, 25)


def test_held_restaurant_is_skipped():
    balance = _healthy_balance(payout_status="held")
    decision = decide_payout(balance, PayoutTrigger.MONDAY, MONDAY)
    assert decision.should_pay is False
    assert decision.skip_reason == "payout_held"
    # amount is zeroed out even though there was a real balance -
    # held restaurants must never have money moved
    assert decision.amount_cents == 0


def test_restaurant_without_subaccount_is_skipped():
    balance = _healthy_balance(paystack_subaccount_code=None)
    decision = decide_payout(balance, PayoutTrigger.MONDAY, MONDAY)
    assert decision.should_pay is False
    assert decision.skip_reason == "no_paystack_subaccount"


def test_restaurant_below_minimum_balance_is_skipped():
    balance = _healthy_balance(available_balance_cents=MINIMUM_PAYOUT_CENTS - 1)
    decision = decide_payout(balance, PayoutTrigger.MONDAY, MONDAY)
    assert decision.should_pay is False
    assert decision.skip_reason == "below_minimum_balance"


def test_restaurant_at_exactly_minimum_balance_is_paid():
    balance = _healthy_balance(available_balance_cents=MINIMUM_PAYOUT_CENTS)
    decision = decide_payout(balance, PayoutTrigger.MONDAY, MONDAY)
    assert decision.should_pay is True
    assert decision.amount_cents == MINIMUM_PAYOUT_CENTS


def test_zero_balance_restaurant_is_skipped():
    balance = _healthy_balance(available_balance_cents=0)
    decision = decide_payout(balance, PayoutTrigger.MONDAY, MONDAY)
    assert decision.should_pay is False
    assert decision.skip_reason == "below_minimum_balance"


def test_negative_balance_is_skipped_not_paid_and_not_crashed():
    # Defensive: this should never happen upstream, but the payout job
    # must not blow up or, worse, attempt a negative transfer if it does.
    balance = _healthy_balance(available_balance_cents=-500)
    decision = decide_payout(balance, PayoutTrigger.MONDAY, MONDAY)
    assert decision.should_pay is False
    assert decision.amount_cents == 0


def test_decision_always_carries_correct_idempotency_key():
    decision = decide_payout(_healthy_balance(), PayoutTrigger.MONDAY, MONDAY)
    assert decision.idempotency_key == "rest_1:2026-08-25"


def test_held_status_takes_priority_over_missing_subaccount():
    # If a restaurant is both held AND missing a subaccount, the skip
    # reason should be deterministic (held checked first) so operational
    # logs are consistent and not order-dependent by accident.
    balance = _healthy_balance(payout_status="held", paystack_subaccount_code=None)
    decision = decide_payout(balance, PayoutTrigger.MONDAY, MONDAY)
    assert decision.skip_reason == "payout_held"


# ---- decide_payouts_for_run (batch) ----

def test_batch_decision_processes_all_restaurants_independently():
    balances = [
        _healthy_balance(restaurant_id="rest_1"),
        _healthy_balance(restaurant_id="rest_2", payout_status="held"),
        _healthy_balance(restaurant_id="rest_3", available_balance_cents=0),
    ]
    decisions = decide_payouts_for_run(balances, PayoutTrigger.MONDAY, MONDAY)
    assert len(decisions) == 3
    by_id = {d.restaurant_id: d for d in decisions}
    assert by_id["rest_1"].should_pay is True
    assert by_id["rest_2"].should_pay is False
    assert by_id["rest_2"].skip_reason == "payout_held"
    assert by_id["rest_3"].should_pay is False
    assert by_id["rest_3"].skip_reason == "below_minimum_balance"


def test_batch_decision_empty_list_returns_empty_list():
    assert decide_payouts_for_run([], PayoutTrigger.MONDAY, MONDAY) == []


def test_re_running_same_trigger_produces_identical_idempotency_keys():
    # This is the property that makes the job safe to re-run: calling it
    # twice for the same trigger date must produce the same keys, so
    # PocketBase's unique constraint on idempotency_key rejects the
    # second attempt before any transfer is requested.
    balances = [_healthy_balance(restaurant_id="rest_1")]
    first_run = decide_payouts_for_run(balances, PayoutTrigger.MONDAY, MONDAY)
    second_run = decide_payouts_for_run(balances, PayoutTrigger.MONDAY, MONDAY)
    assert first_run[0].idempotency_key == second_run[0].idempotency_key


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
