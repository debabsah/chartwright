"""Saved queries for the charts a spec builds (triage Q).

A chart's saved query (`query_context`) is what Superset's CSV and text chart reports
and `GET /api/v1/chart/{id}/data` run. Superset's frontend builds it in each chart
plugin's buildQuery, with the plugin's own post-processing (pivots, contribution,
rolling windows, histograms), and stores it when someone saves the chart in Explore.
An imported chart has none, so those reports fail on every chart the tool creates.

Rather than port every plugin's buildQuery for every release, this opens each chart in
Explore in a headless Chromium (the optional `chartwright[visual]` extra), captures the
query Superset's own frontend sends to `/api/v1/chart/data`, and stores it on the chart,
which is what Save does: Explore's save writes `buildV1ChartDataPayload(formData,
force=false, resultFormat='json', resultType='full')` (src/explore/actions/
saveModalActions.ts, 4.1.4 `:169`, 6.1.0 `:162`), the same builder its data request
uses. Each saved query is then run through `GET /api/v1/chart/{id}/data`, the endpoint
reports use, before it counts as saved.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .visible import INSTALL, VisibleError, VisualUnavailable


@dataclass
class SavedQuery:
    chart: str
    chart_id: int
    ok: bool
    detail: str = ""
    rows: int | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in ("", None)}


def _playwright():
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError as e:
        raise VisualUnavailable(
            f"saving chart queries needs a browser, which chartwright installs only on "
            f"request: {INSTALL}") from e
    return sync_playwright, Error, TimeoutError


def normalized(payload: dict) -> dict:
    """The captured data request as Save stores it: not forced, JSON, the full result."""
    return {**payload, "force": False, "result_format": "json", "result_type": "full"}


def capture(base_url: str, username: str, password: str, charts: dict[str, int], *,
            timeout_s: float = 60.0, verify_tls: bool = True) -> dict[str, dict | str]:
    """Chart name -> the query Superset's frontend builds for it, or why none was seen.
    One browser, one sign-in, each chart opened in Explore in turn."""
    sync_playwright, pw_error, pw_timeout = _playwright()
    out: dict[str, dict | str] = {}
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:  # noqa: BLE001 - Playwright's own error for a missing browser
                raise VisualUnavailable(f"Chromium could not start ({str(e).splitlines()[0]}); "
                                        f"install it with: playwright install chromium") from e
            try:
                page = browser.new_context(ignore_https_errors=not verify_tls).new_page()
                page.set_default_timeout(timeout_s * 1000)
                page.goto(f"{base_url}/login/")
                page.wait_for_selector("input[type=password]")
                page.locator("input:not([type=password]):not([type=hidden])").first.fill(username)
                page.fill("input[type=password]", password)
                page.keyboard.press("Enter")
                page.wait_for_load_state("networkidle")
                if "/login" in page.url:
                    raise VisibleError("saved_query_login",
                                       f"signing in to {base_url} as {username!r} failed")
                for name, chart_id in charts.items():
                    out[name] = _one(page, base_url, chart_id, timeout_s)
            finally:
                browser.close()
    except pw_timeout as e:
        raise VisibleError("saved_query_timeout", (
            f"Superset at {base_url} did not answer within {timeout_s:g} s "
            f"({str(e).splitlines()[0]}); pass a longer --timeout")) from e
    except pw_error as e:
        first = str(e).splitlines()[0] if str(e) else type(e).__name__
        if "CERT" in first.upper() or "SSL" in first.upper():
            raise VisibleError("saved_query_tls", (
                f"the browser does not trust {base_url}'s certificate ({first}); it checks "
                f"against the operating system's certificates, so install the CA there, or "
                f"set verify = false in the profile to skip the check")) from e
        raise VisibleError("saved_query_browser", f"the browser failed at {base_url}: {first}") from e
    return out


def _one(page, base_url: str, chart_id: int, timeout_s: float) -> dict | str:
    """The first full-result data request Explore sends for this chart."""
    seen: list[dict] = []

    def on_request(req):
        if req.method == "POST" and "/api/v1/chart/data" in req.url:
            try:
                body = json.loads(req.post_data or "{}")
            except ValueError:
                return
            if isinstance(body, dict) and body.get("queries") and body.get("result_type") in (None, "full"):
                seen.append(body)

    page.on("request", on_request)
    try:
        page.goto(f"{base_url}/explore/?slice_id={chart_id}")
        page.wait_for_load_state("networkidle")
        waited = 0
        while not seen and waited < timeout_s * 1000:
            page.wait_for_timeout(250)
            waited += 250
    finally:
        page.remove_listener("request", on_request)
    return seen[0] if seen else "Explore sent no data request for this chart"


def save(client, charts: dict[str, int], captured: dict[str, dict | str]) -> list[SavedQuery]:
    """Store each captured query on its chart, then run it as reports do."""
    results = []
    for name, chart_id in charts.items():
        got = captured.get(name, "not captured")
        if not isinstance(got, dict):
            results.append(SavedQuery(name, chart_id, False, got))
            continue
        r = client.put_json(f"/api/v1/chart/{chart_id}",
                            {"query_context": json.dumps(normalized(got))})
        if r.status_code != 200:
            results.append(SavedQuery(name, chart_id, False,
                                      f"saving the query: HTTP {r.status_code} {r.text[:200]}"))
            continue
        try:
            data = client.get(f"/api/v1/chart/{chart_id}/data/")
        except Exception as e:  # noqa: BLE001 - SupersetAPIError or a bad body: report it
            results.append(SavedQuery(name, chart_id, False, f"the saved query did not run: {e}"))
            continue
        rows = sum(len(q.get("data") or []) for q in data.get("result") or [])
        results.append(SavedQuery(name, chart_id, True, rows=rows))
    return results


def save_queries(client, profile, chart_ids: dict[str, int], *, timeout_s: float = 60.0) -> dict:
    """Capture and store the saved query of each chart; the payload names each one."""
    from .visible import tls_for

    if getattr(profile, "api_token", None):
        return {"ok": False, "stage": "saved_queries", "errors": [{
            "code": "saved_query_needs_password",
            "detail": "saving chart queries signs in to Superset's own sign-in page with a "
                      "username and password; a Preset API token opens no browser session"}]}
    verify_tls, tls_note = tls_for(profile)
    try:
        captured = capture(profile.base_url.rstrip("/"), profile.username, profile.password,
                           chart_ids, timeout_s=timeout_s, verify_tls=verify_tls)
    except VisualUnavailable as e:
        return {"ok": False, "stage": "saved_queries",
                "errors": [{"code": "visual_extra_missing", "detail": str(e)}]}
    except VisibleError as e:
        return {"ok": False, "stage": "saved_queries", "errors": [{"code": e.code, "detail": str(e)}]}
    results = save(client, chart_ids, captured)
    out = {"ok": all(r.ok for r in results), "stage": "saved_queries",
           "charts": [r.as_dict() for r in results]}
    if tls_note:
        out["tls"] = tls_note
    return out
