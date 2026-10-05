"""Waivers: standards/waivers.yaml, the one way past a lock (docs/DESIGN-BRAIN.md sec.18,
"Waivers").

A waiver names a dashboard (slug, or spec path), a locked rule or content item, an
owner, a reason and an expiry. It lets that dashboard's locked finding pass, reported as
waived with who, why and until when; a spec's design.ignore still can't. Expiry follows
the decision record's #5: `standards check` and `advise` fail on an expired waiver for
the specs they were asked to check; standards apply and the check, apply and plan that
deploy keep applying it with a warning; restore never reads it. --as-of fixes the day.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

import chartwright.cli as cli
from chartwright.design import advice_payload, gate_block
from chartwright.design.standards import StandardsError, StandardsSource, load_standards
from chartwright.spec import load_spec
from test_standards import make_repo, mcp_call, run
from test_standards_content import DATA, FINANCE, ORG, apply, edit, read, spec_file

pytest.importorskip("yaml")

FOOTER_ITEM = "layout.footer[org][0]"
LATER, EARLIER = "2026-12-31", "2026-09-30"


@pytest.fixture(autouse=True)
def no_overlay(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(home))
    monkeypatch.delenv("CHARTWRIGHT_STANDARDS_DIR", raising=False)


def waivers_yaml(*entries: dict) -> str:
    return json.dumps({"waivers": list(entries)})  # JSON is YAML


def waiver(**kw) -> dict:
    out = {"slug": "t", "rule": FOOTER_ITEM, "owner": "@acme/platform",
           "reason": "The wallboard has no room for the legal row", "expires": LATER}
    out.update(kw)
    return {k: v for k, v in out.items() if v is not None}


def make(tmp_path, *entries, files=None) -> Path:
    files = files or {"org.yaml": ORG, "teams/finance.yaml": FINANCE}
    if entries is not None:
        files = {**files, "waivers.yaml": waivers_yaml(*entries)}
    return make_repo(tmp_path / "repo", files).parent


def applied(capsys, repo, name="s.json", data=None, drop_footer=True) -> Path:
    path = spec_file(repo, name, data)
    code, _ = apply(capsys, str(path))
    assert code == 0
    if drop_footer:
        edit(path, lambda d: d["layout"].__setitem__(
            "footer", [r for r in d["layout"]["footer"] if "Acme Corp" not in json.dumps(r)]))
    return path


def check(capsys, *argv) -> tuple[int, dict]:
    return run(capsys, "standards", "check", *argv)


def rules(entry) -> list[str]:
    return sorted({f["rule"] for f in entry["findings"]})


# -- the file is not a standard -------------------------------------------------------


def test_the_waivers_file_is_no_standard(tmp_path):
    repo = make(tmp_path, waiver())
    standards = load_standards(repo / "standards")   # before: "`name` is required"
    assert sorted(standards.files) == ["finance", "org"]
    assert len(standards.waivers) == 1 and standards.waivers_file.name == "waivers.yaml"
    # A waivers file alone makes no standards folder: discovery wants a standard.
    from chartwright.design.standards import discover

    root = tmp_path / "alone"
    (root / ".git").mkdir(parents=True)
    (root / "standards").mkdir()
    (root / "standards" / "waivers.yaml").write_text(waivers_yaml(waiver()), encoding="utf-8")
    assert discover(root / "specs") is None


def test_an_empty_waivers_file_is_fine(tmp_path):
    repo = make_repo(tmp_path / "repo", {"org.yaml": ORG, "waivers.yaml": ""}).parent
    assert load_standards(repo / "standards").waivers == []


@pytest.mark.parametrize("entry, says", [
    (waiver(owner=None), "`owner` is required"),
    (waiver(owner="  "), "`owner` is required"),
    (waiver(reason=None), "`reason` is required"),
    (waiver(expires=None), "`expires` is required"),
    (waiver(expires="next year"), "must be a date"),
    (waiver(rule="size.no-such-rule"), "is no rule id and no content item"),
    (waiver(rule="layout.footer[nobody][0]"), "names the layer 'nobody'"),
    (waiver(rule="standard.waiver-expired"), "no waiver covers it"),
    (waiver(layer="nobody"), "names no standard"),
    (waiver(slug=None), "by spec path (preferred) or slug"),
    (waiver(slug="Not A Slug"), "is not a dashboard slug"),
    ({**waiver(), "approved_by": "me"}, "unknown keys"),
])
def test_malformed_waivers_are_errors_naming_the_entry(tmp_path, entry, says):
    repo = make(tmp_path, entry)
    with pytest.raises(StandardsError) as e:
        load_standards(repo / "standards")
    assert e.value.code == "waivers_file" and "waivers[0]" in str(e.value) and says in str(e.value)


def test_one_waiver_per_dashboard_rule_and_layer(tmp_path):
    repo = make(tmp_path, waiver(), waiver(expires="2027-01-31"))
    with pytest.raises(StandardsError, match=r"waivers\[1\] repeats waivers\[0\]"):
        load_standards(repo / "standards")


def test_a_broken_waivers_file_fails_the_run(tmp_path, capsys):
    repo = make(tmp_path, waiver(owner=None))
    code, out = check(capsys, str(spec_file(repo)))
    assert code == 1 and out["errors"][0]["code"] == "waivers_file"


# -- a waiver lets a locked item pass ------------------------------------------------


def test_a_locked_item_the_spec_lacks_fails_without_a_waiver(tmp_path, capsys):
    repo = make(tmp_path)
    path = applied(capsys, repo)
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    assert code == 1 and "standard.content-locked" in rules(out["specs"][0])
    assert "waived" not in out["specs"][0]
    # Without a waivers file at all, the report reads exactly as before.
    plain = make_repo(tmp_path / "plain", {"org.yaml": ORG, "teams/finance.yaml": FINANCE}).parent
    code, out = check(capsys, str(applied(capsys, plain, drop_footer=False)), "--report")
    assert code == 0 and "waivers" not in out and "waiver_warnings" not in out
    assert "waived" not in out["specs"][0]
    assert "standard.waiver-expired" not in out["specs"][0]["locks_hit"]


def test_a_waiver_lets_the_locked_item_differ_and_says_who_why_and_until(tmp_path, capsys):
    repo = make(tmp_path, waiver())
    path = applied(capsys, repo)
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    entry = out["specs"][0]
    assert code == 0 and entry["ok"] and "standard.content-locked" not in rules(entry)
    [w] = entry["waived"]
    assert w["where"] == FOOTER_ITEM and w["rule"] == "standard.content-locked"
    assert (w["owner"], w["expires"], w["status"], w["days_left"]) == (
        "@acme/platform", LATER, "active", 88)
    assert w["reason"].startswith("The wallboard")


def test_design_ignore_still_cannot_silence_the_lock(tmp_path, capsys):
    repo = make(tmp_path)   # a waivers file, but no waiver for this dashboard
    path = applied(capsys, repo)
    edit(path, lambda d: d.setdefault("design", {}).__setitem__(
        "ignore", ["standard.content-locked"]))
    code, out = check(capsys, str(path))
    entry = out["specs"][0]
    assert code == 1 and "standard.content-locked" in rules(entry)
    assert entry["locks"]["refused_ignores"][0]["entry"] == "standard.content-locked"


def test_a_rule_waiver_covers_every_locked_item_of_that_rule(tmp_path, capsys):
    repo = make(tmp_path, waiver(rule="standard.content-locked"))
    path = applied(capsys, repo)
    edit(path, lambda d: d["dashboard"].__setitem__("css", ".mine { color: blue; }"))
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    assert code == 0
    assert sorted(w["where"] for w in out["specs"][0]["waived"]) == [
        "dashboard.css[org]", FOOTER_ITEM]


def test_a_waiver_for_another_item_or_dashboard_does_not_apply(tmp_path, capsys):
    repo = make(tmp_path, waiver(rule="dashboard.certified_by"), waiver(slug="other"))
    path = applied(capsys, repo)
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    assert code == 1 and "waived" not in out["specs"][0]


def test_a_layer_scoped_waiver_covers_only_that_layers_lock(tmp_path, capsys):
    repo = make(tmp_path, waiver(layer="finance"))
    path = applied(capsys, repo)
    code, _ = check(capsys, str(path), "--as-of", "2026-10-04")
    assert code == 1   # org locks the footer; a waiver scoped to finance can't lift it
    repo2 = make(tmp_path / "b", waiver(layer="org"))
    path2 = applied(capsys, repo2)
    assert check(capsys, str(path2), "--as-of", "2026-10-04")[0] == 0


def test_a_waiver_can_name_the_spec_by_path(tmp_path, capsys):
    repo = make(tmp_path, waiver(slug=None, spec="specs/s.json"))
    path = applied(capsys, repo)
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    assert code == 0 and out["specs"][0]["waived"][0]["spec"] == "specs/s.json"


def test_a_waiver_covers_a_locked_rule(tmp_path, capsys):
    from test_standards import DATA as LINE, ORG as RULES_ORG

    repo = make(tmp_path, waiver(rule="size.axis-min-height"), files={"org.yaml": RULES_ORG})
    plain = make_repo(tmp_path / "plain", {"org.yaml": RULES_ORG}).parent
    # A four-unit line chart: size.axis-min-height, which the rules-only org file locks.
    code, out = check(capsys, str(spec_file(plain, data=LINE)), "--strict")
    assert code == 1 and "size.axis-min-height" in rules(out["specs"][0])
    code, out = check(capsys, str(spec_file(repo, data=LINE)), "--strict", "--as-of", "2026-10-04")
    entry = out["specs"][0]
    assert code == 0 and "size.axis-min-height" not in rules(entry)
    assert [w["rule"] for w in entry["waived"]] == ["size.axis-min-height"]


# -- expiry --------------------------------------------------------------------------


def test_an_expired_waiver_fails_standards_check_and_as_of_reproduces_a_run(tmp_path, capsys):
    repo = make(tmp_path, waiver(expires=EARLIER))
    path = applied(capsys, repo)
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    entry = out["specs"][0]
    assert code == 1
    assert {"standard.content-locked", "standard.waiver-expired"} <= set(rules(entry))
    expired = next(f for f in entry["findings"] if f["rule"] == "standard.waiver-expired")
    assert "expired on 2026-09-30" in expired["detail"] and "@acme/platform" in expired["detail"]
    assert expired["locked"] is True
    # The same files, read as of the day before expiry, pass: the day is the only input.
    code, out = check(capsys, str(path), "--as-of", "2026-09-30")
    assert code == 0 and out["specs"][0]["waived"][0]["days_left"] == 0
    assert check(capsys, str(path), "--as-of", "2026-09-30")[1] == out


def test_expiry_fails_only_the_specs_checked(tmp_path, capsys):
    repo = make(tmp_path, waiver(expires=EARLIER))
    lapsed = applied(capsys, repo)
    other = dict(DATA, dashboard={**DATA["dashboard"], "slug": "other"})
    clean = applied(capsys, repo, "other.json", other, drop_footer=False)
    code, out = check(capsys, str(clean), "--as-of", "2026-10-04")
    assert code == 0                      # a pull request touching only `other` stays green
    assert out["waiver_warnings"][0]["code"] == "waiver_expired"   # but it is told
    assert check(capsys, str(lapsed), "--as-of", "2026-10-04")[0] == 1


def test_an_expired_waiver_with_nothing_left_to_cover_still_fails(tmp_path, capsys):
    """The decision record's #5 read literally: expiry is an error on a pull request
    touching the dashboard, so the stale entry is removed from the waivers file."""
    repo = make(tmp_path)                               # the footer is written first
    path = applied(capsys, repo, drop_footer=False)
    (repo / "standards" / "waivers.yaml").write_text(waivers_yaml(waiver(expires=EARLIER)),
                                                     encoding="utf-8")
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    assert code == 1 and rules(out["specs"][0]) .count("standard.waiver-expired") == 1
    assert rules(out["specs"][0]).count("standard.content-locked") == 0
    [f] = [f for f in out["specs"][0]["findings"] if f["rule"] == "standard.waiver-expired"]
    assert "covers nothing now" in f["detail"] and "delete the entry" in f["detail"]
    # One that still covers a finding says renew or fix, as before.
    lapsed = applied(capsys, repo, "lapsed.json")
    code, out = check(capsys, str(lapsed), "--as-of", "2026-10-04")
    [f] = [f for f in out["specs"][0]["findings"] if f["rule"] == "standard.waiver-expired"]
    assert "covers nothing" not in f["detail"] and "Renew or remove it" in f["detail"]


def test_advise_enforces_expiry_too(tmp_path, capsys):
    repo = make(tmp_path, waiver(expires=EARLIER))
    path = applied(capsys, repo)
    code, out = run(capsys, "advise", str(path), "--as-of", "2026-10-04")
    assert code == 1 and any(f["rule"] == "standard.waiver-expired" for f in out["findings"])
    code, out = run(capsys, "advise", str(path), "--as-of", "2026-09-01")
    assert code == 0 and out["waived"][0]["status"] == "active"


def test_a_deploy_warns_on_an_expired_waiver_and_never_blocks(tmp_path, capsys):
    repo = make(tmp_path, waiver(expires=EARLIER))
    path = applied(capsys, repo)
    spec = load_spec(read(path))
    source = StandardsSource(repo / "standards", as_of=dt.date(2026, 10, 4))
    advice = advice_payload(spec, strict=True, standards=source, spec_path=path)
    assert advice["waived"][0]["status"] == "expired"
    assert any("expired on 2026-09-30" in w for w in advice["warnings"])
    assert gate_block(advice) is None     # --design strict lets the deploy through


def test_cli_apply_carries_the_expiry_warning_and_proceeds(tmp_path, capsys, monkeypatch):
    repo = make(tmp_path, waiver(expires=EARLIER))
    path = applied(capsys, repo)
    import chartwright.apply as apply_mod
    from chartwright.apply import ApplyReport

    seen = {}

    def fake_apply(spec, client, profile, version):
        seen["spec"] = spec
        return ApplyReport(ok=True, stage="done")

    monkeypatch.setattr(cli, "_client", lambda profile: object())
    monkeypatch.setattr(apply_mod, "apply", fake_apply)
    code, out = run(capsys, "apply", str(path), "--profile", "p", "--design", "strict",
                    "--as-of", "2026-10-04")
    assert code == 0 and seen and any("expired on 2026-09-30" in w for w in out["warnings"])


def test_restore_never_reads_waivers(tmp_path, capsys, monkeypatch):
    repo = make(tmp_path, waiver(expires=EARLIER))
    import chartwright.apply as apply_mod
    import chartwright.design.standards as standards_mod
    from chartwright import ids
    from chartwright.apply import ApplyReport
    from chartwright.compiler import compile_bundle
    from chartwright.testing import stub_resolution

    spec = load_spec(read(applied(capsys, repo)))
    bundle = tmp_path / "backup.zip"
    bundle.write_bytes(compile_bundle(spec, stub_resolution(spec)))
    assert str(ids.dashboard_uuid(spec.dashboard.slug))

    def boom(*a, **k):
        raise AssertionError("restore read the standards folder")

    monkeypatch.setattr(standards_mod, "load_standards", boom)
    monkeypatch.setattr(standards_mod.StandardsSource, "load", boom)
    monkeypatch.setattr(cli, "_client", lambda profile: object())
    monkeypatch.setattr(apply_mod, "restore_bundle",
                        lambda blob, slug, client: ApplyReport(ok=True, stage="done"))
    monkeypatch.chdir(repo)
    code, out = run(capsys, "restore", str(bundle), "--profile", "p")
    assert code == 0 and out["ok"]


# -- standards apply -----------------------------------------------------------------


def test_standards_apply_leaves_a_waived_item_and_check_passes(tmp_path, capsys):
    repo = make(tmp_path, waiver())
    path = applied(capsys, repo)
    before = read(path)
    code, out = apply(capsys, str(path), "--locked", "--as-of", "2026-10-04")
    assert code == 0 and read(path) == before          # --locked rewrites nothing waived
    assert out["specs"][0]["waived"][0]["item"] == FOOTER_ITEM
    code, out = apply(capsys, str(path), "--check", "--as-of", "2026-10-04")
    assert code == 0 and out["specs"][0]["locked_stale"] == []
    code, text = run(capsys, "standards", "apply", str(path), "--check", "--as-of", "2026-10-04")
    assert "differ under a waiver in standards/waivers.yaml" in text


def test_standards_apply_keeps_an_expired_waiver_with_a_warning(tmp_path, capsys):
    repo = make(tmp_path, waiver(expires=EARLIER))
    path = applied(capsys, repo)
    code, out = apply(capsys, str(path), "--check", "--as-of", "2026-10-04")
    entry = out["specs"][0]
    assert code == 0 and entry["waived"][0]["status"] == "expired"
    assert "expired on 2026-09-30" in entry["warnings"][0]


def test_without_a_waiver_standards_apply_reports_the_lock_as_before(tmp_path, capsys):
    repo = make(tmp_path)
    path = applied(capsys, repo)
    code, out = apply(capsys, str(path), "--check")
    assert code == 1 and out["specs"][0]["locked_stale"] == [FOOTER_ITEM]
    assert "waived" not in out["specs"][0]


# -- the report -----------------------------------------------------------------------


def test_the_report_lists_expired_expiring_and_unmatched_waivers(tmp_path, capsys):
    repo = make(tmp_path, waiver(expires=EARLIER), waiver(rule="dashboard.css[org]",
                                                           expires="2026-10-20"),
                waiver(slug="gone", expires="2027-06-30"))
    path = applied(capsys, repo)
    code, out = check(capsys, str(path), "--report", "--as-of", "2026-10-04")
    w = out["waivers"]
    assert code == 1 and w["file"].endswith("standards/waivers.yaml")
    assert (w["total"], w["active"]) == (3, 2)
    assert [e["expires"] for e in w["expired"]] == [EARLIER]
    assert [e["rule"] for e in w["expiring"]] == ["dashboard.css[org]"]
    assert [e["slug"] for e in w["unmatched"]] == ["gone"]
    assert w["by_owner"] == {"@acme/platform": 3}
    code, out = check(capsys, str(path), "--report", "--as-of", "2026-10-04",
                      "--expiring-within", "10")
    assert out["waivers"]["expiring"] == []


# -- MCP --------------------------------------------------------------------------------


def test_mcp_standards_check_matches_the_cli_with_waivers(tmp_path, capsys, monkeypatch):
    repo = make(tmp_path, waiver(expires=EARLIER))
    path = applied(capsys, repo)
    _, cli_out = check(capsys, str(path), "--as-of", "2026-09-01")
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    out = mcp_call("standards_check", spec_json=path.read_text(), as_of="2026-09-01")
    assert out["ok"] and out["waived"] == cli_out["specs"][0]["waived"]
    out = mcp_call("standards_check", spec_json=path.read_text(), as_of="2026-10-04")
    assert not out["ok"] and any(f["rule"] == "standard.waiver-expired" for f in out["findings"])
    assert mcp_call("standards_check", spec_json=path.read_text(),
                    as_of="soon")["errors"][0]["code"] == "bad_as_of"


def test_mcp_standards_apply_leaves_a_waived_item(tmp_path, capsys, monkeypatch):
    repo = make(tmp_path, waiver())
    path = applied(capsys, repo)
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    out = mcp_call("standards_apply", spec_json=path.read_text(), locked=True,
                   as_of="2026-10-04")
    assert out["written"] is False and out["waived"][0]["item"] == FOOTER_ITEM


def test_mcp_build_dashboard_warns_on_an_expired_waiver_and_builds(tmp_path, capsys, monkeypatch):
    pytest.importorskip("mcp")
    import chartwright.apply as apply_mod
    import chartwright.mcp_server as server
    from chartwright.apply import ApplyReport
    from test_mcp_server import _call

    repo = make(tmp_path, waiver(expires=EARLIER))
    path = applied(capsys, repo)
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    monkeypatch.setattr(server, "_client", lambda profile: object())
    monkeypatch.setattr(apply_mod, "apply",
                        lambda spec, client, profile, version: ApplyReport(ok=True, stage="done"))
    out = _call("build_dashboard", {"spec_json": path.read_text(), "design": "strict",
                                    "as_of": "2026-10-04"}, "p")
    assert out["ok"] and any("expired on 2026-09-30" in w for w in out["warnings"])
    assert out["advice"]["waived"][0]["status"] == "expired"


def test_a_waiver_written_before_the_first_apply_keeps_the_item_off(tmp_path, capsys):
    repo = make(tmp_path, waiver())
    path = spec_file(repo)
    code, out = apply(capsys, str(path), "--as-of", "2026-10-04")
    data = read(path)
    assert code == 0 and "Acme Corp" not in json.dumps(data["layout"].get("footer"))
    assert FOOTER_ITEM not in data["design"]["standard_written"]
    assert out["specs"][0]["waived"][0]["item"] == FOOTER_ITEM
    # Once the waiver is gone, apply writes the footer as for any dashboard.
    (repo / "standards" / "waivers.yaml").write_text(waivers_yaml(), encoding="utf-8")
    apply(capsys, str(path))
    assert "Acme Corp" in json.dumps(read(path)["layout"]["footer"])


# -- the trust boundary: a slug is the author's, a path the repository's -------------


def test_a_slug_waiver_is_flagged_where_the_path_is_known(tmp_path, capsys):
    """Before: an author edits a spec's slug to a waived one and inherits the waiver,
    with nothing said. The waiver still matches (slug-only waivers exist), but every run
    with a path names the risk and the fix."""
    repo = make(tmp_path, waiver())
    path = applied(capsys, repo)
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    entry = out["specs"][0]
    assert code == 0 and entry["waived"]
    assert any("matches by slug t alone" in w and "pin it with spec: specs/s.json" in w
               for w in entry["warnings"])


def test_a_pinned_waiver_ignores_another_spec_that_takes_its_slug(tmp_path, capsys):
    repo = make(tmp_path, waiver(spec="specs/wallboard.json"))      # slug t, pinned
    impostor = applied(capsys, repo, "impostor.json")               # also slug t
    code, out = check(capsys, str(impostor), "--as-of", "2026-10-04")
    entry = out["specs"][0]
    assert code == 1 and "waived" not in entry
    assert any("is for spec specs/wallboard.json" in w and "does not apply" in w
               for w in entry["warnings"])
    real = applied(capsys, repo, "wallboard.json")
    code, out = check(capsys, str(real), "--as-of", "2026-10-04")
    assert code == 0 and out["specs"][0]["waived"][0]["spec"] == "specs/wallboard.json"
    assert "warnings" not in out["specs"][0]


def test_the_report_flags_a_waiver_matched_by_more_than_one_spec(tmp_path, capsys):
    repo = make(tmp_path, waiver())
    a = applied(capsys, repo, "a.json")
    applied(capsys, repo, "b.json")          # the same slug, t
    code, out = check(capsys, str(a.parent), "--report", "--as-of", "2026-10-04")
    [shared] = out["waivers"]["matched_more_than_once"]
    assert shared["slug"] == "t" and shared["specs"] == sorted(
        [(a.parent / "a.json").as_posix(), (a.parent / "b.json").as_posix()])


def test_mcp_matches_a_pinned_waiver_by_slug_and_says_so(tmp_path, capsys, monkeypatch):
    repo = make(tmp_path, waiver(spec="specs/s.json"))
    path = applied(capsys, repo)
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    out = mcp_call("standards_check", spec_json=path.read_text(), as_of="2026-10-04")
    assert out["ok"] and any("no spec path here to confirm" in w for w in out["warnings"])


def test_mcp_fix_spec_enforces_expiry_as_advise_fix_does(tmp_path, capsys, monkeypatch):
    repo = make(tmp_path, waiver(expires=EARLIER))
    path = applied(capsys, repo)
    code, out = run(capsys, "advise", str(path), "--as-of", "2026-10-04")
    assert "standard.waiver-expired" in {f["rule"] for f in out["findings"]}
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    out = mcp_call("fix_spec", spec_json=path.read_text(), as_of="2026-10-04")
    assert "standard.waiver-expired" in {f["rule"] for f in out["advice"]["findings"]}
    out = mcp_call("fix_spec", spec_json=path.read_text(), as_of="2026-09-01")
    assert "standard.waiver-expired" not in {f["rule"] for f in out["advice"]["findings"]}
    assert mcp_call("fix_spec", spec_json=path.read_text(),
                    as_of="20261004")["errors"][0]["code"] == "bad_as_of"


@pytest.mark.parametrize("text", ["20261231", "2026-W53-4", "2026-366", "2026-1-5", "soon"])
def test_dates_are_yyyy_mm_dd_exactly(tmp_path, capsys, text):
    repo = make(tmp_path, waiver())
    path = spec_file(repo)
    with pytest.raises(SystemExit) as e:
        cli.main(["standards", "check", str(path), "--as-of", text])
    assert e.value.code == 2                           # argparse refuses it
    capsys.readouterr()
    repo2 = make(tmp_path / "b", waiver(expires=text))
    with pytest.raises(StandardsError, match="must be a date"):
        load_standards(repo2 / "standards")


# -- waivers that name nothing, and a waivers file in the wrong place ------------------


@pytest.mark.parametrize("rule, why", [
    ("layout.footer[org][9]", "the org standard has no footer row 9 (it has 1)"),
    ("layout.footer[org][classification=confidential][3]", "no footer row 3"),
    ("dashboard.theme", "no standards file sets content.theme"),
    ("dashboard.label_colors[Profit]", "no standards file sets a colour for label 'Profit'"),
    ("size.min-width", "no standard locks size.min-width"),
])
def test_a_waiver_naming_nothing_is_reported_as_unmatched_with_why(tmp_path, capsys, rule, why):
    """layout.footer[org][9] used to load and sit there silently."""
    # Beside it, waivers that name real things: never reported.
    repo = make(tmp_path, waiver(rule=rule), waiver(rule="dashboard.css[finance]"),
                waiver(rule="standard.content-locked"))
    path = applied(capsys, repo, drop_footer=False)
    code, out = check(capsys, str(path), "--report", "--as-of", "2026-10-04")
    reasons = {u["rule"]: u["reason"] for u in out["waivers"]["unmatched"]}
    assert why in reasons[rule]
    assert "dashboard.css[finance]" not in reasons and "standard.content-locked" not in reasons
    assert [w["detail"] for w in out["waiver_warnings"]
            if w["code"] == "waiver_names_nothing"] == [f"waivers[0]: {reasons[rule]}"]


def test_a_waivers_file_in_a_subfolder_says_where_it_belongs(tmp_path, capsys):
    """standards/teams/waivers.yaml used to fail as a standard with unknown keys
    ['waivers'], which pointed nowhere useful."""
    repo = make_repo(tmp_path / "repo", {"org.yaml": ORG, "teams/finance.yaml": FINANCE,
                                         "teams/waivers.yaml": waivers_yaml(waiver())}).parent
    with pytest.raises(StandardsError) as e:
        load_standards(repo / "standards")
    assert e.value.code == "waivers_file"
    assert "belongs at the top of the standards folder" in str(e.value)
    assert "standards/waivers.yaml" in str(e.value) and "unknown keys" not in str(e.value)


# -- edges the mutation pass found unpinned -------------------------------------------


def test_a_rule_waiver_covers_only_its_own_rule(tmp_path, capsys):
    repo = make(tmp_path, waiver(rule="standard.css-hides"))    # locked too, but another rule
    path = applied(capsys, repo)
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    assert code == 1 and "standard.content-locked" in rules(out["specs"][0])
    assert "waived" not in out["specs"][0]


def test_an_active_waiver_wins_over_an_expired_one_for_the_same_finding(tmp_path, capsys):
    repo = make(tmp_path)
    path = applied(capsys, repo)                                # the footer then dropped
    (repo / "standards" / "waivers.yaml").write_text(waivers_yaml(
        waiver(rule="standard.content-locked", expires=EARLIER),
        waiver(expires=LATER)), encoding="utf-8")               # the item, still active
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    [w] = out["specs"][0]["waived"]
    assert w["waiver"] == 1 and w["status"] == "active"
    assert "standard.content-locked" not in rules(out["specs"][0])


def test_a_spec_waiver_matches_the_path_not_the_file_name(tmp_path, capsys):
    repo = make(tmp_path, waiver(slug=None, spec="specs/ops/s.json"))
    elsewhere = applied(capsys, repo, "s.json")                 # specs/s.json: same name
    code, out = check(capsys, str(elsewhere), "--as-of", "2026-10-04")
    assert code == 1 and "waived" not in out["specs"][0]


def test_a_waiver_naming_spec_and_slug_needs_both(tmp_path, capsys):
    repo = make(tmp_path, waiver(spec="specs/s.json"))          # slug t as well
    data = json.loads(json.dumps(DATA))
    data["dashboard"]["slug"] = "renamed"
    path = applied(capsys, repo, data=data)                     # the file, another slug
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    assert code == 1 and "waived" not in out["specs"][0]


def test_an_expired_classification_waiver_stops_keying_rows_under_enforcement(tmp_path, capsys):
    """Once a classification waiver expires, standards check keys the rows off the
    standard's classification again, so the confidential row is still expected."""
    from test_standards_classification import BASE, LOCKED

    repo = make_repo(tmp_path / "repo", {"org.yaml": BASE + LOCKED, "waivers.yaml": waivers_yaml(
        waiver(rule="dashboard.classification", expires=EARLIER))}).parent
    path = spec_file(repo)
    apply(capsys, str(path), "--as-of", "2026-09-01")
    edit(path, lambda d: d["dashboard"].__setitem__("classification", "public"))
    code, out = check(capsys, str(path), "--as-of", "2026-10-04")
    entry = out["specs"][0]
    assert code == 1 and "standard.content-stale" not in rules(entry)
    assert [f["where"] for f in entry["findings"] if f["rule"] == "standard.content-locked"] == [
        "dashboard.classification"]


def test_the_row_after_the_last_one_names_nothing(tmp_path, capsys):
    repo = make(tmp_path, waiver(rule="layout.footer[org][1]"))   # org has row 0 only
    path = applied(capsys, repo, drop_footer=False)
    code, out = check(capsys, str(path), "--report", "--as-of", "2026-10-04")
    [u] = out["waivers"]["unmatched"]
    assert "has no footer row 1 (it has 1)" in u["reason"]
