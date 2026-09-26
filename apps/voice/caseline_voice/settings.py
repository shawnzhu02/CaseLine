"""Voice-agent configuration. Holds no firm phone numbers of its own except the defensive allowlist."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_PARENTS = Path(__file__).resolve().parents
# In a container the package sits at a shallow path (/app/...), so fall back to ./.env.
REPO_ENV_FILE = _PARENTS[3] / ".env" if len(_PARENTS) > 3 else Path(".env")
APPROVED_DEMO_NUMBERS = frozenset({"+12676804795", "+16173187562"})


class VoiceSettings(BaseSettings):
    # Repo-root .env for local runs; apps/voice/.env (git-ignored) for a `guava deploy` bundle. Later file wins.
    model_config = SettingsConfigDict(env_file=(REPO_ENV_FILE, Path(__file__).resolve().parents[1] / ".env"),
                                      extra="ignore", hide_input_in_errors=True)

    caseline_api_base_url: str = "http://127.0.0.1:8000"
    caseline_internal_api_token: SecretStr = SecretStr("dev-only-token")
    # The Guava-managed CaseLine inbound number callers dial.
    guava_agent_number: str = "+14849687497"
    # Defense in depth: even a backend-authorized destination is dialed only if it is also listed here.
    voice_transfer_allowlist: str = "+12676804795,+16173187562"
    default_phone_region: str = "US"
    # Live assessment: stream caller speech to the API, let it pick follow-up questions and the routing
    # (jurisdiction/category). Off = the classic fixed checklist with multiple-choice routing fields.
    voice_live_assessment: bool = False
    # Permanent Guava WebRTC code so people can talk to the agent from a browser (judge demo page).
    guava_webrtc_code: str | None = None
    speech_debounce_seconds: float = 0.9

    @property
    def transfer_allowlist(self) -> frozenset[str]:
        allow = frozenset(n.strip() for n in self.voice_transfer_allowlist.split(",") if n.strip())
        # Never widen beyond the two approved demo destinations without changing this code on purpose.
        return allow & APPROVED_DEMO_NUMBERS
