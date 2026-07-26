"""Batch C of the v2 roadmap: taxonomy contract, ignore validation and scoped
keys, severity overrides, calibration v2, geometry caching, fix disclosure,
width remainder, and the golden dogfood."""

import json
from pathlib import Path

import pytest

from chartwright.design import DESIGN_BRAIN_VERSION, advise, advise_and_fix
from chartwright.design.model import (AXIS_TYPES, KPI_TYPES, RULES, STANDALONE_TYPES)
from chartwright.design.presets import Overlay
from chartwright.spec import CHART_TYPES, load_spec

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()


def mk(charts, layout=None, design=None):
    data = {
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "t"},
        "charts": charts,
        "layout": layout or {"rows": [[c["name"]] for c in charts]},
    }
    if design is not None:
        data["design"] = design
    return data


def line(name, **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS,
            "metrics": ["COUNT(*)"], "time_column": "ts", "height": 8, **kw}


# -- 26: the taxonomy is exhaustive by contract ---------------------------------


def test_chart_type_taxonomy_is_exhaustive():
    """A 15th chart type must fail here until consciously classified."""
    assert KPI_TYPES | AXIS_TYPES | STANDALONE_TYPES == set(CHART_TYPES)
    assert not (KPI_TYPES & AXIS_TYPES) and not (AXIS_TYPES & STANDALONE_TYPES)


# -- 25: ignore validation + scoped band keys ------------------------------------


def test_typoed_ignore_is_surfaced_not_silent():
    data = mk([line("L", height=3)], design={"ignore": ["size.axis-minheight"]})
    rep = advise(load_spec(data), overlay=EMPTY)
    assert rep.unmatched_ignores == ["size.axis-minheight"]
    assert "unmatched_ignores" in rep.payload()
    assert any(f.rule == "size.axis-min-height" for f in rep.findings)  # typo suppressed nothing


def test_band_findings_accept_scoped_ignore_keys():
    charts = [line("A"), {"type": "big_number_total", "name": "K", "dataset": DS,
                          "metric": "COUNT(*)", "number_format": ",.0f"}]
    layout = {"rows": [["A"], ["K"]]}
    rep = advise(load_spec(mk(charts, layout)), overlay=EMPTY)
    band = next(f for f in rep.findings if f.rule == "layout.kpi-first")
    rep2 = advise(load_spec(mk(charts, layout, design={"ignore": [band.scope_key]})),
                  overlay=EMPTY)
    assert not any(f.rule == "layout.kpi-first" for f in rep2.findings)
    assert band.scope_key in rep2.ignored


# -- 27: version constant, since metadata, golden dogfood --------------------------


def test_version_constant_flows_to_payload():
    rep = advise(load_spec(mk([line("L")])), overlay=EMPTY)
    assert rep.payload()["design_brain"] == DESIGN_BRAIN_VERSION


def test_every_rule_carries_a_known_since_version():
    """Tied to the version constant, not a hand-listed tuple, so bumping the
    rulebook does not need an edit here -- but a typo'd `since` still fails."""
    known = {str(v) for v in range(1, int(DESIGN_BRAIN_VERSION) + 1)}
    assert all(r.since in known for r in RULES.values()), {
        r.id: r.since for r in RULES.values() if r.since not in known}
    assert any(r.since == "2" for r in RULES.values())
    assert any(r.since == DESIGN_BRAIN_VERSION for r in RULES.values())


def test_golden_dogfood_example_advises_clean():
    example = json.loads(
        (Path(__file__).parent.parent / "examples" / "nyc_taxi_operations.json")
        .read_text(encoding="utf-8"))
    rep = advise(load_spec(example), overlay=EMPTY)
    assert rep.findings == [], [f.key for f in rep.findings]


def test_advise_never_raises_on_mutated_specs():
    """Seeded fuzz over the geometry axes advise reasons about."""
    base = mk([line("L"), line("M", groupby="region"),
               {"type": "pie", "name": "P", "dataset": DS, "metric": "COUNT(*)",
                "groupby": "region"},
               {"type": "big_number_total", "name": "K", "dataset": DS,
                "metric": "COUNT(*)"}])
    for h in (None, 1, 3.4, 8, 100):
        for w in (None, 1, 3, 12):
            data = json.loads(json.dumps(base))
            for c in data["charts"]:
                if h is None:
                    c.pop("height", None)
                else:
                    c["height"] = h
                if w is not None:
                    c["width"] = w
            if w == 12:
                data["layout"] = {"rows": [[c["name"]] for c in data["charts"]]}
            else:
                data["layout"] = {"rows": [[c["name"] for c in data["charts"]]]}
            advise(load_spec(data), overlay=EMPTY)  # must never raise


# -- 28: severity overrides ---------------------------------------------------------


def test_overlay_severity_override_gates():
    data = mk([line("L", height=3)])
    ov = Overlay(severity={"size.axis-min-height": "error"})
    rep = advise(load_spec(data), overlay=ov)
    f = next(f for f in rep.findings if f.rule == "size.axis-min-height")
    assert f.severity == "error" and not rep.ok


def test_overlay_severity_file_validation(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    from chartwright.design.presets import load_overlay

    (tmp_path / "design.yaml").write_text(yaml.safe_dump(
        {"severity": {"size.nope": "error"}}))
    with pytest.raises(ValueError, match="unknown rule"):
        load_overlay()
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(
        {"severity": {"size.min-width": "fatal"}}))
    with pytest.raises(ValueError, match="error|warn|info"):
        load_overlay()


# -- 30: geometry caching -------------------------------------------------------------


def test_sketch_parsed_once_per_holder():
    from chartwright.sketch import parse_sketch_cached

    charts = [line(f"L{i}") for i in range(6)]
    for c in charts:
        c.pop("height")
    legend = {chr(65 + i): f"L{i}" for i in range(6)}
    sketch = ["".join(chr(65 + i) * 2 for i in range(6))] * 4
    data = mk(charts, layout={"sketch": sketch, "legend": legend})
    parse_sketch_cached.cache_clear()
    advise(load_spec(data), overlay=EMPTY)
    info = parse_sketch_cached.cache_info()
    assert info.misses == 1, info  # one holder, one parse; everything else hits


# -- 31/33: fix disclosure + width remainder --------------------------------------------


def test_fixed_entries_disclose_old_and_new():
    data = mk([line("L", height=3)])
    _, rep = advise_and_fix(data, overlay=EMPTY)
    entry = next(e for e in rep.fixed if e["rule"] == "size.axis-min-height")
    assert entry["chart"] == "L" and entry["was"] == {"height": 3}
    assert entry["set"]["height"] >= 6


def test_implicit_width_remainder_distributed():
    charts = [line(f"L{i}") for i in range(5)]
    spec = load_spec(mk(charts, layout={"rows": [[c["name"] for c in charts]]}))
    widths = [spec.resolved_item_width(c["name"]) for c in charts]
    assert sum(widths) == 12 and widths == [3, 3, 2, 2, 2]
    rep = advise(spec, overlay=EMPTY)
    assert not any(f.rule == "layout.row-fill" for f in rep.findings)


def test_equal_markdown_blocks_do_not_overflow_the_row():
    """Markdown blocks compare by VALUE, so every identical block used to
    resolve to the first one's index, take the remainder's +1, and push the
    row past 12 columns -- a row Superset cannot lay out."""
    md = {"markdown": "---", "height": 2}
    spec = load_spec(mk([line("L")], layout={"rows": [["L", *(dict(md) for _ in range(4))]]}))
    widths = [spec.resolved_item_width(i) for i in spec.layout.rows[0]]
    assert sum(widths) == 12, widths
    assert widths == [3, 3, 2, 2, 2], widths


# -- review: the polish skip is an INFERRED signal, so it must be reported ---------


def test_human_polished_skip_is_disclosed_not_silent():
    """A fractional height means "a human sized this in the UI", so sizing
    rules stand down. Silence there is indistinguishable from passing, so the
    withheld finding is named in `polished`."""
    spec = load_spec(mk([line("Polished", height=2.4)]))
    rep = advise(spec, overlay=EMPTY)
    assert not any(f.rule == "size.axis-min-height" for f in rep.findings)
    assert rep.polished == ["size.axis-min-height@Polished"]
    assert rep.payload()["polished"] == ["size.axis-min-height@Polished"]


def test_polished_absent_when_nothing_was_withheld():
    """Payload stays clean when the heuristic never fired."""
    assert "polished" not in advise(load_spec(mk([line("L")])), overlay=EMPTY).payload()


def test_sketch_height_fix_disclosed():
    charts = [line("A"), line("B")]
    for c in charts:
        c.pop("height")
    data = mk(charts, layout={"sketch": ["AAAAAABBBBBB"], "legend": {"A": "A", "B": "B"},
                              "line": 2})
    rep = advise(load_spec(data), overlay=EMPTY)
    f = next(f for f in rep.findings if f.rule == "size.axis-min-height")
    assert "overrides the sketch" in f.detail
