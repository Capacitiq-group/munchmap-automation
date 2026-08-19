"""
Thin async wrapper around the Paystack API. Exposes only what this
service needs: creating transfer recipients, initiating transfers, and
verifying inbound webhook signatures.

Docs referenced: https://paystack.com/docs/transfers/single-transfers/
and https://paystack.com/docs/payments/webhooks/
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from app.config import Settings


class PaystackError(RuntimeError):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Paystack returned {status_code}: {body}")


class PaystackClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._base_url = settings.paystack_base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=20.0)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.paystack_secret_key}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.request(
            method, f"{self._base_url}{path}", headers=self._headers(), **kwargs
        )
        body = response.json()
        if response.status_code >= 400 or body.get("status") is False:
            raise PaystackError(response.status_code, body)
        return body

    async def create_transfer_recipient(
        self,
        name: str,
        account_number: str,
        bank_code: str,
        currency: str = "ZAR",
    ) -> str:
        """Creates (or Paystack will dedupe internally on repeat calls with
        identical details) a transfer recipient and returns the
        recipient_code. This should be cached on the restaurant record
        (paystack_recipient_code) rather than recreated on every payout run."""
        body = await self._request(
            "POST",
            "/transferrecipient",
            json={
                "type": "basa",  # South African bank account
                "name": name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": currency,
            },
        )
        return body["data"]["recipient_code"]

    async def initiate_transfer(
        self,
        recipient_code: str,
        amount_cents: int,
        reason: str,
        reference: str,
    ) -> dict[str, Any]:
        """
        amount_cents: Paystack's API takes amount in the currency's
        smallest unit (cents for ZAR), matching how we store balances
        internally - no conversion needed at this boundary.

        reference: MUST be the payout's idempotency_key (see
        payout_logic.build_idempotency_key). Paystack itself also
        deduplicates on `reference`, so this gives us idempotency at
        two layers: our own PocketBase unique constraint, and
        Paystack's own reference deduplication as a backstop.
        """
        body = await self._request(
            "POST",
            "/transfer",
            json={
                "source": "balance",
                "amount": amount_cents,
                "recipient": recipient_code,
                "reason": reason,
                "reference": reference,
            },
        )
        return body["data"]

    async def verify_transfer(self, reference: str) -> dict[str, Any]:
        body = await self._request("GET", f"/transfer/verify/{reference}")
        return body["data"]

    def verify_webhook_signature(self, raw_body: bytes, signature_header: str) -> bool:
        """
        Paystack signs webhook payloads with HMAC-SHA512 using your
        secret key, sent in the x-paystack-signature header. This must
        be checked before trusting ANY webhook payload - otherwise
        anyone who finds the webhook URL can post fake "transfer
        succeeded" events.
        """
        computed = hmac.new(
            self._settings.paystack_secret_key.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(computed, signature_header)

    async def close(self) -> None:
        await self._client.aclose()
