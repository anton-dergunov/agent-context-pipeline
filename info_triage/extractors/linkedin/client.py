"""Anonymous HTTP client for public LinkedIn pages."""

from __future__ import annotations

import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable

import requests

USER_AGENT = "text-cleanup-linkedin-extractor/0.1 (+personal low-volume archival)"
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}
BLOCKED_STATUSES = {401, 403, 429, 999}


@dataclass(slots=True)
class FetchError(RuntimeError):
    message: str
    kind: str = "failed"
    body: str | None = None

    def __str__(self) -> str:
        return self.message


class AnonymousClient:
    """Small requests client that never persists or sends LinkedIn login state."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retries: int = 2,
        requester: Callable[..., requests.Response] = requests.get,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self._requester = requester
        self._sleep = sleeper

    @staticmethod
    def _headers(accept: str) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "en-GB,en;q=0.9",
        }

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return min(60.0, max(0.0, float(value)))
            except ValueError:
                try:
                    seconds = (
                        parsedate_to_datetime(value)
                        - parsedate_to_datetime(response.headers["Date"])
                    ).total_seconds()
                    return min(60.0, max(0.0, seconds))
                except (KeyError, TypeError, ValueError):
                    pass
        return float(2**attempt)

    def get(self, url: str, *, accept: str, stream: bool = False) -> requests.Response:
        last_error: requests.RequestException | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self._requester(
                    url,
                    headers=self._headers(accept),
                    cookies={},
                    allow_redirects=True,
                    timeout=(min(10.0, self.timeout), self.timeout),
                    stream=stream,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise FetchError(f"request failed: {exc}") from exc
                self._sleep(float(2**attempt))
                continue

            if response.status_code in TRANSIENT_STATUSES and attempt < self.retries:
                delay = self._retry_delay(response, attempt)
                response.close()
                self._sleep(delay)
                continue
            return response

        raise FetchError(f"request failed: {last_error}")

    def get_html(self, url: str, *, max_bytes: int = 5 * 1024 * 1024) -> tuple[str, str]:
        response = self.get(
            url,
            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        body = response.content[: max_bytes + 1]
        text = body.decode(response.encoding or "utf-8", errors="replace")
        if response.status_code in BLOCKED_STATUSES:
            raise FetchError(
                f"LinkedIn returned HTTP {response.status_code}",
                kind="blocked",
                body=text,
            )
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FetchError(f"LinkedIn returned HTTP {response.status_code}", body=text) from exc
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            raise FetchError(
                f"expected HTML, received {content_type or 'an unknown content type'}", body=text
            )
        if len(body) > max_bytes:
            raise FetchError(f"HTML response exceeded {max_bytes} bytes", body=text)
        final_url = str(getattr(response, "url", url))
        lowered = final_url.lower()
        if any(marker in lowered for marker in ("/authwall", "/uas/login", "/checkpoint/")):
            raise FetchError(
                f"LinkedIn redirected to an access page: {final_url}", kind="blocked", body=text
            )
        return text, final_url
