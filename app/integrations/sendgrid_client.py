from __future__ import annotations

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.core.config import settings


class SendGridEmailClient:
    def __init__(self, api_key: str | None = None, from_email: str | None = None) -> None:
        self.api_key = api_key or settings.SENDGRID_API_KEY
        self.from_email = from_email or settings.SENDGRID_FROM_EMAIL

    def configured(self) -> bool:
        return bool(self.api_key and self.from_email)

    def send(self, *, to_email: str, subject: str, body: str, html: str | None = None) -> dict:
        if not self.configured():
            raise RuntimeError("SendGrid is not configured")
        message = Mail(
            from_email=self.from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=body or "",
            html_content=html or (body or "").replace("\n", "<br />"),
        )
        client = SendGridAPIClient(self.api_key)
        resp = client.send(message)
        return {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": resp.body.decode() if isinstance(resp.body, bytes) else str(resp.body),
        }
