"""Client resilience: rate-limited requests back off and retry instead of
dying (found by live CI: Superset 4.1.4 returned 429 under the adversary's
burst of decompile lookups), and a repeatable request the server dropped
unanswered is sent once more (found by live CI on 6.1.0)."""

import pytest
import requests

from chartwright.client import SupersetAPIError, SupersetClient


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


def test_json_q_survives_fab_parse_qs_fallback():
    """FAB's JSON fallback re-parses the decoded `q` with parse_qs: '&' split
    the value (HTTP 400 on "Sales & Marketing"), '+' and '%' decoded twice."""
    import json
    import urllib.parse

    from chartwright.client import _json_q

    q = {"filters": [{"col": "slice_name", "opr": "eq", "value": "Sales & Marketing + 5% — x"}]}
    value = _json_q(q)
    assert json.loads(urllib.parse.parse_qs(f"q={value}")["q"][0]) == q


# -- a connection the server dropped before answering --------------------------------------


def _dropped(method: str, path: str = "/api/v1/x") -> requests.exceptions.ConnectionError:
    req = requests.Request(method, f"http://superset.test{path}").prepare()
    return requests.exceptions.ConnectionError(
        "('Connection aborted.', RemoteDisconnected('Remote end closed connection without "
        "response'))", request=req)


class _Ok:
    status_code = 200
    headers: dict = {}


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_a_dropped_repeatable_request_is_sent_once_more(method):
    """A pooled keep-alive connection the server had already closed fails a request
    with no answer (seen on the 6.1.0 CI sandbox). Sending a GET or PUT again means the
    same, so it is retried once."""
    c = SupersetClient("http://superset.test", "u", "p")
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise _dropped(method)
        return _Ok()
    assert c._send(fn).status_code == 200 and len(calls) == 2


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_a_dropped_post_or_delete_is_never_repeated(method):
    c = SupersetClient("http://superset.test", "u", "p")
    calls = []

    def fn():
        calls.append(1)
        raise _dropped(method)
    with pytest.raises(SupersetAPIError, match="connection to http://superset.test failed"):
        c._send(fn)
    assert len(calls) == 1


def test_a_request_dropped_twice_fails():
    c = SupersetClient("http://superset.test", "u", "p")
    calls = []

    def fn():
        calls.append(1)
        raise _dropped("GET")
    with pytest.raises(SupersetAPIError):
        c._send(fn)
    assert len(calls) == 2


def test_a_dropped_chart_query_is_sent_once_more():
    """A chart query only reads, so a POST to /api/v1/chart/data is repeatable (it
    dropped on the 6.1.0 sandbox after the client sat idle during a browser capture)."""
    c = SupersetClient("http://superset.test", "u", "p")
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise _dropped("POST", "/api/v1/chart/data")
        return _Ok()
    assert c._send(fn).status_code == 200 and len(calls) == 2
