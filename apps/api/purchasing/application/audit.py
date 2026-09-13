from uuid import uuid4

from sqlalchemy.orm import Session

from purchasing.infrastructure.models import AuditEvent


def record_event(
    session: Session,
    review_id: str,
    event_type: str,
    payload: dict | None = None,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
) -> None:
    session.add(
        AuditEvent(
            review_id=review_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=str(uuid4()),
            payload_json=payload or {},
        )
    )
