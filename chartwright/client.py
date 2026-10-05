"""Thin Superset REST client. Auth = JWT login + CSRF token + session cookie
(Superset requires all three for mutating multipart endpoints).

Enterprise-network posture: every requests-level failure is converted into a
typed SupersetAPIError with a hint (TLS interception -> ca_bundle, proxy,
timeouts), login guards against SSO/proxy HTML pages, and an expired access
token triggers exactly one re-login + retry (long applies on big estates).
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

import requests

from .profiles import PRESET_BASEURL, Profile


def _json_q(obj) -> str:
    """The `q` argument as JSON that survives Flask-AppBuilder's fallback.

    FAB tries rison first; on failure it re-parses the already-decoded value
    with `urllib.parse.parse_qs(f"q={value}")`, which splits at '&' and
    decodes '+' and '%' a second time. So a chart named "Sales & Marketing"
    came back HTTP 400 "Not a valid rison/json argument". Escaping those three
    as JSON unicode escapes keeps the value intact through both parses."""
    return (json.dumps(obj).replace("%", r"\u0025").replace("&", r"\u0026")
            .replace("+", r"\u002b"))


# "version_string": "5.0.0" inside a page's (unescaped) bootstrap JSON.
_BOOTSTRAP_VERSION = re.compile(r'"version_string"\s*:\s*"([^"]*)"')


class SupersetAPIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


_REPEATABLE = {"GET", "HEAD", "OPTIONS", "PUT"}
# POSTs that only read: a chart query (smoke, saved-query capture) changes nothing.
_READ_ONLY_POSTS = ("/api/v1/chart/data",)


def _dropped_unanswered(e: requests.exceptions.ConnectionError) -> bool:
    """The server closed the connection before answering (RemoteDisconnected, a
    reset), on a request that means the same sent twice."""
    req = getattr(e, "request", None)
    method = getattr(req, "method", None)
    path = urlsplit(getattr(req, "url", "") or "").path.rstrip("/")
    text = str(e)
    repeatable = method in _REPEATABLE or (method == "POST" and path.endswith(_READ_ONLY_POSTS))
    return repeatable and ("RemoteDisconnected" in text or "Connection aborted" in text
                           or "Connection reset" in text)


@dataclass
class SupersetClient:
    base_url: str
    username: str
    password: str
    auth_provider: str = "db"
    ca_bundle: str | None = None
    verify: bool = True
    # Preset mode (api_token set): API key pair -> JWT, no password login.
    api_token: str | None = None
    api_secret: str | None = None
    preset_baseurl: str = PRESET_BASEURL
    session: requests.Session = field(default_factory=requests.Session)
    _csrf: str | None = None
    _logged_in: bool = False
    _version: str | None = None
    _version_probed: bool = False

    @classmethod
    def from_profile(cls, p: Profile) -> "SupersetClient":
        """The client a profile describes (not logged in yet). The one place a
        profile becomes a client, so the CLI and the MCP server can't drift."""
        return cls(p.base_url, p.username, p.password, auth_provider=p.auth_provider,
                   ca_bundle=p.ca_bundle, verify=p.verify,
                   api_token=p.api_token, api_secret=p.api_secret,
                   preset_baseurl=p.preset_baseurl)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        # Corporate TLS interception: custom root CA, or (last resort) no verify.
        self.session.verify = self.ca_bundle if self.ca_bundle else self.verify

    # -- transport ------------------------------------------------------------

    def _send(self, fn: Callable[[], requests.Response], relogin_on_401: bool = True,
              tries_429: int = 3, tries_dropped: int = 1) -> requests.Response:
        """Run one request with typed network errors, a single 401 retry, polite
        429 backoff (Superset ships a rate limiter; corporate gateways add their
        own), and one retry of a request the server dropped unanswered, when
        sending it twice means the same as once."""
        try:
            r = fn()
        except requests.exceptions.SSLError as e:
            raise SupersetAPIError(
                "TLS verification failed; corporate TLS interception? Set ca_bundle "
                "in the profile (path to your corp root CA .pem) or REQUESTS_CA_BUNDLE; "
                f"verify=false is the last resort. Underlying: {str(e)[:300]}"
            ) from e
        except requests.exceptions.ProxyError as e:
            raise SupersetAPIError(
                f"proxy error; check HTTPS_PROXY/HTTP_PROXY/NO_PROXY: {str(e)[:300]}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise SupersetAPIError(f"request timed out against {self.base_url}: {str(e)[:200]}") from e
        except requests.exceptions.ConnectionError as e:
            if tries_dropped > 0 and _dropped_unanswered(e):
                # A pooled keep-alive connection the server had already closed (a
                # recycled gunicorn worker, an idle timeout): no answer came back.
                # GET, HEAD, OPTIONS, PUT and a chart query are safe to send again;
                # any other POST (an import, a login) or a DELETE never is.
                return self._send(fn, relogin_on_401, tries_429, tries_dropped - 1)
            raise SupersetAPIError(f"connection to {self.base_url} failed: {str(e)[:300]}") from e
        except requests.exceptions.RequestException as e:
            raise SupersetAPIError(f"connection to {self.base_url} failed: {str(e)[:300]}") from e
        if r.status_code == 401 and relogin_on_401 and self._logged_in:
            # Access token expired mid-run (default TTLs are short); one refresh.
            self.login()
            r = self._send(fn, relogin_on_401=False)
        if r.status_code == 429 and tries_429 > 0:
            try:
                delay = min(float(r.headers.get("Retry-After") or 2), 30)
            except (TypeError, ValueError):
                delay = 2.0
            time.sleep(delay)
            r = self._send(fn, relogin_on_401=relogin_on_401, tries_429=tries_429 - 1)
        return r

    # -- auth ---------------------------------------------------------------

    def login(self) -> None:
        if self.api_token:
            self._login_preset()
            return
        r = self._send(lambda: self.session.post(
            f"{self.base_url}/api/v1/security/login",
            json={
                "username": self.username,
                "password": self.password,
                "provider": self.auth_provider,
                "refresh": True,
            },
            timeout=30,
        ), relogin_on_401=False)
        self._raise_for(r, "login failed")
        try:
            token = r.json()["access_token"]
        except (ValueError, KeyError) as e:
            raise SupersetAPIError(
                "login returned a non-JSON or tokenless response; usually an SSO portal "
                "or proxy login page intercepting the API. Chartwright signs in with a "
                "Superset account's own password (database or LDAP) or Preset API tokens, "
                "never through SSO or OAuth; ask the instance's admin for such an account.",
                r.status_code, r.text[:300],
            ) from e
        self.session.headers["Authorization"] = f"Bearer {token}"
        r = self._send(lambda: self.session.get(
            f"{self.base_url}/api/v1/security/csrf_token/", timeout=30), relogin_on_401=False)
        self._raise_for(r, "csrf token fetch failed")
        self._csrf = r.json()["result"]
        self.session.headers["X-CSRFToken"] = self._csrf
        self.session.headers["Referer"] = self.base_url
        self._logged_in = True

    def _login_preset(self) -> None:
        """Preset-hosted workspaces: exchange the API key pair for a JWT at the
        manager API, then Bearer + Referer only. NO CSRF token -- Preset's own
        client (preset-io/backend-sdk) posts multipart import bundles with just
        those two, and the workspace has no /security/csrf_token/ session to
        pair one with."""
        auth_url = self.preset_baseurl.rstrip("/") + "/v1/auth/"
        r = self._send(lambda: self.session.post(
            auth_url,
            json={"name": self.api_token, "secret": self.api_secret},
            timeout=30,
        ), relogin_on_401=False)
        self._raise_for(r, "preset auth failed")
        try:
            token = r.json()["payload"]["access_token"]
        except (ValueError, KeyError, TypeError) as e:
            raise SupersetAPIError(
                f"preset auth at {auth_url} returned no payload.access_token; check the "
                "API key pair (Preset > Manage User Settings > API Keys) and that "
                "base_url points at the WORKSPACE, not the manager.",
                r.status_code, r.text[:300],
            ) from e
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.session.headers["Referer"] = self.base_url
        self._logged_in = True

    # -- generic ------------------------------------------------------------

    def get(self, path: str, **params) -> dict:
        q = {}
        if params:
            q["q"] = _json_q(params.pop("q")) if "q" in params else None
            q = {k: v for k, v in q.items() if v is not None}
            q.update(params)
        r = self._send(lambda: self.session.get(f"{self.base_url}{path}", params=q, timeout=60))
        self._raise_for(r, f"GET {path} failed")
        return r.json()

    def post_json(self, path: str, payload: dict) -> requests.Response:
        return self._send(lambda: self.session.post(f"{self.base_url}{path}", json=payload, timeout=120))

    def put_json(self, path: str, payload: dict) -> requests.Response:
        return self._send(lambda: self.session.put(f"{self.base_url}{path}", json=payload, timeout=120))

    # -- datasets / databases -----------------------------------------------

    def find_datasets(self, table: str) -> list[dict]:
        out = self.get(
            "/api/v1/dataset/",
            q={
                "filters": [{"col": "table_name", "opr": "eq", "value": table}],
                "columns": ["id", "table_name", "schema", "uuid", "database.database_name", "database.id"],
                "page_size": 100,
            },
        )
        return out["result"]

    def dataset_detail(self, dataset_id: int) -> dict:
        return self.get(f"/api/v1/dataset/{dataset_id}")["result"]

    # -- dashboards / charts ------------------------------------------------

    def find_dashboard_by_slug(self, slug: str) -> dict | None:
        out = self.get(
            "/api/v1/dashboard/",
            q={"filters": [{"col": "slug", "opr": "eq", "value": slug}], "page_size": 10},
        )
        results = out["result"]
        return results[0] if results else None

    def find_charts_by_name(self, name: str) -> list[dict]:
        """Targeted lookup (never scans the estate; corporate instances can
        hold tens of thousands of charts)."""
        out = self.get(
            "/api/v1/chart/",
            q={
                "filters": [{"col": "slice_name", "opr": "eq", "value": name}],
                "columns": ["id", "slice_name", "uuid"],
                "page_size": 100,
            },
        )
        return out["result"]

    def charts_by_uuids(self, uuid_to_name: dict[str, str]) -> dict[str, dict]:
        """uuid -> chart summary for uuids that exist, looked up per chart NAME
        (indexed server-side) and matched by uuid client-side."""
        found: dict[str, dict] = {}
        for u, name in uuid_to_name.items():
            for c in self.find_charts_by_name(name):
                if str(c.get("uuid")) == u:
                    found[u] = c
        return found

    def dashboard_charts(self, dashboard_id: int) -> list[dict]:
        return self.get(f"/api/v1/dashboard/{dashboard_id}/charts")["result"]

    def delete_chart(self, chart_id: int) -> None:
        r = self._send(lambda: self.session.delete(f"{self.base_url}/api/v1/chart/{chart_id}", timeout=60))
        self._raise_for(r, f"delete chart {chart_id} failed")

    # -- themes (6.0.0+) ----------------------------------------------------

    def themes(self) -> list[dict]:
        """Every theme on the instance: id, uuid and theme_name, from the theme list API
        (ThemeRestApi, resource "theme", superset/themes/api.py at 6.0.0 and 6.1.0).
        Themes are few, so all pages are read and names matched here: theme_name has no
        unique constraint (models/core.py class Theme), and the API's only name search is
        a substring match over name and JSON alike (themes/filters.py ThemeAllTextFilter)."""
        out: list[dict] = []
        page = 0
        while page < 100:   # 10,000 themes: a runaway guard, not an expected ceiling
            rows = self.get("/api/v1/theme/", q={
                "columns": ["id", "uuid", "theme_name"], "page": page, "page_size": 100,
            })["result"]
            out += rows
            if len(rows) < 100:
                break
            page += 1
        return out

    # -- import / export ----------------------------------------------------

    def import_dashboard_bundle(self, zip_bytes: bytes, overwrite: bool = True) -> requests.Response:
        return self._send(lambda: self.session.post(
            f"{self.base_url}/api/v1/dashboard/import/",
            files={"formData": ("bundle.zip", zip_bytes, "application/zip")},
            data={"overwrite": json.dumps(overwrite)},
            # Without this, command errors can render the static HTML 500 page
            # instead of the 422 JSON body (docs/CONTRACTS.md).
            headers={"Accept": "application/json"},
            timeout=120,
        ))

    def export_dashboard(self, dashboard_id: int) -> bytes:
        r = self._send(lambda: self.session.get(
            f"{self.base_url}/api/v1/dashboard/export/",
            params={"q": json.dumps([dashboard_id])},
            timeout=120,
        ))
        self._raise_for(r, "dashboard export failed")
        return r.content

    def export_dataset(self, dataset_id: int) -> bytes:
        r = self._send(lambda: self.session.get(
            f"{self.base_url}/api/v1/dataset/export/",
            params={"q": json.dumps([dataset_id])},
            timeout=120,
        ))
        self._raise_for(r, "dataset export failed")
        return r.content

    # -- chart data (smoke) ---------------------------------------------------

    def chart_data(self, query_context: dict) -> requests.Response:
        return self.post_json("/api/v1/chart/data", query_context)

    # -- version -------------------------------------------------------------

    def superset_version(self) -> str | None:
        """The instance's Superset release, e.g. "5.0.0", or None when it doesn't say.
        Asked once per client.

        6.1.0 answers GET /version with JSON (superset/views/health.py:36-45, the
        health blueprint, no login). 4.1.4 and 5.0.0 have no such route; their
        sign-in page carries the version in its bootstrap data instead: FAB's
        login_db.html extends appbuilder/base.html, whose base_template is
        superset/base.html (superset/initialization/__init__.py:564 at 4.1.4, :559
        at 5.0.0), which extends appbuilder/baselayout.html, whose data-bootstrap
        (:45) holds common.menu_data.navbar_right.version_string
        (superset/views/base.py:282 at 4.1.4, :274 at 5.0.0). Both requests go out
        signed out, so the sign-in page renders instead of redirecting."""
        if not self._version_probed:
            self._version_probed = True
            self._version = self._probe_version()
        return self._version

    def _probe_version(self) -> str | None:
        from .versions import format_version, parse_version

        anon = requests.Session()
        anon.verify = self.session.verify

        def fetch(path: str, **headers) -> requests.Response | None:
            try:
                return self._send(lambda: anon.get(f"{self.base_url}{path}", headers=headers, timeout=30),
                                  relogin_on_401=False)
            except SupersetAPIError:
                return None

        r = fetch("/version", Accept="application/json")
        if r is not None and r.status_code == 200:
            try:
                release = parse_version((r.json() or {}).get("version_string"))
            except (ValueError, AttributeError):
                release = None
            if release:
                return format_version(release)
        r = fetch("/login/")
        if r is None or r.status_code != 200:
            return None
        m = _BOOTSTRAP_VERSION.search(html.unescape(r.text))
        release = parse_version(m.group(1)) if m else None
        return format_version(release) if release else None

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _raise_for(r: requests.Response, message: str) -> None:
        if r.status_code >= 400:
            raise SupersetAPIError(f"{message}: HTTP {r.status_code}", r.status_code, r.text[:2000])
