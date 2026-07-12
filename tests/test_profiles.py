"""Credential/portability behavior of profile loading."""

import json
import sys

import pytest

from chartwright.profiles import ProfileError, load_profile, profiles_path


def _write(tmp_path, body: str):
    f = tmp_path / "profiles.toml"
    f.write_text(body)
    return f


BASE = '''
[work]
base_url = "https://superset.example.com"
username = "jdoe"
'''


def test_password_env_any_name(tmp_path, monkeypatch):
    f = _write(tmp_path, BASE + 'password_env = "MY_WEIRD_CORP_VAR_42"\n')
    monkeypatch.setenv("MY_WEIRD_CORP_VAR_42", "s3cret")
    p = load_profile("work", f)
    assert p.password == "s3cret"
    assert p.auth_provider == "db"
    assert p.verify is True


def test_password_env_unset_names_the_var_and_the_alternative(tmp_path, monkeypatch):
    f = _write(tmp_path, BASE + 'password_env = "NOPE_VAR"\n')
    monkeypatch.delenv("NOPE_VAR", raising=False)
    with pytest.raises(ProfileError, match="NOPE_VAR.*password_cmd"):
        load_profile("work", f)


def test_password_cmd_fallback(tmp_path, monkeypatch):
    cmd = f'"{sys.executable}" -c "print(chr(112)+chr(119))"'  # prints 'pw'
    f = _write(tmp_path, BASE + f"password_cmd = '{cmd}'\n")
    p = load_profile("work", f)
    assert p.password == "pw"


def test_password_cmd_list_form_no_shell(tmp_path):
    """The recommended cross-shell form: argv list, exec'd directly.
    The sops-style bracket argument survives verbatim (no shell to mangle it)."""
    f = _write(tmp_path, BASE)
    # json.dumps: valid TOML basic-string escaping for Windows paths (C:\...)
    exe = json.dumps(sys.executable)
    body = f.read_text() + (
        f'password_cmd = [{exe}, "-c", '
        f'"import sys; print(sys.argv[1])", "[\\"superset\\"][\\"password\\"]"]\n'
    )
    f.write_text(body)
    p = load_profile("work", f)
    assert p.password == '["superset"]["password"]'


def test_password_cmd_list_expands_tilde(tmp_path):
    f = _write(tmp_path, BASE)
    exe = json.dumps(sys.executable)
    body = f.read_text() + (
        f'password_cmd = [{exe}, "-c", "import sys; print(sys.argv[1])", "~/secrets.yaml"]\n'
    )
    f.write_text(body)
    p = load_profile("work", f)
    assert "~" not in p.password and p.password.endswith("secrets.yaml")


def test_env_wins_over_cmd(tmp_path, monkeypatch):
    cmd = f'"{sys.executable}" -c "print(1/0)"'  # would explode if run
    f = _write(tmp_path, BASE + 'password_env = "PRESENT_VAR"\n' + f"password_cmd = '{cmd}'\n")
    monkeypatch.setenv("PRESENT_VAR", "from-env")
    assert load_profile("work", f).password == "from-env"


def test_username_env_any_name(tmp_path, monkeypatch):
    """The identity can live in an env var too (username_env mirrors
    password_env; real work machines provision both that way)."""
    body = """
[work]
base_url = "https://superset.example.com"
username_env = "MY_USER_VAR"
password_env = "MY_PASS_VAR"
"""
    f = _write(tmp_path, body)
    monkeypatch.setenv("MY_USER_VAR", "svc-account")
    monkeypatch.setenv("MY_PASS_VAR", "pw")
    p = load_profile("work", f)
    assert p.username == "svc-account" and p.password == "pw"


def test_username_env_unset_is_a_named_error(tmp_path, monkeypatch):
    body = """
[work]
base_url = "https://superset.example.com"
username_env = "NOPE_USER_VAR"
password_env = "V"
"""
    f = _write(tmp_path, body)
    monkeypatch.setenv("V", "pw")
    monkeypatch.delenv("NOPE_USER_VAR", raising=False)
    with pytest.raises(ProfileError, match="NOPE_USER_VAR"):
        load_profile("work", f)


def test_literal_username_wins_over_env(tmp_path, monkeypatch):
    """House precedence rule: the first-listed source wins (as password_env
    wins over password_cmd); a literal username beats username_env."""
    body = """
[work]
base_url = "https://superset.example.com"
username = "literal-user"
username_env = "MY_USER_VAR"
password_env = "V"
"""
    f = _write(tmp_path, body)
    monkeypatch.setenv("MY_USER_VAR", "env-user")
    monkeypatch.setenv("V", "pw")
    assert load_profile("work", f).username == "literal-user"


def test_no_username_source_is_a_named_error(tmp_path, monkeypatch):
    body = """
[work]
base_url = "https://superset.example.com"
password_env = "V"
"""
    f = _write(tmp_path, body)
    monkeypatch.setenv("V", "pw")
    with pytest.raises(ProfileError, match="username or username_env"):
        load_profile("work", f)


def test_no_credential_source_is_a_named_error(tmp_path):
    f = _write(tmp_path, BASE)
    with pytest.raises(ProfileError, match="password_env.*password_cmd"):
        load_profile("work", f)


def test_tls_options(tmp_path, monkeypatch):
    f = _write(tmp_path, BASE + 'password_env = "V"\nca_bundle = "/etc/corp-ca.pem"\nverify = false\n')
    monkeypatch.setenv("V", "x")
    p = load_profile("work", f)
    assert p.ca_bundle == "/etc/corp-ca.pem"
    assert p.verify is False


def test_profiles_path_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_PROFILES", str(tmp_path / "elsewhere.toml"))
    assert profiles_path() == tmp_path / "elsewhere.toml"
