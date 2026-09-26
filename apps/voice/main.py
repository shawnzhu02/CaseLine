"""CaseLine inbound voice agent. `guava run` executes this file with uv.

Modes (first CLI argument):
  (default) phone, plus the browser WebRTC code too when GUAVA_WEBRTC_CODE is set
  phone  - only the Guava-managed CaseLine number (GUAVA_AGENT_NUMBER, default +14849687497)
  chat   - text chat in the terminal (no phone call, no audio)
  local  - talk through the local microphone/speaker
Whether a transfer can actually dial is decided by the CaseLine API (DEMO_LIVE_TRANSFER_ENABLED, off by default).
"""

import sys

import guava
from guava import logging_utils

from caseline_voice.agent import build_agent
from caseline_voice.settings import VoiceSettings

if __name__ == "__main__":
    logging_utils.configure_logging()
    settings = VoiceSettings()
    agent = build_agent(settings)
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "chat":
        agent.chat()
    elif mode == "local":
        agent.call_local()
    elif mode == "phone" or not settings.guava_webrtc_code:
        agent.listen_phone(settings.guava_agent_number)
    else:
        guava.Runner().listen_phone(agent, settings.guava_agent_number).listen_webrtc(
            agent, settings.guava_webrtc_code).run()
