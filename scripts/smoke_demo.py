"""Mock end-to-end demo: a scripted inbound call runs through the real voice CallFlow and the real API
(in-process, throwaway SQLite, MockCallGateway). No phone call, SMS or email is ever made.

Usage (from the repo root, with the API venv active):
    python scripts/smoke_demo.py --scenario firm-a|firm-b|after-hours|declined|live-disabled|all
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "apps" / "voice")]

import warnings  # noqa: E402

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
from caseline_voice.backend_client import CaseLineBackend  # noqa: E402
from caseline_voice.flow import CallFlow  # noqa: E402
from caseline_voice.settings import VoiceSettings  # noqa: E402
from caseline_voice.telecom import MockCallGateway  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from caseline.config import Settings  # noqa: E402
from caseline.db import Base, Database  # noqa: E402
from caseline.main import create_app  # noqa: E402
from caseline.providers import Gateways  # noqa: E402
from caseline.providers.email_mock import MockEmailGateway  # noqa: E402
from caseline.providers.guava_mock import MockGuavaSmsGateway  # noqa: E402
from caseline.seed import seed  # noqa: E402
from caseline.workers.outbox import process_due  # noqa: E402

DAYTIME = datetime(2026, 9, 29, 13, 0, tzinfo=UTC)  # 09:00 New York: demo firms open
NIGHT = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)  # 23:00 New York: demo firms closed
TOKEN = "smoke-demo-token"

SCENARIOS = {
    "firm-a": {"area": "Demo matter A", "now": DAYTIME, "live": True, "transfer_consent": "yes"},
    "firm-b": {"area": "Demo matter B", "now": DAYTIME, "live": True, "transfer_consent": "yes"},
    "after-hours": {"area": "Demo matter A", "now": NIGHT, "live": True, "transfer_consent": "yes"},
    "declined": {"area": "Demo matter B", "now": DAYTIME, "live": True, "transfer_consent": "no"},
    "live-disabled": {"area": "Demo matter A", "now": DAYTIME, "live": False, "transfer_consent": "yes"},
}


def run(name: str) -> str:
    sc = SCENARIOS[name]
    # Live gate open here only inside this throwaway process; GUAVA_MODE=mock and the gateway is a recorder.
    settings = Settings(_env_file=None, app_env="test", database_url="sqlite://", guava_mode="mock",
                        caseline_internal_api_token=TOKEN, demo_mode=True, demo_live_transfer_enabled=sc["live"],
                        guava_sms_enabled=True, guava_sms_from_number="+12025550199")
    db = Database(settings.database_url)
    Base.metadata.create_all(db.engine)
    with db.sessionmaker() as s:
        seed(s, settings)
    app = create_app(settings, db, clock=lambda: sc["now"])
    backend = CaseLineBackend("http://testserver", TOKEN, client=TestClient(app))
    flow = CallFlow(backend, VoiceSettings(_env_file=None).transfer_allowlist)

    gw = MockCallGateway(call_id=f"smoke-{name}", caller_id_number="+12125550100", fields={
        "intake_consent": "yes", "recording_ok": "yes", "caller_name": "Alex Demo",
        "issue_summary": "Fictional demo matter (invented caller, invented facts)", "immediate_danger": "no",
        "jurisdiction": "CaseLine demo region", "practice_area": sc["area"], "callback_number": "212-555-0100",
    })
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    flow.on_task_complete(gw, "triage")
    firm = gw.get_variable("firm_name")
    if gw.current_task == "transfer_consent":
        gw.fields["transfer_consent"] = sc["transfer_consent"]
        flow.on_task_complete(gw, "transfer_consent")
    if gw.transfers:
        dest, _ = gw.transfers[0]
        flow.on_session_end(gw, "bot-transfer")
        return f"{name} -> {firm} -> {dest} -> MOCK TRANSFER ONLY (attempt recorded as 'requested', not connected)"
    if gw.current_task == "extended_intake":
        gw.fields.update({"x_event_date": "last week", "share_consent": "yes", "sms_consent": "yes"})
        flow.on_task_complete(gw, "extended_intake")
        gateways = Gateways(sms=MockGuavaSmsGateway(), email=MockEmailGateway())
        with db.sessionmaker() as s:
            process_due(s, settings, gateways, now=sc["now"])
        flow.on_session_end(gw, "bot-hangup")
        return (f"{name} -> {firm} -> no live transfer -> extended intake/referral path "
                f"(mock email x{len(gateways.email.sent)}, mock Guava SMS x{len(gateways.sms.sent)})")
    return f"{name} -> ended: {gw.ended_with}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    args = parser.parse_args()
    for name in SCENARIOS if args.scenario == "all" else [args.scenario]:
        print(run(name))


if __name__ == "__main__":
    main()
