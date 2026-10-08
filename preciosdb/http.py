"""Small HTTP client: JSON GETs with retries, timeouts and a polite delay between requests."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass(frozen=True)
class JsonResponse:
    url: str
    status: int
    data: Any
    headers: dict[str, str] = field(default_factory=dict)


class HttpClient(Protocol):
    def get_json(self, url: str, params: dict[str, Any] | None = None) -> JsonResponse: ...


class RequestsClient:
    """Real client. Retries 429/5xx with exponential backoff and waits `delay` seconds between calls."""

    def __init__(self, user_agent: str, timeout: float = 20.0, delay: float = 1.0, retries: int = 3) -> None:
        self.timeout = timeout
        self.delay = delay
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        retry = Retry(
            total=retries,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> JsonResponse:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        finally:
            self._last_request = time.monotonic()
        response.raise_for_status()
        return JsonResponse(
            url=response.url,
            status=response.status_code,
            data=response.json(),
            headers=dict(response.headers),
        )
