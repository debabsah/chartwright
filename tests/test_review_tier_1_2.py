"""Review burn-down, tiers 1 and 2: places the tool contradicted itself, plus
the coverage gap that undercut the product claim.
"""

import inspect
import json
import re

import pytest

from chartwright.design import advise
from chartwright.design.model import RULES, SEVERITY_RANK
from chartwright.design.presets import Overlay
from chartwright.spec import load_spec

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()


def mk(charts, filters=None):
    data = {"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
            "charts": charts, "layout": {"rows": [[c["name"] for c in charts]]}}
    if filters:
        data["filters"] = filters
    return load_spec(data)


def ts(name, **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS,
            "metrics": ["COUNT(*)"], "time_column": "ts", "height": 8, **kw}


def rules_of(spec):
    return sorted(f.rule for f in advise(spec, overlay=EMPTY).findings)


# -- 1.3 / 1.6: severity truth ---------------------------------------------------


def test_declared_severities_cover_what_each_rule_can_emit():
    """`ok` is error-driven, so a rule advertising `warn` while emitting
    `error` understates the one case that fails a run. Checked against the
    rule's own source: a new escalation fails here until it is declared."""
    literal = re.compile(r'"(error|warn|info)"')
    undeclared = {}
    for rid, r in RULES.items():
        emitted = set(literal.findall(inspect.getsource(r.fn)))
        missing = emitted - set(r.severities)
        if missing:
            undeclared[rid] = sorted(missing)
    assert not undeclared, (
        f"add severities=(...) to @rule for these: {undeclared}")


def test_severity_label_shows_escalation_default_first():
    assert RULES["size.min-width"].severity_label == "warn/error"
    assert RULES["layout.row-fill"].severity_label == "warn/info"
    assert RULES["chart.dupe"].severity_label == "info"


def test_escalating_rules_really_do_escalate():
    """The declaration is not decoration: min-width yields a hard error."""
    spec = mk([ts("A", width=1), ts("B", width=11)])
    errs = [f for f in advise(spec, overlay=EMPTY).findings
            if f.rule == "size.min-width" and f.severity == "error"]
    assert errs and advise(spec, overlay=EMPTY).ok is False


def test_every_rule_declares_a_sane_severity_set():
    for r in RULES.values():
        assert r.severities and r.severities[0] == r.severity
        assert set(r.severities) <= set(SEVERITY_RANK)


# -- 2.1: the unwindowed-history coverage gap ------------------------------------


def test_no_time_filter_at_all_is_flagged_once_for_the_dashboard():
    """The commonest real failure: nothing bounds the window, so every load
    draws the dataset's full history at daily grain."""
    spec = mk([ts("A"), ts("B"), ts("C")])
    found = [f for f in advise(spec, overlay=EMPTY).findings
             if f.rule == "data.unwindowed-history"]
    assert len(found) == 1, "one dashboard-level finding, not one per chart"
    assert found[0].chart is None and found[0].severity == "warn"
    for name in ("A", "B", "C"):
        assert name in found[0].detail


@pytest.mark.parametrize("charts,filters", [
    # a defaulted picker bounds the load
    ([ts("A")], [{"type": "time_range", "name": "D", "default": "Last month"}]),
    # an undefaulted picker is filters.time-default's job, not a second warn
    ([ts("A")], [{"type": "time_range", "name": "D"}]),
    # a coarse grain is not a point explosion
    ([ts("A", time_grain="P1M")], None),
    # the chart bounds itself
    ([ts("A", time_range="Last month")], None),
])
def test_unwindowed_history_stays_silent_when_covered(charts, filters):
    assert "data.unwindowed-history" not in rules_of(mk(charts, filters))


def test_undefaulted_picker_is_reported_exactly_once_by_the_other_rule():
    """No double-reporting of one remedy at two severities."""
    got = rules_of(mk([ts("A")], [{"type": "time_range", "name": "D"}]))
    assert got == ["filters.time-default"]


def test_shipped_example_still_advises_clean():
    """The golden dogfood must stay clean HONESTLY, not by suppression."""
    from pathlib import Path

    ex = json.loads((Path(__file__).resolve().parent.parent / "examples"
                     / "nyc_taxi_operations.json").read_text(encoding="utf-8"))
    rep = advise(load_spec(ex), overlay=EMPTY)
    assert rep.counts == {"error": 0, "warn": 0, "info": 0}, [f.key for f in rep.findings]


# -- 1.1: advise --strict names its gate ------------------------------------------


def test_advise_strict_gate_is_machine_readable():
    """`ok` stays error-driven by contract, so without this the exit code was
    the only signal that --strict blocked."""
    from chartwright.cli import _main

    spec_warn = mk([ts("A", height=2)])            # below min axis height -> warn
    rep = advise(spec_warn, overlay=EMPTY)
    assert rep.counts["warn"] and rep.ok is True   # ok unchanged, per DESIGN-BRAIN 10
    assert rep.gate(strict=True) and not rep.gate(strict=False)
    assert _main is not None


def test_advise_strict_emits_design_gate_entry(tmp_path, capsys, monkeypatch):
    from chartwright.cli import main

    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
        "charts": [ts("A", height=2)], "layout": {"rows": [["A"]]}}), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(["advise", str(spec), "--strict"])
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True                       # error-driven, unchanged
    assert [e["code"] for e in payload["errors"]] == ["design_gate"]
    assert "--strict" in payload["errors"][0]["detail"]


def test_advise_without_strict_has_no_gate_entry(tmp_path, capsys, monkeypatch):
    from chartwright.cli import main

    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
        "charts": [ts("A", height=2)], "layout": {"rows": [["A"]]}}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["advise", str(spec)])
    assert exc.value.code == 0
    assert "errors" not in json.loads(capsys.readouterr().out)


# -- 1.2: one version constant ----------------------------------------------------


def test_mcp_error_fallback_reports_the_real_version():
    """It hardcoded "1" while everything else reported the constant."""
    import chartwright.mcp_server as mcp
    from chartwright.design import DESIGN_BRAIN_VERSION

    src = inspect.getsource(mcp)
    assert '"design_brain": "1"' not in src
    payload = mcp._advice(object())          # not a spec -> the except branch
    assert payload["design_brain"] == DESIGN_BRAIN_VERSION
    assert payload["errors"]


# -- 1.4: decompile says when its dataset index is truncated -----------------------


def test_decompile_names_a_truncated_dataset_index():
    """Past the page cap a real dataset became 'uuid not resolvable' and its
    chart was dropped: a wrong answer wearing the costume of an honest loss."""
    from chartwright.compiler import compile_bundle
    from chartwright.decompile import decompile_bundle
    from chartwright.testing import stub_resolution

    spec = mk([ts("A")])
    bundle = compile_bundle(spec, stub_resolution(spec))

    def lookup(_u):
        return None
    lookup.truncated = 20000

    losses = [x.what for x in decompile_bundle(bundle, lookup).losses]
    assert any("dataset index stopped at 20000" in x for x in losses), losses
    assert any("not resolvable" in x for x in losses)


def test_untruncated_decompile_says_nothing_about_the_index():
    from chartwright.compiler import compile_bundle
    from chartwright.decompile import decompile_bundle
    from chartwright.testing import stub_resolution

    spec = mk([ts("A")])
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    result = decompile_bundle(
        compile_bundle(spec, stub_resolution(spec)),
        lambda u: {"database": "db", "schema": None, "table": "orders"} if u == ds.uuid else None)
    assert result.losses == [], [x.as_dict() for x in result.losses]
