"""Presentation controls: points on a mixed chart, big-number text sizes and a
trendline's date format, axis-title spacing, named markdown blocks, links to a tab,
and per-chart cross-filter scopes. Every key emitted is checked against the Superset
source (paths in the code beside each); each round-trips through decompile and shows
up in `plan` when the spec value changes."""

import io
import json
import zipfile

import pytest
import yaml
from pydantic import ValidationError

from chartwright import dashdiff, ids
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

DS = {"database": "examples", "table": "orders"}
LINE = {"name": "Revenue", "type": "timeseries_line", "dataset": DS,
        "metrics": ["SUM(revenue)"], "time_column": "order_date", "time_grain": "P1M"}
KPI = {"name": "Orders", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"}
MIXED = {"name": "Volume and delays", "type": "mixed", "dataset": DS, "x_column": "order_date",
         "time_grain": "P1M", "a": {"metrics": ["COUNT(*)"]},
         "b": {"metrics": ["MAX(delay)"], "kind": "line", "axis": "secondary"}}


def _spec(charts=None, dashboard=None, layout=None):
    charts = charts or [KPI, LINE]
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "Sales", "slug": "sales", **(dashboard or {})},
        "charts": charts,
        "layout": layout or {"rows": [[c["name"]] for c in charts]},
    })


def _compiled(spec) -> bytes:
    return compile_bundle(spec, stub_resolution(spec))


def _docs(bundle: bytes) -> dict[str, dict]:
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    return {n: yaml.safe_load(zf.read(n)) for n in zf.namelist() if n.endswith(".yaml")}


def _dashboard(spec) -> dict:
    return next(d for n, d in _docs(_compiled(spec)).items() if "/dashboards/" in n)


def _params(spec) -> dict:
    return {d["slice_name"]: d["params"] for n, d in _docs(_compiled(spec)).items() if "/charts/" in n}


def _lookup(spec):
    by_uuid = {ds.uuid: {"database": ds.database_name, "schema": ds.schema, "table": ds.table}
               for ds in stub_resolution(spec).datasets.values()}
    return lambda u: by_uuid.get(u)


def _decompile(spec, edit=None):
    """Decompile `spec`'s bundle, after `edit(path, doc)` when given: what a UI user changed."""
    bundle = _compiled(spec)
    if edit is not None:
        bundle = edit_bundle(bundle, edit)
    return decompile_bundle(bundle, _lookup(spec))


def _edit_chart(name, **params):
    def edit(path, doc):
        if "/charts/" in path and doc["slice_name"] == name:
            doc["params"].update(params)
    return edit


def _edit_metadata(**metadata):
    def edit(path, doc):
        if "/dashboards/" in path:
            doc["metadata"].update(metadata)
    return edit


def _round_trips(spec):
    result = _decompile(spec)
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec)) == _normalize(spec)
    return result


def _plan(target, live, monkeypatch) -> dict:
    """`plan` against a live dashboard that is `live` compiled and exported."""
    import chartwright.resolver as resolver

    monkeypatch.setattr(resolver, "resolve", lambda s, c, *_: stub_resolution(s))
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: _decompile(live))

    class Client:
        def find_dashboard_by_slug(self, slug):
            return {"id": 1, "uuid": str(ids.dashboard_uuid(slug))}

    return json.loads(dashdiff.plan(target, Client()).to_json())


# -- a mixed chart's points ------------------------------------------------------------

def test_mixed_series_can_be_scatter(monkeypatch):
    """kind scatter writes seriesType / seriesTypeB "scatter" (MixedTimeseries/
    controlPanel.tsx seriesType choices; EchartsTimeseriesSeriesType.Scatter)."""
    points = {**MIXED, "b": {**MIXED["b"], "kind": "scatter"}}
    p = _params(_spec([points]))[MIXED["name"]]
    assert (p["seriesType"], p["seriesTypeB"]) == ("bar", "scatter")
    assert "areaB" not in p
    _round_trips(_spec([points]))
    plan = _plan(_spec([points]), _spec([MIXED]), monkeypatch)
    assert plan["charts_changed"] == [MIXED["name"]]
    # a UI chart's points read back as points, not as a line with a loss
    result = _decompile(_spec([MIXED]), _edit_chart(MIXED["name"], seriesType="scatter"))
    assert result.losses == [], result.losses_json()
    assert result.spec["charts"][0]["a"]["kind"] == "scatter"
