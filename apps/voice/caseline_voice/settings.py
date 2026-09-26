"""Voice-agent configuration. Holds no firm phone numbers of its own except the defensive allowlist."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
APPROVED_DEMO_NUMBERS = frozenset({"+12676804795", "+16173187562"})


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ENV_FILE, extra="ignore", hide_input_in_errors=True)

    caseline_api_base_url: str = "http://127.0.0.1:8000"
    caseline_internal_api_token: SecretStr = SecretStr("dev-only-token")
    # The Guava-managed CaseLine inbound number callers dial.
    guava_agent_number: str = "+14849687497"
    # Defense in depth: even a backend-authorized destination is dialed only if it is also listed here.
    voice_transfer_allowlist: str = "+12676804795,+16173187562"
    default_phone_region: str = "US"

    @property
    def transfer_allowlist(self) -> frozenset[str]:
        allow = frozenset(n.strip() for n in self.voice_transfer_allowlist.split(",") if n.strip())
        # Never widen beyond the two approved demo destinations without changing this code on purpose.
        return allow & APPROVED_DEMO_NUMBERS
