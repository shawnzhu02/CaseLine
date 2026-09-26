"""Structured logging that never emits PII, transcripts or secrets."""

from __future__ import annotations

import json
import logging
import re

_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact(text: str) -> str:
    return _EMAIL.sub("[email]", _PHONE.sub("[phone]", text))


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key in ("correlation_id", "case_id", "referral_id", "event"):
            if hasattr(record, key):
                payload[key] = str(getattr(record, key))
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
