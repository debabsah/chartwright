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


CRED_TOML = '''
[preset_dryrun]
base_url = "https://ws.us1.app.preset.io"
preset_credentials_file = "{path}"
'''


def test_credentials_file_from_preset_cli_is_reused(tmp_path):
    cred = tmp_path / "credentials.yaml"
    cred.write_text(
        "api_token: stored-name\napi_secret: stored-secret\nbaseurl: https://api.app.preset.io/\n"
    )
    f = _write(tmp_path, CRED_TOML.format(path=cred.as_posix()))
    p = load_profile("preset_dryrun", f)
    assert (p.api_token, p.api_secret) == ("stored-name", "stored-secret")
    assert p.preset_baseurl == "https://api.app.preset.io/"   # taken from the stored file
    assert p.username == "" and p.password == ""


def test_credentials_file_missing_names_the_path(tmp_path):
    f = _write(tmp_path, CRED_TOML.format(path=(tmp_path / "nope.yaml").as_posix()))
    with pytest.raises(ProfileError, match="nope.yaml"):
        load_profile("preset_dryrun", f)


def test_credentials_file_without_keys_lists_keys_not_values(tmp_path):
    cred = tmp_path / "credentials.yaml"
    cred.write_text("unrelated: sup3r-s3cret-value\n")
    f = _write(tmp_path, CRED_TOML.format(path=cred.as_posix()))
    with pytest.raises(ProfileError) as ei:
        load_profile("preset_dryrun", f)
    assert "unrelated" in str(ei.value)
    assert "sup3r-s3cret-value" not in str(ei.value)   # errors must not leak the file's contents


def test_explicit_preset_baseurl_beats_the_stored_one(tmp_path):
    cred = tmp_path / "credentials.yaml"
    cred.write_text("api_token: n\napi_secret: s\nbaseurl: https://stored.example.com/\n")
    body = CRED_TOML.format(path=cred.as_posix()) + 'preset_baseurl = "https://override.example.com/"\n'
    p = load_profile("preset_dryrun", _write(tmp_path, body))
    assert p.preset_baseurl == "https://override.example.com/"


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


def test_from_profile_carries_every_profile_setting():
    """The CLI and the MCP server both build their client here; a profile field
    the client takes but this skips would break sign-in on both at once."""
    import dataclasses

    from chartwright.profiles import Profile

    p = Profile(name="w", base_url="https://ws.example/", username="u", password="pw",
                auth_provider="ldap", ca_bundle="/ca.pem", verify=False, api_token="t",
                api_secret="s", preset_baseurl="https://api.example/")
    c = SupersetClient.from_profile(p)
    for f in dataclasses.fields(Profile):
        if f.name != "name":
            want = getattr(p, f.name).rstrip("/") if f.name == "base_url" else getattr(p, f.name)
            assert getattr(c, f.name) == want, f.name
