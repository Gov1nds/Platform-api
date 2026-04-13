from __future__ import annotations

from twilio.rest import Client

from app.core.config import settings


class TwilioSMSClient:
    def __init__(self) -> None:
        self.account_sid = settings.TWILIO_ACCOUNT_SID
        self.auth_token = settings.TWILIO_AUTH_TOKEN
        self.from_number = settings.TWILIO_FROM_NUMBER

    def configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)

    def send(self, *, to_number: str, body: str) -> dict:
        if not self.configured():
            raise RuntimeError("Twilio is not configured")
        client = Client(self.account_sid, self.auth_token)
        msg = client.messages.create(from_=self.from_number, to=to_number, body=body)
        return {"sid": msg.sid, "status": msg.status, "error_code": msg.error_code}
