"""
I/O layer for short links - wraps link_logic.py with actual PocketBase
reads/writes. This is what main.py calls.
"""
from __future__ import annotations

from app.pocketbase_client import PocketBaseClient
from app.config import Settings
from app.services.link_logic import (
    LinkCreationRequest,
    LinkCreationError,
    plan_link_creation,
    build_short_url,
    is_resolvable_code,
)
from app.utils.shortcode import generate_short_code

MAX_COLLISION_RETRIES = 5


class LinkServiceError(RuntimeError):
    pass


class LinkService:
    def __init__(self, settings: Settings, pocketbase: PocketBaseClient):
        self._settings = settings
        self._pb = pocketbase

    async def create_short_link(self, request: LinkCreationRequest) -> dict[str, str]:
        plan = plan_link_creation(request)
        if not plan.valid:
            raise LinkServiceError(f"invalid link request: {plan.error}")

        code = plan.candidate_code
        assert code is not None  # plan.valid guarantees this

        # Handle the (rare, expected) case where the randomly generated
        # code already exists - retry with a fresh code rather than
        # failing the whole request.
        attempts = 0
        while await self._pb.record_exists("short_links", f'code = "{code}"'):
            attempts += 1
            if attempts >= MAX_COLLISION_RETRIES:
                raise LinkServiceError(
                    "could not generate a unique short code after "
                    f"{MAX_COLLISION_RETRIES} attempts - check code length / alphabet size"
                )
            code = generate_short_code()

        record = await self._pb.create_record(
            "short_links",
            {
                "code": code,
                "target_url": request.target_url,
                "link_type": request.link_type.value,
                "restaurant": request.restaurant_id,
                "order": request.order_id,
                "click_count": 0,
            },
        )

        return {
            "code": code,
            "short_url": build_short_url(self._settings.short_link_base_url, code),
            "record_id": record["id"],
        }

    async def resolve(self, code: str) -> str | None:
        """Returns the target URL for a short code, or None if it doesn't
        resolve to anything. Also increments the click counter - this is
        a public, unauthenticated endpoint (people scanning stickers),
        so it must be cheap and must never leak information about
        whether a code almost matched something."""
        if not is_resolvable_code(code):
            return None

        matches = await self._pb.list_records("short_links", filter_query=f'code = "{code}"', per_page=1)
        if not matches:
            return None

        record = matches[0]
        await self._pb.update_record(
            "short_links", record["id"], {"click_count": record.get("click_count", 0) + 1}
        )
        return record["target_url"]
