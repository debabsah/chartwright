"""Thin Superset REST client. Auth = JWT login + CSRF token + session cookie
(Superset requires all three for mutating multipart endpoints).

Enterprise-network posture: every requests-level failure is converted into a
typed SupersetAPIError with a hint (TLS interception -> ca_bundle, proxy,
timeouts), login guards against SSO/proxy HTML pages, and an expired access
token triggers exactly one re-login + retry (long applies on big estates).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

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


class SupersetAPIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


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
              tries_429: int = 3) -> requests.Response:
        """Run one request with typed network errors, a single 401 retry, and
        polite 429 backoff (Superset ships a rate limiter; corporate gateways
        add their own)."""
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

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _raise_for(r: requests.Response, message: str) -> None:
        if r.status_code >= 400:
            raise SupersetAPIError(f"{message}: HTTP {r.status_code}", r.status_code, r.text[:2000])
