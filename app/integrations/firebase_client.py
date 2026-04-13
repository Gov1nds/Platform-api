from __future__ import annotations

import threading

import firebase_admin
from firebase_admin import credentials, messaging

from app.core.config import settings


class FirebasePushClient:
    _app = None
    _lock = threading.Lock()

    def __init__(self, credentials_path: str | None = None, project_id: str | None = None) -> None:
        self.credentials_path = credentials_path or settings.FIREBASE_CREDENTIALS_PATH
        self.project_id = project_id or settings.FIREBASE_PROJECT_ID

    def configured(self) -> bool:
        return bool(self.credentials_path)

    def _get_app(self):
        if not self.configured():
            raise RuntimeError("Firebase is not configured")
        if self.__class__._app is not None:
            return self.__class__._app
        with self.__class__._lock:
            if self.__class__._app is None:
                cred = credentials.Certificate(self.credentials_path)
                opts = {"projectId": self.project_id} if self.project_id else None
                self.__class__._app = firebase_admin.initialize_app(cred, opts)
        return self.__class__._app

    def send(self, *, tokens: list[str], title: str, body: str, data: dict | None = None) -> dict:
        if not tokens:
            raise RuntimeError("No Firebase tokens supplied")
        app = self._get_app()
        msg = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=tokens[:500],
        )
        resp = messaging.send_each_for_multicast(msg, app=app)
        return {
            "success_count": resp.success_count,
            "failure_count": resp.failure_count,
        }
