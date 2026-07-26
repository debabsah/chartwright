"""`--design strict` must fail CLOSED.

Advice never breaks check/apply: a broken design.yaml degrades to an error
note inside the advice block. That is right for `--design warn`. Under
`--design strict` it used to mean counts of zero, so ONE typo in an org-wide
overlay silently disarmed the gate everywhere it was used -- on specs that
would otherwise have blocked.
"""

import pytest

from chartwright.cli import _advice_payload, _design_blocks
from chartwright.spec import load_spec

DS = {"database": "db", "table": "orders"}

BAD = load_spec({
    "spec_version": "1",
    "dashboard": {"title": "T", "slug": "t"},
    # 1 unit tall and 1/12 wide: an error plus warnings under any audience.
    "charts": [{"type": "timeseries_line", "name": "L", "dataset": DS,
                "metrics": ["COUNT(*)"], "time_column": "ts", "height": 1, "width": 1}],
    "layout": {"rows": [["L"]]},
})


@pytest.fixture
def design_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    return tmp_path


def test_healthy_overlay_gates_on_findings(design_dir):
    advice = _advice_payload(BAD)
    assert advice["counts"]["error"] and not advice.get("errors")
    assert "design findings block" in _design_blocks(advice)


def test_broken_overlay_blocks_instead_of_passing_silently(design_dir):
    (design_dir / "design.yaml").write_text("params:\n  min_axis_height: notanumber\n")
    advice = _advice_payload(BAD)
    # advice still degrades rather than crashing the pipeline...
    assert advice["counts"] == {"error": 0, "warn": 0, "info": 0}
    assert [e["code"] for e in advice["errors"]] == ["overlay"]
    # ...but a gate that cannot evaluate must not report "clean"
    blocked = _design_blocks(advice)
    assert blocked and "could not be evaluated" in blocked and "--design off" in blocked


def test_clean_spec_does_not_block(design_dir):
    clean = load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "t"},
        "charts": [{"type": "timeseries_line", "name": "L", "dataset": DS,
                    "metrics": ["COUNT(*)"], "time_column": "ts", "height": 8}],
        # A defaulted time_range picker is what makes a timeseries dashboard
        # clean: without one, data.unwindowed-history warns that every load
        # draws the dataset's full history.
        "filters": [{"type": "time_range", "name": "Date", "default": "Last month"}],
        "layout": {"rows": [["L"]]},
    })
    advice = _advice_payload(clean)
    assert not [f for f in advice["findings"] if f["severity"] in ("error", "warn")]
    assert _design_blocks(advice) is None
