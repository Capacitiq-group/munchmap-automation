"""
All transactional email - password resets, order confirmations, payout
notifications, restaurant onboarding/verification status, dispute
updates, everything - goes through Resend. No other email provider,
and no email sending logic anywhere else in the codebase.

This module is split the same way as the others: pure template/payload
building (testable without network) separate from the actual send call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from app.config import Settings

RESEND_API_URL = "https://api.resend.com/emails"


class EmailTemplate(str, Enum):
    PASSWORD_RESET = "password_reset"
    CUSTOMER_ORDER_CONFIRMATION = "customer_order_confirmation"
    CUSTOMER_ORDER_READY = "customer_order_ready"
    RESTAURANT_NEW_ORDER = "restaurant_new_order"
    RESTAURANT_VERIFICATION_APPROVED = "restaurant_verification_approved"
    RESTAURANT_VERIFICATION_REJECTED = "restaurant_verification_rejected"
    RESTAURANT_PAYOUT_COMPLETED = "restaurant_payout_completed"
    RESTAURANT_PAYOUT_FAILED = "restaurant_payout_failed"
    RESTAURANT_SUBSCRIPTION_PAYMENT_FAILED = "restaurant_subscription_payment_failed"
    DISPUTE_OPENED = "dispute_opened"
    DISPUTE_RESOLVED = "dispute_resolved"


# From-address per sending context. Kept centralised so it's not decided
# ad hoc at each call site, and so it's obvious which inbox replies land in.
_FROM_ADDRESS = "Munchmap <notifications@munchmap.synkra.co.za>"
_SUPPORT_REPLY_TO = "support@munchmap.synkra.co.za"


@dataclass(frozen=True)
class EmailRequest:
    to: str
    subject: str
    html: str
    reply_to: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


def build_password_reset_email(to: str, reset_url: str) -> EmailRequest:
    return EmailRequest(
        to=to,
        subject="Reset your Munchmap password",
        html=(
            f'<p>We received a request to reset your Munchmap password.</p>'
            f'<p><a href="{reset_url}">Reset your password</a></p>'
            f'<p>If you did not request this, you can ignore this email.</p>'
        ),
        tags={"template": EmailTemplate.PASSWORD_RESET.value},
    )


def build_order_confirmation_email(
    to: str, restaurant_name: str, order_reference: str, order_summary_html: str, tracking_url: str
) -> EmailRequest:
    return EmailRequest(
        to=to,
        subject=f"Your order from {restaurant_name} is confirmed",
        html=(
            f"<p>Your order reference is <strong>{order_reference}</strong>.</p>"
            f"{order_summary_html}"
            f'<p><a href="{tracking_url}">Track your order</a></p>'
        ),
        reply_to=_SUPPORT_REPLY_TO,
        tags={"template": EmailTemplate.CUSTOMER_ORDER_CONFIRMATION.value},
    )


def build_payout_completed_email(
    to: str, restaurant_name: str, amount_display: str, payout_date_display: str
) -> EmailRequest:
    return EmailRequest(
        to=to,
        subject=f"Payout sent: {amount_display}",
        html=(
            f"<p>Hi {restaurant_name},</p>"
            f"<p>Your Munchmap payout of <strong>{amount_display}</strong> was sent on "
            f"{payout_date_display}. It should reflect in your account shortly, "
            f"depending on your bank's processing time.</p>"
        ),
        tags={"template": EmailTemplate.RESTAURANT_PAYOUT_COMPLETED.value},
    )


def build_payout_failed_email(to: str, restaurant_name: str, amount_display: str) -> EmailRequest:
    return EmailRequest(
        to=to,
        subject="There was a problem sending your payout",
        html=(
            f"<p>Hi {restaurant_name},</p>"
            f"<p>We tried to send your payout of <strong>{amount_display}</strong> but it did not "
            f"go through. This is usually a banking details issue. Your balance is safe and will be "
            f"included automatically in the next payout cycle - no action needed unless your bank "
            f"details on file have changed.</p>"
        ),
        reply_to=_SUPPORT_REPLY_TO,
        tags={"template": EmailTemplate.RESTAURANT_PAYOUT_FAILED.value},
    )


def build_verification_approved_email(to: str, restaurant_name: str, listing_url: str) -> EmailRequest:
    return EmailRequest(
        to=to,
        subject="Your Munchmap listing is live",
        html=(
            f"<p>Hi {restaurant_name},</p>"
            f"<p>Your documents have been verified and your listing is now live.</p>"
            f'<p><a href="{listing_url}">View your listing</a></p>'
        ),
        tags={"template": EmailTemplate.RESTAURANT_VERIFICATION_APPROVED.value},
    )


def build_verification_rejected_email(to: str, restaurant_name: str, reason: str) -> EmailRequest:
    return EmailRequest(
        to=to,
        subject="We need something else from you before you go live",
        html=(
            f"<p>Hi {restaurant_name},</p>"
            f"<p>We were not able to verify your listing yet: {reason}</p>"
            f"<p>Please update your documents in the portal and we will review again.</p>"
        ),
        reply_to=_SUPPORT_REPLY_TO,
        tags={"template": EmailTemplate.RESTAURANT_VERIFICATION_REJECTED.value},
    )


class EmailError(RuntimeError):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Resend returned {status_code}: {body}")


class EmailService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def send(self, request: EmailRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "from": _FROM_ADDRESS,
            "to": [request.to],
            "subject": request.subject,
            "html": request.html,
        }
        if request.reply_to:
            payload["reply_to"] = request.reply_to
        if request.tags:
            payload["tags"] = [{"name": k, "value": v} for k, v in request.tags.items()]

        response = await self._client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {self._settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code >= 400:
            raise EmailError(response.status_code, response.text)
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
