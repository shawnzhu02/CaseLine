"""Guava agent wiring (guava-sdk 0.45.0). The only module that constructs a guava.Agent."""

from __future__ import annotations

import guava
from guava.events import BotSessionEnded

from caseline_voice import prompts
from caseline_voice.backend_client import CaseLineBackend
from caseline_voice.flow import CallFlow
from caseline_voice.settings import VoiceSettings
from caseline_voice.telecom import GuavaCallGateway


def build_agent(settings: VoiceSettings | None = None) -> guava.Agent:
    settings = settings or VoiceSettings()
    backend = CaseLineBackend(settings.caseline_api_base_url, settings.caseline_internal_api_token.get_secret_value())
    flow = CallFlow(backend, settings.transfer_allowlist, settings.default_phone_region)

    agent = guava.Agent(name=prompts.AGENT_NAME, organization=prompts.ORGANIZATION, purpose=prompts.PURPOSE)

    @agent.on_call_start
    def on_call_start(call: guava.Call) -> None:
        flow.on_call_start(GuavaCallGateway(call))

    @agent.on_task_complete
    def on_task_complete(call: guava.Call, task_id: str) -> None:
        flow.on_task_complete(GuavaCallGateway(call), task_id)

    @agent.on_session_end
    def on_session_end(call: guava.Call, event: BotSessionEnded) -> None:
        flow.on_session_end(GuavaCallGateway(call), event.termination_reason)

    return agent
