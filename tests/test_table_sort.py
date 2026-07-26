"""Table `sort_by` must actually sort.

It used to compile to `order_desc: true` and nothing else -- a direction with
no column to apply it to -- so a "top 10" table returned 10 arbitrary rows.
Three symptoms, one field: the compiler dropped it, the resolver never checked
it (a typo passed `check`), and `plan` reported the chart changed forever
because decompile could never produce a field the compiler never wrote.
"""

import json

from chartwright.compiler import _chart_params, compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.resolver import Resolution, ResolvedDataset, _check_chart_fields
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

DS = {"database": "db", "table": "orders"}


def mk(chart):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "t"},
        "charts": [chart],
        "layout": {"rows": [[chart["name"]]]},
    })


def params_for(chart):
    spec = mk(chart)
    return _chart_params(spec.charts[0], spec, stub_resolution(spec))


def aggregate(**over):
    return {"name": "Top Regions", "type": "table", "dataset": DS,
            "metrics": ["SUM(v)"], "groupby": ["region"], "row_limit": 10, **over}


def raw(**over):
    return {"name": "Recent", "type": "table", "dataset": DS,
            "columns": ["region", "v"], "row_limit": 10, **over}


def _lookup(spec):
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    return lambda u: {"database": DS["database"], "schema": None,
                      "table": DS["table"]} if u == ds.uuid else None


# -- compile ------------------------------------------------------------------


def test_aggregate_sort_emits_the_sort_metric():
    p = params_for(aggregate(sort_by="SUM(v)"))
    sort = p["timeseries_limit_metric"]
    assert sort["aggregate"] == "SUM" and sort["column"]["column_name"] == "v"
    assert p["order_desc"] is True


def test_raw_sort_emits_order_by_cols():
    p = params_for(raw(sort_by="v"))
    assert p["order_by_cols"] == [json.dumps(["v", False])]   # False = descending
    assert p["order_desc"] is True


def test_no_sort_by_emits_no_sort_keys():
    """Off is off: specs without sort_by compile exactly as before."""
    p = params_for(aggregate())
    assert "timeseries_limit_metric" not in p and "order_by_cols" not in p
    assert "order_desc" not in p


# -- resolve ------------------------------------------------------------------


def _errors(chart, columns=("region", "v")):
    ds = ResolvedDataset(1, "u", "orders", None, "db", list(columns), [], None)
    res = Resolution()
    _check_chart_fields(mk(chart).charts[0], ds, res)
    return [e.code for e in res.errors]


def test_typo_in_aggregate_sort_by_is_a_resolution_error():
    assert _errors(aggregate(sort_by="SUM(nope)")) == ["column_not_found"]
    assert _errors(aggregate(sort_by="not_a_metric")) == ["metric_not_found"]


def test_typo_in_raw_sort_by_is_a_resolution_error():
    assert _errors(raw(sort_by="nope")) == ["column_not_found"]


def test_valid_sort_by_resolves_clean():
    assert _errors(aggregate(sort_by="SUM(v)")) == []
    assert _errors(raw(sort_by="region")) == []


# -- round trip / plan ---------------------------------------------------------


def test_sort_by_survives_compile_decompile():
    for chart in (aggregate(sort_by="SUM(v)"), raw(sort_by="v")):
        spec = mk(chart)
        result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _lookup(spec))
        assert result.losses == [], [x.as_dict() for x in result.losses]
        assert result.spec["charts"][0]["sort_by"] == chart["sort_by"]


def test_plan_does_not_drift_on_a_sorted_table():
    """The user-visible symptom: `plan` never returned clean, so the CI drift
    gate this tool advertises could not be used on any table with a sort."""
    spec = mk(aggregate(sort_by="SUM(v)"))
    live = load_spec(decompile_bundle(
        compile_bundle(spec, stub_resolution(spec)), _lookup(spec)).spec)
    assert _normalize(live)["charts"] == _normalize(spec)["charts"]


def test_ascending_live_sort_is_reported_as_a_loss():
    """The spec sorts descending; an ascending live sort is NAMED, not
    silently flipped on the next apply."""
    spec = mk(aggregate(sort_by="SUM(v)"))
    bundle = compile_bundle(spec, stub_resolution(spec))
    import io
    import zipfile

    import yaml

    zf = zipfile.ZipFile(io.BytesIO(bundle))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as w:
        for n in zf.namelist():
            blob = zf.read(n)
            if "/charts/" in n:
                cy = yaml.safe_load(blob)
                cy["params"]["order_desc"] = False
                blob = yaml.safe_dump(cy).encode()
            w.writestr(n, blob)
    result = decompile_bundle(out.getvalue(), _lookup(spec))
    assert any("ascending sort not preserved" in x.what for x in result.losses)
