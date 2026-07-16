"""Data-aware rules (phase 2): column types from resolution, cardinality via
a fake prober; offline runs skip them; probes failing degrade to silence."""

from chartwright.design import advise
from chartwright.design.presets import Overlay
from chartwright.resolver import ResolvedDataset, Resolution
from chartwright.spec import load_spec

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()


class FakeProber:
    """Canned cardinalities; None simulates a failed probe."""

    def __init__(self, counts):
        self.counts = counts

    def count_up_to(self, ds, column, cap):
        n = self.counts.get(column)
        return None if n is None else min(n, cap + 1)

    def more_than(self, ds, column, n):
        c = self.count_up_to(ds, column, n)
        return None if c is None else c > n


def resolution(**col_types):
    ds = ResolvedDataset(
        id=1, uuid="u", table="orders", schema=None, database_name="db",
        columns=list(col_types), metrics=[], main_dttm_col="ts",
        column_types={k: v for k, v in col_types.items() if v is not None},
        temporal_columns=[k for k, v in col_types.items() if v == 2],
    )
    res = Resolution()
    res.datasets["db//orders"] = ds
    return res


def mk(charts, filters=None):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "t"},
        "charts": charts,
        "filters": filters or [{"type": "time_range", "name": "W", "default": "Last year"}],
        "layout": {"rows": [[c["name"]] for c in charts]},
    })


def line(name, **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS,
            "metrics": ["COUNT(*)"], "time_column": "ts", "height": 8, **kw}


def test_temporal_type_error_and_unknown_skips():
    spec = mk([line("L")])
    rep = advise(spec, resolution=resolution(ts=1), overlay=EMPTY)  # ts is a string
    f = next(f for f in rep.findings if f.rule == "chart.temporal-type")
    assert f.severity == "error" and not rep.ok
    assert not advise(spec, resolution=resolution(ts=2), overlay=EMPTY).findings
    # no type metadata -> no verdict
    assert not advise(spec, resolution=resolution(ts=None), overlay=EMPTY).findings


def test_data_aware_rules_skipped_offline():
    spec = mk([line("L", groupby="region")])
    assert not any(f.rule == "chart.series-limit"
                   for f in advise(spec, overlay=EMPTY).findings)


def test_cardinality_downgrades_pie_and_vbar():
    pie = {"type": "pie", "name": "P", "dataset": DS, "metric": "COUNT(*)",
           "groupby": "region", "width": 6, "height": 8}
    bar = {"type": "bar", "name": "B", "dataset": DS, "x_column": "region",
           "metrics": ["COUNT(*)"], "height": 8}
    spec = mk([pie, bar])
    res = resolution(ts=2, region=1)
    low = advise(spec, resolution=res, prober=FakeProber({"region": 4}), overlay=EMPTY)
    assert not any(f.rule in ("chart.pie-slices", "chart.vbar-categories")
                   for f in low.findings)
    high = advise(spec, resolution=res, prober=FakeProber({"region": 40}), overlay=EMPTY)
    assert {"chart.pie-slices", "chart.vbar-categories"} <= {f.rule for f in high.findings}
    # a failed probe neither fires nor silences beyond the offline behavior
    broken = advise(spec, resolution=res, prober=FakeProber({}), overlay=EMPTY)
    assert {"chart.pie-slices", "chart.vbar-categories"} <= {f.rule for f in broken.findings}


def test_series_funnel_heatmap_probes():
    ts = line("L", groupby="customer")
    funnel = {"type": "funnel", "name": "F", "dataset": DS, "metric": "COUNT(*)",
              "groupby": "stage", "height": 8}
    heat = {"type": "heatmap", "name": "H", "dataset": DS, "metric": "COUNT(*)",
            "x_column": "a", "y_column": "b", "width": 7, "height": 8}
    spec = mk([ts, funnel, heat])
    prober = FakeProber({"customer": 50, "stage": 2, "a": 25, "b": 25})
    rep = advise(spec, resolution=resolution(ts=2), prober=prober, overlay=EMPTY)
    fired = {f.rule for f in rep.findings}
    assert {"chart.series-limit", "chart.funnel-stages", "chart.heatmap-grid"} <= fired


def test_heatmap_grid_saturated_side_reprobed():
    # 6,000 x 10 must not pass because the first probe saturates at 31.
    heat = {"type": "heatmap", "name": "H", "dataset": DS, "metric": "COUNT(*)",
            "x_column": "a", "y_column": "b", "width": 7, "height": 8}
    spec = mk([heat])
    rep = advise(spec, resolution=resolution(ts=2),
                 prober=FakeProber({"a": 6000, "b": 10}), overlay=EMPTY)
    f = next(f for f in rep.findings if f.rule == "chart.heatmap-grid")
    assert "at least" in f.detail
    # 25 x 10 = 250 genuinely fits
    rep = advise(spec, resolution=resolution(ts=2),
                 prober=FakeProber({"a": 25, "b": 10}), overlay=EMPTY)
    assert not any(f.rule == "chart.heatmap-grid" for f in rep.findings)


def test_heatmap_width_requirement_rises_with_cardinality():
    heat = {"type": "heatmap", "name": "H", "dataset": DS, "metric": "COUNT(*)",
            "x_column": "a", "y_column": "b", "width": 6, "height": 8}
    spec = mk([heat])
    res = resolution(ts=2)
    ok = advise(spec, resolution=res, prober=FakeProber({"a": 5, "b": 5}), overlay=EMPTY)
    assert not any(f.rule == "size.heatmap-geometry" for f in ok.findings)
    wide = advise(spec, resolution=res, prober=FakeProber({"a": 20, "b": 5}), overlay=EMPTY)
    assert any(f.rule == "size.heatmap-geometry" for f in wide.findings)
