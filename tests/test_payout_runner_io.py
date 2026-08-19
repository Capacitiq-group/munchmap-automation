"""
These tests mock the HTTP layer with respx, so they check that this
code calls PocketBase and Paystack correctly - not that PocketBase and
Paystack themselves behave a certain way. They need `pip install -r
requirements.txt` to run (fastapi, httpx, respx, pytest, pytest-asyncio),
which this sandbox couldn't do (no network access here) - run these
yourself once the repo is set up locally or in CI.

Expected result: all pass, 0 failures.
"""
import pytest
import respx
import httpx
from datetime import date

from app.config import Settings
from app.pocketbase_client import PocketBaseClient
from app.paystack_client import PaystackClient
from app.services.email_service import EmailService
from app.services.payout_logic import PayoutTrigger
from app.services.payout_runner import run_payout_cycle


def _settings() -> Settings:
    return Settings(
        pocketbase_url="https://pb.example.com",
        pocketbase_admin_email="admin@example.com",
        pocketbase_admin_password="pw",
        paystack_secret_key="sk_test_xxx",
        paystack_webhook_secret="sk_test_xxx",
        resend_api_key="re_xxx",
    )


@pytest.mark.asyncio
@respx.mock
async def test_full_payout_cycle_happy_path():
    settings = _settings()

    respx.post("https://pb.example.com/api/admins/auth-with-password").mock(
        return_value=httpx.Response(200, json={"token": "admin-token"})
    )
    respx.get("https://pb.example.com/api/collections/restaurants/records").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "rest_1",
                        "name": "Test Diner",
                        "email": "owner@testdiner.co.za",
                        "wallet_available_balance_cents": 25000,
                        "payout_status": "active",
                        "paystack_subaccount_code": "ACCT_1",
                        "paystack_recipient_code": "RCP_1",
                    }
                ]
            },
        )
    )
    respx.get("https://pb.example.com/api/collections/payouts/records").mock(
        return_value=httpx.Response(200, json={"items": []})  # no existing payout -> not a duplicate
    )
    create_payout_route = respx.post(
        "https://pb.example.com/api/collections/payouts/records"
    ).mock(
        return_value=httpx.Response(200, json={"id": "payout_1", "status": "processing"})
    )
    respx.get("https://pb.example.com/api/collections/restaurants/records/rest_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "rest_1",
                "name": "Test Diner",
                "email": "owner@testdiner.co.za",
                "paystack_recipient_code": "RCP_1",
            },
        )
    )
    respx.post("https://api.paystack.co/transfer").mock(
        return_value=httpx.Response(
            200,
            json={"status": True, "data": {"transfer_code": "TRF_abc123"}},
        )
    )
    update_payout_route = respx.patch(
        "https://pb.example.com/api/collections/payouts/records/payout_1"
    ).mock(return_value=httpx.Response(200, json={"id": "payout_1", "status": "completed"}))
    respx.patch("https://pb.example.com/api/collections/restaurants/records/rest_1").mock(
        return_value=httpx.Response(200, json={"id": "rest_1"})
    )
    email_route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "email_1"})
    )

    pb = PocketBaseClient(settings)
    paystack = PaystackClient(settings)
    email = EmailService(settings)

    monday = date(2026, 8, 24)
    decisions = await run_payout_cycle(PayoutTrigger.MONDAY, monday, pb, paystack, email)

    assert len(decisions) == 1
    assert decisions[0].should_pay is True
    assert decisions[0].amount_cents == 25000
    assert create_payout_route.called
    assert update_payout_route.called
    assert email_route.called

    await pb.close()
    await paystack.close()
    await email.close()


@pytest.mark.asyncio
@respx.mock
async def test_payout_cycle_skips_restaurant_with_existing_idempotent_payout():
    """This is the re-run safety test - if a payout record with this
    idempotency key already exists, no transfer should be attempted."""
    settings = _settings()

    respx.post("https://pb.example.com/api/admins/auth-with-password").mock(
        return_value=httpx.Response(200, json={"token": "admin-token"})
    )
    respx.get("https://pb.example.com/api/collections/restaurants/records").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "rest_1",
                        "name": "Test Diner",
                        "email": "owner@testdiner.co.za",
                        "wallet_available_balance_cents": 25000,
                        "payout_status": "active",
                        "paystack_subaccount_code": "ACCT_1",
                        "paystack_recipient_code": "RCP_1",
                    }
                ]
            },
        )
    )
    # This time, an existing payout WITH this idempotency key is returned
    respx.get("https://pb.example.com/api/collections/payouts/records").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "payout_existing", "idempotency_key": "rest_1:2026-08-25"}]}
        )
    )
    transfer_route = respx.post("https://api.paystack.co/transfer").mock(
        return_value=httpx.Response(200, json={"status": True, "data": {}})
    )

    pb = PocketBaseClient(settings)
    paystack = PaystackClient(settings)
    email = EmailService(settings)

    monday = date(2026, 8, 24)
    decisions = await run_payout_cycle(PayoutTrigger.MONDAY, monday, pb, paystack, email)

    assert len(decisions) == 1
    assert decisions[0].should_pay is True  # decision itself says eligible...
    assert not transfer_route.called  # ...but the runner must not call Paystack again

    await pb.close()
    await paystack.close()
    await email.close()
