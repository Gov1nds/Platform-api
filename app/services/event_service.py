"""Platform observability — event tracking."""
from sqlalchemy.orm import Session
from app.models.events import PlatformEvent


def track(db: Session, event_type: str, actor_id: str | None = None, actor_type: str = "user",
          resource_type: str | None = None, resource_id: str | None = None, payload: dict | None = None):
    ev = PlatformEvent(
        event_type=event_type,
        actor_id=actor_id,
        actor_type=actor_type,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=payload or {},
    )
    db.add(ev)
    db.flush()
    return ev
