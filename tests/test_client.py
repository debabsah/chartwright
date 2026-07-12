"""Client resilience: rate-limited requests back off and retry instead of
dying (found by live CI: Superset 4.1.4 returned 429 under the adversary's
burst of decompile lookups)."""

from chartwright.client import SupersetClient


class _Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def _client() -> SupersetClient:
    return SupersetClient("http://superset.test", "u", "p")


def test_429_backs_off_and_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr("chartwright.client.time.sleep", sleeps.append)
    responses = [_Resp(429, {"Retry-After": "1"}), _Resp(429), _Resp(200)]
    r = _client()._send(lambda: responses.pop(0))
    assert r.status_code == 200
    assert sleeps == [1.0, 2.0]   # header honored, then the default


def test_429_retries_are_bounded(monkeypatch):
    monkeypatch.setattr("chartwright.client.time.sleep", lambda s: None)
    r = _client()._send(lambda: _Resp(429))
    assert r.status_code == 429   # exhausted: caller's _raise_for reports it typed
