"""Tests for the anonymous LinkedIn HTTP client."""

import requests

from info_triage.extractors.linkedin.client import USER_AGENT, AnonymousClient, FetchError


class _Response:
    def __init__(self, status=200, body=b"<html></html>", content_type="text/html; charset=utf-8"):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"
        self.url = "https://www.linkedin.com/posts/example/"
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


def test_requests_are_anonymous_and_transient_errors_retry():
    responses = [_Response(429), _Response(200)]
    calls = []
    sleeps = []

    def requester(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    client = AnonymousClient(requester=requester, sleeper=sleeps.append)
    html, _ = client.get_html("https://www.linkedin.com/posts/example/")
    assert html == "<html></html>"
    assert sleeps == [1.0]
    assert len(calls) == 2
    for _, kwargs in calls:
        assert kwargs["cookies"] == {}
        assert kwargs["headers"]["User-Agent"] == USER_AGENT
        assert "Authorization" not in kwargs["headers"]
        assert "Cookie" not in kwargs["headers"]


def test_access_response_is_reported_as_blocked():
    client = AnonymousClient(requester=lambda *_args, **_kwargs: _Response(999), retries=0)
    try:
        client.get_html("https://www.linkedin.com/posts/example/")
    except FetchError as exc:
        assert exc.kind == "blocked"
        assert exc.body == "<html></html>"
    else:
        raise AssertionError("expected FetchError")


def test_non_html_response_is_rejected():
    client = AnonymousClient(
        requester=lambda *_args, **_kwargs: _Response(content_type="application/json"),
        retries=0,
    )
    try:
        client.get_html("https://www.linkedin.com/posts/example/")
    except FetchError as exc:
        assert "expected HTML" in str(exc)
    else:
        raise AssertionError("expected FetchError")
