"""Native time filter (filter_time): an optional default pre-fills the picker.
The default hydrates only when defaultDataMask carries BOTH extraFormData and
filterState (TimeFilterPlugin reads filterState.value for the pill; queries
read extraFormData.time_range), and a tool-born default round-trips through
decompile with zero losses."""

import io
import json
import zipfile

import yaml

from chartwright.compiler import compile_bundle
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

DS = {"database": "examples", "table": "t"}


def _spec(filters):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [
            {"name": "A", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
        ],
        "filters": filters,
        "layout": {"rows": [["A"]]},
    })


def _native_filter_config(spec):
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    dash = next(n for n in zf.namelist() if "/dashboards/" in n)
    meta = yaml.safe_load(zf.read(dash))["metadata"]
    return json.loads(json.dumps(meta["native_filter_configuration"]))


def _stub_lookup_for(spec):
    res = stub_resolution(spec)
    by_uuid = {}
    for chart in spec.charts:
        ds = res.for_chart(chart.dataset)
        by_uuid[ds.uuid] = {
            "database": chart.dataset.database,
            "schema": chart.dataset.schema_,
            "table": chart.dataset.table,
        }
    return lambda u: by_uuid.get(u)


def test_default_emits_both_mask_halves():
    (nf,) = _native_filter_config(_spec([
        {"type": "time_range", "name": "Date", "default": "2026-05-01 : 2026-06-01"},
    ]))
    assert nf["filterType"] == "filter_time"
    assert nf["defaultDataMask"] == {
        "extraFormData": {"time_range": "2026-05-01 : 2026-06-01"},
        "filterState": {"value": "2026-05-01 : 2026-06-01"},
        "ownState": {},
    }


def test_no_default_emits_empty_mask():
    (nf,) = _native_filter_config(_spec([{"type": "time_range", "name": "Date"}]))
    assert nf["filterType"] == "filter_time"
    assert nf["defaultDataMask"] == {"extraFormData": {}, "filterState": {}, "ownState": {}}


def test_default_round_trips_loss_free():
    spec = _spec([{"type": "time_range", "name": "Date", "default": "Last month"}])
    bundle = compile_bundle(spec, stub_resolution(spec))
    result = decompile_bundle(bundle, _stub_lookup_for(spec))
    assert result.losses == [], [loss.as_dict() for loss in result.losses]
    assert {"type": "time_range", "name": "Date", "default": "Last month"} in result.spec["filters"]
