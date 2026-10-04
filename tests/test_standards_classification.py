"""A classification a standard assigns, and can lock (docs/DESIGN-BRAIN.md sec.18,
"Lifecycle and classification").

`content.classification` is a scalar slot like the colour scheme: standards apply writes
it into dashboard.classification and records it. Unlocked, it is a default the author
may change. Locked (`locked.content: [classification]`), the spec must hold the
standard's value: changing or dropping it is a standard.content-locked error, and the
classification footer rows follow the standard's value, so reclassifying a dashboard no
longer swaps its locked confidential row away. A waiver lets one dashboard differ.
"""

import json

import pytest

from chartwright.design.standards import StandardsError, load_standards
from test_standards import make_repo, run
from test_standards_content import DATA, apply, edit, read, spec_file

pytest.importorskip("yaml")

BASE = """\
name: org
default: true
classifications: [public, internal, confidential]
content:
  footer_by_classification:
    confidential:
      - [{markdown: "Named recipients only.", width: 12, height: 1}]
"""
CONFIDENTIAL_ROW = [{"markdown": "Named recipients only.", "width": 12, "height": 1}]


@pytest.fixture(autouse=True)
def no_overlay(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(home))
    monkeypatch.delenv("CHARTWRIGHT_STANDARDS_DIR", raising=False)


def repo_with(tmp_path, extra: str, waivers: str | None = None):
    files = {"org.yaml": BASE + extra}
    if waivers is not None:
        files["waivers.yaml"] = waivers
    return make_repo(tmp_path / "repo", files).parent


ASSIGNED = """\
  classification: confidential
"""
LOCKED = ASSIGNED + """\
locked:
  content: [classification, footer_by_classification]
"""


def check(capsys, path, *argv):
    code, out = run(capsys, "standards", "check", str(path), *argv)
    return code, out["specs"][0]


def locked_findings(entry):
    return [f for f in entry["findings"] if f["rule"] == "standard.content-locked"]


def test_apply_writes_the_assigned_classification_and_its_rows_in_one_run(tmp_path, capsys):
    repo = repo_with(tmp_path, ASSIGNED)
    path = spec_file(repo)
    code, out = apply(capsys, str(path))
    data = read(path)
    assert code == 0 and data["dashboard"]["classification"] == "confidential"
    assert data["design"]["standard_written"]["dashboard.classification"] == {
        "layer": "org", "value": "confidential"}
    assert CONFIDENTIAL_ROW in data["layout"]["footer"]   # the rows key off it at once
    assert apply(capsys, str(path))[1]["specs"][0]["changes"] == []   # converged


def test_an_unlocked_assignment_is_a_default_the_author_may_change(tmp_path, capsys):
    repo = repo_with(tmp_path, ASSIGNED)
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].__setitem__("classification", "public"))
    code, entry = check(capsys, path)
    assert code == 0 and not locked_findings(entry)
    apply(capsys, str(path))
    data = read(path)
    assert data["dashboard"]["classification"] == "public"
    assert CONFIDENTIAL_ROW not in data["layout"]["footer"]
    # As phase 3 had it for a lock on the rows alone: the rows follow the author's field.
    rows_only = repo_with(tmp_path / "rows", "locked:\n  content: [footer_by_classification]\n")
    path = spec_file(rows_only, data={**DATA, "dashboard": {**DATA["dashboard"],
                                                            "classification": "confidential"}})
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].__setitem__("classification", "public"))
    code, entry = check(capsys, path)
    assert code == 0 and not locked_findings(entry)


def test_a_locked_classification_changed_is_an_error_and_its_rows_stay(tmp_path, capsys):
    repo = repo_with(tmp_path, LOCKED)
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].__setitem__("classification", "public"))
    code, entry = check(capsys, path)
    [f] = locked_findings(entry)
    assert code == 1 and f["where"] == "dashboard.classification"
    assert '"confidential"' in f["detail"] and '"public"' in f["detail"]
    assert "follows another standard, or gets a waiver" in f["detail"]
    # The confidential row is still the one expected: reclassifying didn't drop it.
    assert not [x for x in entry["findings"] if x["rule"] == "standard.content-stale"]
    code, out = apply(capsys, str(path), "--locked")
    data = read(path)
    assert data["dashboard"]["classification"] == "confidential"
    assert CONFIDENTIAL_ROW in data["layout"]["footer"]
    assert [c["action"] for c in out["specs"][0]["changes"]] == ["rewrite"]


def test_a_locked_classification_dropped_is_an_error(tmp_path, capsys):
    repo = repo_with(tmp_path, LOCKED)
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].pop("classification"))
    code, entry = check(capsys, path)
    assert code == 1 and [f["where"] for f in locked_findings(entry)] == [
        "dashboard.classification"]
    apply(capsys, str(path), "--locked")
    assert read(path)["dashboard"]["classification"] == "confidential"


def test_a_waiver_lets_one_dashboard_keep_another_classification(tmp_path, capsys):
    waivers = json.dumps({"waivers": [{
        "slug": "t", "rule": "dashboard.classification", "owner": "@acme/platform",
        "reason": "Published to partners under contract", "expires": "2026-12-31"}]})
    repo = repo_with(tmp_path, LOCKED, waivers)
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].__setitem__("classification", "public"))
    code, entry = check(capsys, path, "--as-of", "2026-10-04")
    assert code == 0 and entry["waived"][0]["where"] == "dashboard.classification"
    # Waived, the rows follow the dashboard's own classification again.
    apply(capsys, str(path), "--as-of", "2026-10-04")
    assert CONFIDENTIAL_ROW not in read(path)["layout"]["footer"]


def test_the_assigned_classification_must_be_in_the_list(tmp_path):
    repo = repo_with(tmp_path, "  classification: secret\n")
    with pytest.raises(StandardsError, match="'secret' is not in the classifications list"):
        load_standards(repo / "standards")


def test_a_team_may_not_change_a_locked_classification(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        "org.yaml": BASE + LOCKED,
        "team.yaml": "name: team\nextends: org\ncontent:\n  classification: internal\n"}).parent
    with pytest.raises(StandardsError) as e:
        load_standards(repo / "standards")
    assert e.value.code == "locked" and "content.classification is locked by 'org'" in str(e.value)


def test_explain_and_show_name_the_classification(tmp_path, capsys):
    repo = repo_with(tmp_path, LOCKED)
    path = spec_file(repo)
    from test_standards import run_ok

    shown = run_ok(capsys, "standards", "show", "--standards", str(repo / "standards"), "--json")
    assert shown["content"]["content.classification"] == {
        "slot": "classification", "value": "confidential", "layer": "org", "locked": True}
    apply(capsys, str(path))
    explained = run_ok(capsys, "explain", str(path), "--json")
    row = next(r for r in explained["dashboard"]
               if r["item"] == "dashboard.classification")
    assert row["source"] == "standard" and row["locked"] is True
