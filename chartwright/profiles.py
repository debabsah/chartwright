"""Per-instance connection profiles.

Default file: ~/.config/chartwright/profiles.toml (override with CHARTWRIGHT_PROFILES env var,
e.g. when the home dir is roaming/OneDrive-redirected).

    [work]
    base_url = "https://superset.example.com"
    username = "jdoe"
    # username_env = "MY_USER_VAR"                    # OR read the username from an env var
    #                                                 # (keeps the identity out of the file too)
    password_env = "MY_EXISTING_SECRET_VAR"          # ANY env var name you already use
    # password_cmd = ["sops", "-d", "--extract", '["superset_password"]', "~/secrets.yaml"]
    #                                                 # OR a command printing the password.
    #                                                 # LIST form (recommended): no shell, no
    #                                                 # quoting ambiguity, works identically on
    #                                                 # bash/git-bash/PowerShell; ~ is expanded.
    #                                                 # STRING form also accepted: parsed with
    #                                                 # bash-style quoting on mac/linux, but runs
    #                                                 # through cmd.exe on Windows (single quotes
    #                                                 # are literal there) -- prefer the list.
    #                                                 # (macOS keychain: ["security",
    #                                                 #  "find-generic-password","-a","jdoe",
    #                                                 #  "-s","superset","-w"]; 1Password:
    #                                                 #  ["op","read","op://Work/superset/password"])
    # auth_provider = "ldap"                          # "db" default; "ldap" for LDAP logins
    # ca_bundle = "/path/to/corp-root-ca.pem"         # corporate TLS interception
    # verify = false                                  # last resort, disables TLS verification

Password resolution: password_env first (if set and non-empty), else
password_cmd. Username resolution: literal `username` first, else
`username_env` (first-listed source wins, same rule as passwords). No
secrets ever live in the file itself.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


def profiles_path() -> Path:
    override = os.environ.get("CHARTWRIGHT_PROFILES")
    if override:
        return Path(override)
    return Path.home() / ".config" / "chartwright" / "profiles.toml"


class ProfileError(RuntimeError):
    pass


@dataclass
class Profile:
    name: str
    base_url: str
    username: str
    password: str
    auth_provider: str = "db"
    ca_bundle: str | None = None
    verify: bool = True


def _expand(token: str) -> str:
    return os.path.expanduser(token) if token.startswith("~") else token


def _resolve_password(name: str, p: dict) -> str:
    env_name = p.get("password_env")
    if env_name:
        value = os.environ.get(env_name)
        if value:
            return value
    cmd = p.get("password_cmd")
    if cmd:
        # List form (recommended): exec directly -- no shell, no quoting
        # ambiguity, identical on bash / git bash / PowerShell. String form:
        # bash-style split on posix; cmd.exe shell on Windows (documented caveat).
        if isinstance(cmd, list):
            argv = [_expand(str(t)) for t in cmd]
            use_shell = False
        elif os.name != "nt":
            argv = [_expand(t) for t in shlex.split(cmd)]
            use_shell = False
        else:
            argv = cmd
            use_shell = True
        try:
            out = subprocess.run(
                argv, capture_output=True, text=True, timeout=30, check=True, shell=use_shell,
            ).stdout.strip()
        except Exception as e:  # noqa: BLE001 - any failure means no credential
            raise ProfileError(f"profile {name!r}: password_cmd failed: {e}") from e
        if out:
            return out
        raise ProfileError(f"profile {name!r}: password_cmd produced no output")
    if env_name:
        raise ProfileError(
            f"profile {name!r}: env var {env_name!r} is unset or empty; export it, "
            f"or set password_cmd to fetch from your credential manager"
        )
    raise ProfileError(f"profile {name!r}: set password_env (any env var name) or password_cmd")


def _resolve_username(name: str, p: dict) -> str:
    if "username" in p:
        return p["username"]
    env_name = p.get("username_env")
    if env_name:
        value = os.environ.get(env_name)
        if value:
            return value
        raise ProfileError(
            f"profile {name!r}: env var {env_name!r} (username_env) is unset or empty"
        )
    raise ProfileError(f"profile {name!r}: set username or username_env")


def load_profile(name: str, path: Path | None = None) -> Profile:
    path = path or profiles_path()
    if not path.exists():
        raise ProfileError(
            f"no profiles file at {path}; create it (see chartwright/profiles.py docstring) "
            f"or point CHARTWRIGHT_PROFILES at yours"
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))  # TOML is UTF-8 by spec; never the locale codepage
    if name not in data:
        raise ProfileError(f"profile {name!r} not in {path}; have: {sorted(data)}")
    p = data[name]
    if "base_url" not in p:
        raise ProfileError(f"profile {name!r} missing 'base_url'")
    return Profile(
        name=name,
        base_url=p["base_url"],
        username=_resolve_username(name, p),
        password=_resolve_password(name, p),
        auth_provider=p.get("auth_provider", "db"),
        ca_bundle=p.get("ca_bundle"),
        verify=p.get("verify", True),
    )
