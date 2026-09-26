"""Typed configuration with fail-fast safety assertions (spec §17)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import phonenumbers
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The only two live transfer destinations the user approved for the supervised demo (spec §4A).
# Repo-root .env, so commands work from any directory.
_PARENTS = Path(__file__).resolve().parents
# In a container the package sits at a shallow path (/app/...), so fall back to ./.env.
REPO_ENV_FILE = _PARENTS[3] / ".env" if len(_PARENTS) > 3 else Path(".env")

APPROVED_DEMO_NUMBERS: frozenset[str] = frozenset({"+12676804795", "+16173187562"})


class ConfigError(ValueError):
    """Raised for unsafe or incomplete configuration. Never includes secret values."""


def is_e164(number: str) -> bool:
    if not number.startswith("+"):
        return False
    try:
        parsed = phonenumbers.parse(number, None)
    except phonenumbers.NumberParseException:
        return False
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164) == number


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ENV_FILE, extra="ignore", hide_input_in_errors=True)

    app_env: Literal["development", "test", "staging", "demo", "production"] = "development"
    api_public_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./caseline-dev.db"

    telecom_provider: str = "guava"
    guava_mode: Literal["mock", "live"] = "mock"
    guava_api_key: SecretStr | None = None
    guava_agent_number: str | None = None
    guava_sms_enabled: bool = False
    guava_sms_from_number: str | None = None

    caseline_internal_api_token: SecretStr = SecretStr("dev-only-token")

    email_provider: Literal["mock", "resend"] = "mock"
    resend_api_key: SecretStr | None = None
    email_from: str | None = None

    demo_mode: bool = False
    demo_live_transfer_enabled: bool = False
    demo_firm_a_name: str = "Demo Partner Firm A"
    demo_firm_a_transfer_number: str = "+12676804795"
    demo_firm_b_name: str = "Demo Partner Firm B"
    demo_firm_b_transfer_number: str = "+16173187562"
    demo_transfer_allowlist: str = "+12676804795,+16173187562"
    demo_simulate_firm_availability: bool = False

    transfer_authorization_ttl_seconds: int = 120
    notification_max_retries: int = 5
    notification_backoff_base_seconds: int = 30
    referral_expiry_hours: int = 72
    stale_transfer_minutes: int = 15
    unacknowledged_referral_hours: int = 24
    stale_firm_availability_days: int = 7

    # Field-level encryption for caller PII (Fernet key) and HMAC secret for signed report links.
    caseline_field_encryption_key: SecretStr | None = None
    report_link_secret: SecretStr | None = None
    report_link_ttl_minutes: int = 30  # links opened from the operator dashboard
    report_email_link_ttl_hours: int = 72  # links sent in firm alert emails

    # Requests per minute per client (in-memory; per-process). 0 disables.
    rate_limit_per_minute: int = 120
    report_link_rate_limit_per_minute: int = 30

    # Live assessment. Claude only extracts facts from caller speech; rules decide category/urgency/match.
    assessment_llm_enabled: bool = False
    anthropic_api_key: SecretStr | None = None
    assessment_model: str = "claude-sonnet-5"
    assessment_timeout_seconds: float = 6.0
    # Public judge page may show the most recent REAL call's non-identifying assessment (demo line only).
    public_demo_show_live_calls: bool = False
    public_demo_window_minutes: int = 30

    # Comma-separated origins allowed to call the API from a browser (the admin dashboard calls server-side).
    cors_allow_origins: str = ""

    @property
    def transfer_allowlist(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.demo_transfer_allowlist.split(",") if n.strip())

    @property
    def live_transfer_gate_open(self) -> bool:
        """Server-side gate for returning a dialable destination to the voice agent."""
        return self.demo_mode and self.demo_live_transfer_enabled

    @property
    def simulated_availability_active(self) -> bool:
        return self.demo_mode and self.demo_simulate_firm_availability and self.app_env != "production"

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        # Hosted Postgres (Render, Heroku-style) hands out postgres:// URLs; SQLAlchemy needs the psycopg driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @model_validator(mode="after")
    def _assert_safe(self) -> Settings:
        if self.telecom_provider != "guava":
            raise ConfigError("TELECOM_PROVIDER must be 'guava'; no other telephony provider is permitted")
        if self.guava_mode == "live" and (not self.guava_api_key or not self.guava_agent_number):
            raise ConfigError("GUAVA_MODE=live requires GUAVA_API_KEY and GUAVA_AGENT_NUMBER")
        if self.demo_live_transfer_enabled and not self.demo_mode:
            raise ConfigError("DEMO_LIVE_TRANSFER_ENABLED=true is invalid unless DEMO_MODE=true")
        if self.app_env == "test" and self.guava_mode == "live":
            raise ConfigError("APP_ENV=test may never use GUAVA_MODE=live")
        if self.app_env == "production" and (self.demo_mode or self.demo_simulate_firm_availability):
            raise ConfigError("Demo flags must be off in production")
        for number in [*self.transfer_allowlist, self.demo_firm_a_transfer_number, self.demo_firm_b_transfer_number]:
            if not is_e164(number):
                raise ConfigError("Every transfer number must be a valid E.164 number")
        if self.demo_mode and not self.transfer_allowlist <= APPROVED_DEMO_NUMBERS:
            raise ConfigError("In demo mode the transfer allowlist may contain only the two approved demo numbers")
        if {self.demo_firm_a_transfer_number, self.demo_firm_b_transfer_number} - APPROVED_DEMO_NUMBERS:
            raise ConfigError("Demo firm transfer numbers must be the two approved demo numbers")
        if self.app_env in {"staging", "demo", "production"}:
            if self.caseline_internal_api_token.get_secret_value() in {"", "dev-only-token"}:
                raise ConfigError("CASELINE_INTERNAL_API_TOKEN must be set outside development")
            if not self.caseline_field_encryption_key or not self.report_link_secret:
                raise ConfigError(
                    "CASELINE_FIELD_ENCRYPTION_KEY and REPORT_LINK_SECRET must be set outside development")
        if self.guava_sms_enabled and not self.guava_sms_from_number:
            raise ConfigError("GUAVA_SMS_ENABLED=true requires GUAVA_SMS_FROM_NUMBER")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
