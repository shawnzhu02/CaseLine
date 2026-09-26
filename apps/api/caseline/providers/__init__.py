"""Provider adapters. Guava is the only telephony/SMS provider; email is separate."""

from __future__ import annotations

from dataclasses import dataclass

from caseline.config import Settings
from caseline.providers.email_gateway import EmailGateway, ResendEmailGateway
from caseline.providers.email_mock import MockEmailGateway
from caseline.providers.guava_gateway import GuavaSmsGateway, LiveGuavaSmsGateway
from caseline.providers.guava_mock import MockGuavaSmsGateway


@dataclass
class Gateways:
    sms: GuavaSmsGateway
    email: EmailGateway


def build_gateways(settings: Settings) -> Gateways:
    if settings.guava_mode == "live" and settings.guava_sms_enabled and settings.guava_api_key:
        sms: GuavaSmsGateway = LiveGuavaSmsGateway(settings.guava_api_key.get_secret_value())
    else:
        sms = MockGuavaSmsGateway()
    if settings.email_provider == "resend" and settings.resend_api_key and settings.email_from:
        email: EmailGateway = ResendEmailGateway(settings.resend_api_key.get_secret_value(), settings.email_from)
    else:
        email = MockEmailGateway()
    return Gateways(sms=sms, email=email)
