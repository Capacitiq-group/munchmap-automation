"""
Short link business logic, split from I/O the same way payout_logic.py is.

link_service.py (I/O layer, talks to PocketBase) imports these functions.
This module never imports httpx or the PocketBase client - it can be
tested with plain stdlib.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.utils.shortcode import generate_short_code, is_valid_short_code


class LinkType(str, Enum):
    RESTAURANT_ORDERING = "restaurant_ordering"
    ORDER_COLLECTION = "order_collection"


class LinkCreationError(str, Enum):
    INVALID_TARGET_URL = "invalid_target_url"
    MISSING_REFERENCE = "missing_reference"


@dataclass(frozen=True)
class LinkCreationRequest:
    link_type: LinkType
    target_url: str
    restaurant_id: str | None = None
    order_id: str | None = None


@dataclass(frozen=True)
class LinkCreationPlan:
    """What the I/O layer should do to create this link - the code to try
    first, and validation results. The I/O layer is responsible for the
    actual uniqueness check against PocketBase and retrying with a new
    candidate code on collision; this function just prepares the first
    attempt and validates the request shape."""
    valid: bool
    candidate_code: str | None
    error: LinkCreationError | None


def validate_link_request(request: LinkCreationRequest) -> LinkCreationError | None:
    if not request.target_url or not (
        request.target_url.startswith("http://") or request.target_url.startswith("https://")
    ):
        return LinkCreationError.INVALID_TARGET_URL

    if request.link_type is LinkType.RESTAURANT_ORDERING and not request.restaurant_id:
        return LinkCreationError.MISSING_REFERENCE

    if request.link_type is LinkType.ORDER_COLLECTION and not request.order_id:
        return LinkCreationError.MISSING_REFERENCE

    return None


def plan_link_creation(request: LinkCreationRequest) -> LinkCreationPlan:
    error = validate_link_request(request)
    if error is not None:
        return LinkCreationPlan(valid=False, candidate_code=None, error=error)

    return LinkCreationPlan(
        valid=True,
        candidate_code=generate_short_code(),
        error=None,
    )


def build_short_url(base_url: str, code: str) -> str:
    """Assembles the final public URL. Kept separate so the format (e.g.
    whether it's /r/{code} or just /{code}) only needs to change in one
    place if that decision changes later."""
    base = base_url.rstrip("/")
    return f"{base}/r/{code}"


def is_resolvable_code(code: str) -> bool:
    """Shape-only check used by the redirect endpoint before it even
    queries PocketBase - rejects garbage input cheaply."""
    return is_valid_short_code(code)
