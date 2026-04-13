from __future__ import annotations

from sqlalchemy.orm import Session

from app.integrations.firebase_client import FirebasePushClient
from app.integrations.sendgrid_client import SendGridEmailClient
from app.integrations.twilio_client import TwilioSMSClient
from app.models.notification import Notification
from app.models.user import User
from app.services.integration_logging import integration_run


class NotificationDeliveryService:
    def __init__(self) -> None:
        self.email_client = SendGridEmailClient()
        self.sms_client = TwilioSMSClient()
        self.push_client = FirebasePushClient()

    def deliver(self, db: Session, notification: Notification) -> dict:
        user = db.query(User).filter(User.id == notification.user_id).first()
        if not user:
            raise RuntimeError("Notification recipient not found")

        if notification.channel == "email":
            return self._deliver_email(db, notification, user)
        if notification.channel == "sms":
            return self._deliver_sms(db, notification, user)
        if notification.channel == "push":
            return self._deliver_push(db, notification, user)
        return {"status": "skipped", "reason": "unsupported_channel"}

    def _deliver_email(self, db: Session, notification: Notification, user: User) -> dict:
        if not user.email:
            raise RuntimeError("Recipient email missing")
        with integration_run(db, integration_id="INT-006", provider="sendgrid", operation="send_email", payload={"notification_id": notification.id, "to": user.email}) as run:
            resp = self.email_client.send(to_email=user.email, subject=notification.title, body=notification.body or notification.title)
            run["response_count"] = 1
            return resp

    def _deliver_sms(self, db: Session, notification: Notification, user: User) -> dict:
        phone = (user.metadata_ or {}).get("phone_number")
        if not phone:
            raise RuntimeError("Recipient phone missing in user.metadata.phone_number")
        with integration_run(db, integration_id="INT-006", provider="twilio", operation="send_sms", payload={"notification_id": notification.id, "to": phone}) as run:
            resp = self.sms_client.send(to_number=phone, body=notification.body or notification.title)
            run["response_count"] = 1
            return resp

    def _deliver_push(self, db: Session, notification: Notification, user: User) -> dict:
        tokens = (user.metadata_ or {}).get("fcm_tokens") or []
        if not tokens:
            raise RuntimeError("Recipient FCM tokens missing in user.metadata.fcm_tokens")
        with integration_run(db, integration_id="INT-006", provider="firebase", operation="send_push", payload={"notification_id": notification.id, "token_count": len(tokens)}) as run:
            resp = self.push_client.send(tokens=tokens, title=notification.title, body=notification.body or notification.title, data={"notification_id": notification.id, "type": notification.type})
            run["response_count"] = resp.get("success_count", 0)
            return resp


notification_delivery_service = NotificationDeliveryService()
