"""Saved queries (triage Q): the query Superset's frontend builds for a chart is captured
in a headless browser and stored on the chart, as Explore's Save does, so CSV and text
reports work. The browser part is live-only (tools/ci_live_saved_queries.py); these
cover what is stored, how each outcome is reported, and the refusals."""

import json
from types import SimpleNamespace

import chartwright.savedqueries as sq
from chartwright.visible import VisualUnavailable


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text = status_code, text


class _Client:
    def __init__(self, put_status=200, data=None, data_error=None):
        self.put_status, self.data, self.data_error = put_status, data, data_error
        self.puts = []

    def put_json(self, path, payload):
        self.puts.append((path, payload))
        return _Resp(self.put_status, "nope")

    def get(self, path, **params):
        if self.data_error:
            raise self.data_error
        return self.data or {"result": [{"data": [{"x": 1}, {"x": 2}]}]}


CAPTURED = {"datasource": {"id": 5, "type": "table"}, "force": True,
            "queries": [{"metrics": ["count"], "post_processing": [{"operation": "pivot"}]}],
            "form_data": {"viz_type": "echarts_timeseries_line"},
            "result_format": "csv", "result_type": "samples"}


def test_the_stored_query_is_what_save_stores():
    """Explore's Save: buildV1ChartDataPayload(force false, json, full)."""
    got = sq.normalized(CAPTURED)
    assert (got["force"], got["result_format"], got["result_type"]) == (False, "json", "full")
    assert got["queries"] == CAPTURED["queries"] and got["form_data"] == CAPTURED["form_data"]


def test_each_chart_is_saved_then_run_as_reports_run_it():
    client = _Client()
    out = sq.save(client, {"Trend": 11}, {"Trend": CAPTURED})
    assert [r.as_dict() for r in out] == [{"chart": "Trend", "chart_id": 11, "ok": True, "rows": 2}]
    (path, payload), = client.puts
    assert path == "/api/v1/chart/11"
    assert json.loads(payload["query_context"]) == sq.normalized(CAPTURED)


def test_every_failure_is_named_per_chart():
    nothing = sq.save(_Client(), {"A": 1}, {"A": "Explore sent no data request for this chart"})
    assert not nothing[0].ok and "no data request" in nothing[0].detail
    refused = sq.save(_Client(put_status=403), {"A": 1}, {"A": CAPTURED})
    assert not refused[0].ok and "HTTP 403" in refused[0].detail
    broken = sq.save(_Client(data_error=RuntimeError("400 no query context")), {"A": 1},
                     {"A": CAPTURED})
    assert not broken[0].ok and "did not run" in broken[0].detail


def test_without_the_visual_extra_it_says_how_to_install_it(monkeypatch):
    def missing():
        raise VisualUnavailable("saving chart queries needs a browser: pip install ...")
    monkeypatch.setattr(sq, "_playwright", missing)
    profile = SimpleNamespace(api_token=None, base_url="http://s", username="u", password="p",
                              ca_bundle=None, verify=True)
    out = sq.save_queries(_Client(), profile, {"A": 1})
    assert not out["ok"] and out["errors"][0]["code"] == "visual_extra_missing"


def test_a_preset_api_token_cannot_open_a_browser_session():
    profile = SimpleNamespace(api_token="t", base_url="http://s", username=None, password=None)
    out = sq.save_queries(_Client(), profile, {"A": 1})
    assert out["errors"][0]["code"] == "saved_query_needs_password"


def test_only_the_full_result_request_is_captured():
    """Explore can send other data requests (samples, results panes); only the chart's
    own full-result query is the saved one."""
    class Page:
        def __init__(self):
            self.handler = None

        def on(self, event, fn):
            self.handler = fn

        def remove_listener(self, event, fn):
            self.handler = None

        def goto(self, url):
            for body in ({"queries": [{}], "result_type": "samples"},
                         {"queries": [{"metrics": ["count"]}], "result_type": "full"}):
                self.handler(SimpleNamespace(method="POST", url="http://s/api/v1/chart/data",
                                             post_data=json.dumps(body)))

        def wait_for_load_state(self, state):
            pass

        def wait_for_timeout(self, ms):
            pass
    got = sq._one(Page(), "http://s", 7, timeout_s=1)
    assert got["result_type"] == "full" and got["queries"] == [{"metrics": ["count"]}]
