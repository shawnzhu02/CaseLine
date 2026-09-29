"""Replay a live-mode call through the real voice CallFlow and the real API (in-process). Nothing is dialed.

Usage: python scripts/live_call_replay.py
"""

from __future__ import annotations

import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "apps" / "voice")]
warnings.filterwarnings("ignore")

from caseline_voice.backend_client import CaseLineBackend  # noqa: E402
from caseline_voice.flow import CallFlow  # noqa: E402
from caseline_voice.telecom import MockCallGateway  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from caseline.config import Settings  # noqa: E402
from caseline.db import Base, Database  # noqa: E402
from caseline.main import create_app  # noqa: E402
from caseline.seed import seed  # noqa: E402

CALLS = {
    # The 16:20 call that looped: fire, Boston, someone hurt, then "no" to medical treatment.
    "house-fire-someone-hurt": ("So my house burned down about three days ago in Boston, Massachusetts. "
                                "One person did get hurt.", ["Um no actually.", "Uh no."]),
    "demo-script": ("My house burned down. Everything is gone. It was in Cambridge, Massachusetts.",
                    ["Yes. I went to the hospital because of smoke inhalation.",
                     "Yes. We had already told the landlord that some of the electrical outlets were sparking."]),
    "off-script": ("I got a parking ticket in Columbus, Ohio and I think it was unfair.", ["No.", "No.", "No."]),
}


def replay(name: str, story: str, answers: list[str]) -> str:
    token = "replay-token"
    settings = Settings(_env_file=None, app_env="test", database_url="sqlite://", caseline_internal_api_token=token,
                        demo_mode=True, demo_live_transfer_enabled=True, demo_simulate_firm_availability=True,
                        rate_limit_per_minute=0)
    db = Database(settings.database_url)
    Base.metadata.create_all(db.engine)
    with db.sessionmaker() as s:
        seed(s, settings)
    app = create_app(settings, db, clock=lambda: datetime(2026, 9, 29, 15, 0, tzinfo=UTC))
    backend = CaseLineBackend("http://testserver", token, client=TestClient(app))
    flow = CallFlow(backend, frozenset({"+12676804795"}), live_assessment=True)
    gw = MockCallGateway(call_id=name, caller_id_number="+12125550100",
                         fields={"intake_consent": "yes", "issue_summary": story})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    flow.on_task_complete(gw, "story")
    steps = ["story"]
    remaining = list(answers)
    while gw.current_task and gw.current_task.startswith("q_") and remaining:
        gw.fields["answer"] = remaining.pop(0)
        steps.append(gw.current_task)
        flow.on_task_complete(gw, gw.current_task)
    if gw.current_task == "transfer_consent":
        gw.fields["transfer_consent"] = "yes"
        flow.on_task_complete(gw, "transfer_consent")
    outcome = (f"TRANSFER -> {gw.transfers[0][0]} (mock)" if gw.transfers
               else f"ended: {(gw.ended_with or '')[:90]}")
    return f"{name}: {' -> '.join(steps)} -> {outcome}"


if __name__ == "__main__":
    for call, (story, answers) in CALLS.items():
        print(replay(call, story, answers))
