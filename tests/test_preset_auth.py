"""Preset-hosted workspaces: API key pair -> JWT, Bearer only, no CSRF.

The load-bearing test is the last one: an ordinary username/password profile
must be completely unaffected by the Preset branch existing.
"""

import pytest

from chartwright.client import SupersetAPIError, SupersetClient
from chartwright.profiles import ProfileError, load_profile


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}
        self.text = "" if payload is None else str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeSession:
    """Records every URL touched so a test can assert what was NOT called."""

    def __init__(self, routes):
        self.routes = routes          # url -> _Resp
        self.headers = {}
        self.verify = True
        self.calls = []

    def _route(self, url):
        self.calls.append(url)
        if url not in self.routes:
            raise AssertionError(f"unexpected request to {url}")
        return self.routes[url]

    def post(self, url, **kw):
        return self._route(url)

    def get(self, url, **kw):
        return self._route(url)


PRESET_TOML = '''
[preset_dryrun]
base_url = "https://ws.us1.app.preset.io"
api_token_env = "CW_TEST_TOKEN"
api_secret_env = "CW_TEST_SECRET"
'''


def _write(tmp_path, body):
    f = tmp_path / "profiles.toml"
    f.write_text(body)
    return f


def test_preset_profile_needs_no_username_or_password(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_TEST_TOKEN", "key-name")
    monkeypatch.setenv("CW_TEST_SECRET", "key-secret")
    p = load_profile("preset_dryrun", _write(tmp_path, PRESET_TOML))
    assert (p.api_token, p.api_secret) == ("key-name", "key-secret")
    assert p.username == "" and p.password == ""        # never consulted
    assert p.preset_baseurl == "https://api.app.preset.io/"


def test_preset_profile_missing_secret_names_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_TEST_TOKEN", "key-name")
    body = PRESET_TOML.replace('api_secret_env = "CW_TEST_SECRET"\n', "")
    with pytest.raises(ProfileError, match="api_secret_env"):
        load_profile("preset_dryrun", _write(tmp_path, body))


def test_preset_profile_unset_env_names_the_var(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_TEST_TOKEN", "key-name")
    monkeypatch.delenv("CW_TEST_SECRET", raising=False)
    with pytest.raises(ProfileError, match="CW_TEST_SECRET"):
        load_profile("preset_dryrun", _write(tmp_path, PRESET_TOML))


def test_preset_login_sets_bearer_and_never_fetches_csrf():
    s = _FakeSession({
        "https://api.app.preset.io/v1/auth/": _Resp({"payload": {"access_token": "jwt-123"}}),
    })
    c = SupersetClient("https://ws.us1.app.preset.io", "", "", api_token="n",
                       api_secret="s", session=s)
    c.login()
    assert s.headers["Authorization"] == "Bearer jwt-123"
    assert s.headers["Referer"] == "https://ws.us1.app.preset.io"
    assert "X-CSRFToken" not in s.headers                  # Preset has no CSRF session
    assert not any("csrf" in u for u in s.calls)
    assert not any("security/login" in u for u in s.calls)  # password login not attempted


def test_preset_auth_without_token_in_payload_is_typed():
    s = _FakeSession({"https://api.app.preset.io/v1/auth/": _Resp({"wrong": "shape"})})
    c = SupersetClient("https://ws.us1.app.preset.io", "", "", api_token="n",
                       api_secret="s", session=s)
    with pytest.raises(SupersetAPIError, match="payload.access_token"):
        c.login()


def test_password_profile_still_takes_the_old_path():
    """Regression guard: a password profile must be untouched -- password
    login, CSRF fetch, and no call to the Preset manager."""
    base = "http://localhost:8088"
    s = _FakeSession({
        f"{base}/api/v1/security/login": _Resp({"access_token": "tok"}),
        f"{base}/api/v1/security/csrf_token/": _Resp({"result": "csrf-abc"}),
    })
    c = SupersetClient(base, "admin", "pw", session=s)
    c.login()
    assert s.headers["Authorization"] == "Bearer tok"
    assert s.headers["X-CSRFToken"] == "csrf-abc"          # still required self-hosted
    assert not any("preset.io" in u for u in s.calls)
