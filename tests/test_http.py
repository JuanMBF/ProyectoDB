import pytest
import requests

from preciosdb import http
from preciosdb.http import RequestsClient


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.url = "https://example.com/api"
        self.headers = {"Content-Type": "application/json"}
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")


def test_returns_json_and_headers(monkeypatch):
    client = RequestsClient("test-agent", delay=0)
    monkeypatch.setattr(client.session, "get", lambda url, params, timeout: FakeResponse(data=[1, 2]))

    response = client.get_json("https://example.com/api")

    assert response.data == [1, 2]
    assert response.status == 200
    assert response.headers["Content-Type"] == "application/json"
    assert client.session.headers["User-Agent"] == "test-agent"


def test_http_errors_are_raised(monkeypatch):
    client = RequestsClient("test-agent", delay=0)
    monkeypatch.setattr(client.session, "get", lambda url, params, timeout: FakeResponse(status=403))

    with pytest.raises(requests.HTTPError):
        client.get_json("https://example.com/api")


def test_waits_between_requests(monkeypatch):
    clock = iter([100.0, 100.2, 100.3, 101.5])
    sleeps = []
    monkeypatch.setattr(http.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    client = RequestsClient("test-agent", delay=1.0)
    client._last_request = 99.0
    monkeypatch.setattr(client.session, "get", lambda url, params, timeout: FakeResponse(data={}))

    client.get_json("https://example.com/a")  # 1 s since the last one: no wait
    client.get_json("https://example.com/b")  # 0.1 s since the last one: waits 0.9 s

    assert sleeps == [pytest.approx(0.9)]
