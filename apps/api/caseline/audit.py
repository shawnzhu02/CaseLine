"""Audit trail helper. Metadata must be non-sensitive (IDs, states, reasons)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from caseline.models import AuditEvent


def audit(
    session: Session,
    *,
    operation: str,
    target_type: str,
    target_id: object | None,
    result: str,
    actor: str = "internal-service",
    role: str = "service",
    **metadata: object,
) -> None:
    session.add(
        AuditEvent(
            actor=actor,
            role=role,
            operation=operation,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            result=result,
            event_metadata={k: (str(v) if v is not None else None) for k, v in metadata.items()},
        )
    )
