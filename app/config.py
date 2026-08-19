"""
Central configuration. Everything is loaded from environment variables
(see .env.example) so the same code runs unmodified against Paystack's
sandbox and production, and against a local or hosted PocketBase instance.

Deliberately does NOT read the .env file itself in production - that's
the deploy environment's job. python-dotenv is only used so local
development works by dropping a .env file in the project root.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PocketBase
    pocketbase_url: str
    pocketbase_admin_email: str
    pocketbase_admin_password: str

    # Paystack
    paystack_secret_key: str
    paystack_webhook_secret: str
    paystack_base_url: str = "https://api.paystack.co"

    # Resend - all transactional email (password resets, order
    # confirmations, payout notifications, verification status, disputes)
    # goes through Resend. No other provider anywhere in this codebase.
    resend_api_key: str

    # Short links
    short_link_base_url: str = "https://mm.synkra.co.za"

    # Payout schedule - informational here; the actual trigger is external
    # (cron / GitHub Actions), this just documents what the job assumes.
    payout_trigger_days: str = "mon,wed"
    payout_trigger_hour: int = 17

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """
    Cached so we don't re-read the environment on every call. Tests that
    need different settings should call get_settings.cache_clear() first,
    or construct a Settings(...) instance directly rather than going
    through this accessor.
    """
    return Settings()
