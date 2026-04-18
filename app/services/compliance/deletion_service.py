"""GDPR / CCPA user deletion pipeline (Blueprint §31.5)."""
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)

def enqueue_user_deletion(db: Session, user_id: str, requested_by: str, reason: str = "user_request"):
    db.execute(text("UPDATE users SET deletion_requested_at = NOW(), deletion_reason = :r WHERE id = :uid"),
               {"uid": user_id, "r": reason})
    from app.models.events import EventAuditLog
    db.add(EventAuditLog(event_type="user.deletion_requested",
                         entity_type="user", entity_id=user_id,
                         actor_id=requested_by, actor_type="USER"))

def execute_deletions(db: Session) -> int:
    """Redact PII for users who requested deletion > 30 days ago."""
    rows = db.execute(text("""
        SELECT id FROM users
        WHERE deletion_requested_at IS NOT NULL
          AND deletion_requested_at < NOW() - INTERVAL '30 days'
          AND email NOT LIKE 'deleted_%%@redacted.local'
    """)).fetchall()
    count = 0
    for r in rows:
        uid = r.id
        db.execute(text("""
            UPDATE users SET
                email = 'deleted_' || :uid || '@redacted.local',
                full_name = 'Deleted User',
                phone_number = NULL,
                profile_image_url = NULL
            WHERE id = :uid
        """), {"uid": uid})
        db.execute(text("UPDATE guest_sessions SET ip_address = NULL WHERE user_id = :uid"), {"uid": uid})
        db.execute(text("""
            UPDATE notifications SET title = 'Redacted', body = 'Redacted'
            WHERE user_id = :uid
        """), {"uid": uid})
        count += 1
    return count
