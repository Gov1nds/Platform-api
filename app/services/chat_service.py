"""
WebSocket connection manager for real-time chat.

Manages per-thread connection pools, broadcasts messages to all
participants in a thread, and handles graceful connect/disconnect.

References: Blueprint Section 14
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Room-based WebSocket connection manager keyed by thread_id."""

    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, thread_id: str, websocket: WebSocket) -> None:
        """Accept and register a WebSocket connection for a thread."""
        await websocket.accept()
        if thread_id not in self.active_connections:
            self.active_connections[thread_id] = []
        self.active_connections[thread_id].append(websocket)
        logger.info("WS connected: thread=%s, total=%d", thread_id, len(self.active_connections[thread_id]))

    def disconnect(self, thread_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from its thread room."""
        conns = self.active_connections.get(thread_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            self.active_connections.pop(thread_id, None)
        logger.info("WS disconnected: thread=%s", thread_id)

    async def broadcast(self, thread_id: str, message: dict[str, Any]) -> None:
        """Send a message to all connections in a thread."""
        conns = self.active_connections.get(thread_id, [])
        dead: list[WebSocket] = []
        payload = json.dumps(message, default=str)
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(thread_id, ws)

    async def send_personal(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        """Send a message to a single connection."""
        try:
            await websocket.send_text(json.dumps(message, default=str))
        except Exception:
            logger.exception("Failed to send personal WS message")

    def get_connection_count(self, thread_id: str) -> int:
        """Return active connection count for a thread."""
        return len(self.active_connections.get(thread_id, []))


# Module-level singleton
connection_manager = ConnectionManager()
