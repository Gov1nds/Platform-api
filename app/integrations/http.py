from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def build_sync_client(*, base_url: str | None = None, headers: dict | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url or "", headers=headers or {}, timeout=DEFAULT_TIMEOUT)


def build_async_client(*, base_url: str | None = None, headers: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url or "", headers=headers or {}, timeout=DEFAULT_TIMEOUT)
