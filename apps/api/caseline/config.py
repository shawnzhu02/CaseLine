"""Typed configuration with fail-fast safety assertions (spec §17)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import phonenumbers
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The only two live transfer destinations the user approved for the supervised demo (spec §4A).
# Repo-root .env, so commands work from any directory.
REPO_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

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
        if self.guava_sms_enabled and not self.guava_sms_from_number:
            raise ConfigError("GUAVA_SMS_ENABLED=true requires GUAVA_SMS_FROM_NUMBER")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
