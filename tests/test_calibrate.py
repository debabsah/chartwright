"""Phase 3: absorb events feed the log; calibrate proposes medians with
one-vote-per-chart hygiene; --write lands in the overlay that presets and
fixes then honor."""

import json

from chartwright.design.calibrate import calibrate, log_path, record_absorb
from chartwright.design.presets import load_overlay, params_for
from chartwright.spec import load_spec

DS = {"database": "db", "table": "orders"}


def mk_spec(slug="t"):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": slug},
        "charts": [{"type": "table", "name": "Rows", "dataset": DS,
                    "columns": ["a"], "height": 8}],
        "layout": {"rows": [["Rows"]]},
    })


def seed(monkeypatch, tmp_path, heights, slug_prefix="d"):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    for i, h in enumerate(heights):
        record_absorb("prod", mk_spec(f"{slug_prefix}{i}"),
                      [{"chart": "Rows", "height": h}])


def test_record_and_latest_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    spec = mk_spec()
    record_absorb("prod", spec, [{"chart": "Rows", "height": 9}])
    record_absorb("prod", spec, [{"chart": "Rows", "height": 12}])
    lines = [json.loads(x) for x in log_path().read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["type"] == "table"
    report = calibrate(min_samples=1)
    # same (profile, slug, chart): one vote, latest height
    assert report["events"] == 1
    assert report["proposals"][0]["median"] == 12


def test_below_min_samples_no_proposal(monkeypatch, tmp_path):
    seed(monkeypatch, tmp_path, [11, 11, 11])
    report = calibrate(min_samples=5)
    assert report["proposals"] == []
    # observed-but-not-actionable entries are reported, not thrown away
    assert report["candidates"][0]["type"] == "table"
    assert "needs >= 5 samples" in report["candidates"][0]["note"]


def test_write_updates_overlay_and_downstream(monkeypatch, tmp_path):
    seed(monkeypatch, tmp_path, [11, 11.4, 11, 12, 10.8])
    report = calibrate(min_samples=5, write=True)
    assert report["written"] and report["proposals"][0]["type"] == "table"
    overlay = load_overlay()
    assert overlay.recommended_heights["table"] == 11
    assert params_for("analytical", overlay).recommended_heights["table"] == 11
    # within a unit of the new baseline: converged, nothing further to propose
    assert calibrate(min_samples=5)["proposals"] == []


def test_junk_lines_ignored(monkeypatch, tmp_path):
    seed(monkeypatch, tmp_path, [11])
    with log_path().open("a", encoding="utf-8") as f:
        f.write("not json\n" + json.dumps({"chart": "X"}) + "\n")
    assert calibrate(min_samples=1)["events"] == 1
