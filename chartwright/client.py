"""Thin Superset REST client. Auth = JWT login + CSRF token + session cookie
(Superset requires all three for mutating multipart endpoints).

Enterprise-network posture: every requests-level failure is converted into a
typed SupersetAPIError with a hint (TLS interception -> ca_bundle, proxy,
timeouts), login guards against SSO/proxy HTML pages, and an expired access
token triggers exactly one re-login + retry (long applies on big estates).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import requests


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
    session: requests.Session = field(default_factory=requests.Session)
    _csrf: str | None = None
    _logged_in: bool = False

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        # Corporate TLS interception: custom root CA, or (last resort) no verify.
        self.session.verify = self.ca_bundle if self.ca_bundle else self.verify

    # -- transport ------------------------------------------------------------

    def _send(self, fn: Callable[[], requests.Response], relogin_on_401: bool = True) -> requests.Response:
        """Run one request with typed network errors and a single 401 retry."""
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
        return r

    # -- auth ---------------------------------------------------------------

    def login(self) -> None:
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
                "or proxy login page intercepting the API. If this instance is SSO-only, "
                "the password login API is disabled (known limitation).",
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

    # -- generic ------------------------------------------------------------

    def get(self, path: str, **params) -> dict:
        q = {}
        if params:
            q["q"] = json.dumps(params.pop("q")) if "q" in params else None
            q = {k: v for k, v in q.items() if v is not None}
            q.update(params)
        r = self._send(lambda: self.session.get(f"{self.base_url}{path}", params=q, timeout=60))
        self._raise_for(r, f"GET {path} failed")
        return r.json()

    def post_json(self, path: str, payload: dict) -> requests.Response:
        return self._send(lambda: self.session.post(f"{self.base_url}{path}", json=payload, timeout=120))

    def put_json(self, path: str, payload: dict) -> requests.Response:
        return self.session.put(f"{self.base_url}{path}", json=payload, timeout=120)

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
