"""A standard's minimum Superset release, and content held back per instance
(docs/DESIGN-BRAIN.md sec.18, "Mixed Superset releases").

A standards file may declare `min_superset: "6.0"`: the content it contributes is for
that release or later. A content item that writes a version-gated field (versions.py;
dashboard.theme needs 6.0.0) needs that release too. check, apply and plan learn the
instance's release and hold such items back for that instance, reported as held, so one
newer-only item never fails a fleet deploy to an older instance; `standards check
--superset-version` shows the same offline. Only the standard's own content is held:
a field the author wrote meets the version check as before.
"""

import json

import pytest

import chartwright.cli as cli
from chartwright import ids
from chartwright.design.standards import (StandardsError, StandardsSource, for_instance,
                                          load_standards)
from chartwright.spec import load_spec
from test_standards import make_repo, mcp_call, run, run_ok
from test_standards_content import DATA, apply, read, spec_file
from test_superset_version import FakeClient as VersionClient

pytest.importorskip("yaml")

ORG = """\
name: org
default: true
content:
  footer:
    - [{markdown: "Acme Corp. Internal.", width: 12, height: 1}]
  theme: Acme Brand
locked:
  content: [footer]
"""
BRAND = """\
name: brand
extends: org
min_superset: "6.0"
content:
  header:
    - [{markdown: "Brand header", width: 12, height: 1}]
  css: |
    .dashboard-header { border-top: 4px solid #003366; }
"""
WAREHOUSE = {"database": "warehouse", "table": "orders"}
SPEC = {**DATA, "dashboard": {"title": "T", "slug": "t"},
        "charts": [{"type": "big_number_total", "name": "K", "dataset": WAREHOUSE,
                    "metric": "COUNT(*) AS Orders"},
                   {"type": "big_number_total", "name": "R", "dataset": WAREHOUSE,
                    "metric": "SUM(amount) AS Revenue"}],
        "layout": {"rows": [["K", "R"]]}}


@pytest.fixture(autouse=True)
def no_overlay(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(home))
    monkeypatch.delenv("CHARTWRIGHT_STANDARDS_DIR", raising=False)


class Client(VersionClient):
    """A Superset at `version` with one theme, Acme Brand (id 7)."""

    def themes(self):
        return [{"id": 7, "uuid": "77777777-7777-7777-7777-777777777777",
                 "theme_name": "Acme Brand"}]


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo", {"org.yaml": ORG, "brand.yaml": BRAND}).parent


def written(capsys, repo, standard=None) -> "Path":
    path = spec_file(repo, data=SPEC, **({"standard": standard} if standard else {}))
    code, _ = apply(capsys, str(path))
    assert code == 0
    return path


# -- the file ---------------------------------------------------------------------


def test_min_superset_is_read_as_a_release(repo):
    standards = load_standards(repo / "standards")
    assert standards.files["brand"].min_superset == "6.0.0"
    assert standards.resolved["brand"].floors == {"brand": "6.0.0"}
    assert standards.files["org"].min_superset is None


@pytest.mark.parametrize("value", ["latest", "", "[6, 0]"])
def test_min_superset_must_be_a_release(tmp_path, value):
    repo = make_repo(tmp_path / "repo", {"org.yaml": f"name: org\nmin_superset: {value!r}\n"}).parent
    with pytest.raises(StandardsError, match="min_superset must be a Superset release"):
        load_standards(repo / "standards")


def test_show_names_each_files_release(repo, capsys):
    shown = run_ok(capsys, "standards", "show", "brand", "--standards",
                   str(repo / "standards"), "--json")
    assert shown["chain"][1]["min_superset"] == "6.0.0" and "min_superset" not in shown["chain"][0]
    text = run_ok(capsys, "standards", "show", "brand", "--standards", str(repo / "standards"))
    assert "(content for Superset 6.0.0 or later)" in text


# -- standards check, offline ----------------------------------------------------


def test_check_holds_content_to_a_stated_release(repo, capsys):
    path = spec_file(repo, data=SPEC, standard="brand")   # nothing written yet
    code, out = run(capsys, "standards", "check", str(path), "--superset-version", "5.0.0")
    entry = out["specs"][0]
    held = {h["item"]: h["reason"] for h in entry["held"]}
    assert entry["superset_version"] == "5.0.0"
    assert set(held) == {"dashboard.theme", "dashboard.css[brand]", "layout.header[brand][0]"}
    assert "needs Superset 6.0.0" in held["dashboard.theme"]
    assert "min_superset" in held["layout.header[brand][0]"]
    stale = {f["where"] for f in entry["findings"] if f["rule"] == "standard.content-stale"}
    assert not stale & set(held)          # held items are not expected on this release
    # The org's locked footer is for every release: still expected, still missing.
    assert code == 1 and any(f["where"] == "layout.footer[org][0]" for f in entry["findings"]
                             if f["rule"] == "standard.content-locked")


def test_check_at_a_newer_release_holds_nothing(repo, capsys):
    path = written(capsys, repo, "brand")
    code, out = run(capsys, "standards", "check", str(path), "--superset-version", "6.1.0")
    assert code == 0 and out["specs"][0]["held"] == []
    code, out = run(capsys, "standards", "check", str(path))
    assert code == 0 and "held" not in out["specs"][0]   # no release named: nothing held


def test_held_records_are_kept_not_orphaned(repo, capsys):
    path = written(capsys, repo, "brand")
    code, out = run(capsys, "standards", "check", str(path), "--superset-version", "5.0.0")
    entry = out["specs"][0]
    assert code == 0 and not [f for f in entry["findings"] if f["rule"].startswith("standard.")
                              and f["severity"] != "info"]


# -- for_instance: what check, apply and plan send ---------------------------------


def source(repo):
    return StandardsSource(repo / "standards")


def test_an_instance_below_the_release_gets_the_spec_without_the_held_items(repo, capsys):
    path = written(capsys, repo, "brand")
    spec = load_spec(read(path))
    inst = for_instance(spec, source(repo), lambda: "5.0.0")
    assert inst.release == "5.0.0"
    assert [h["item"] for h in inst.held] == [
        "dashboard.css[brand]", "dashboard.theme", "layout.header[brand][0]"]
    d = inst.spec.dashboard
    assert d.theme is None and "cw:std brand" not in (d.css or "")
    assert inst.spec.layout.header is None
    assert spec.dashboard.theme == "Acme Brand"           # the spec itself is untouched
    assert "Acme Corp" in json.dumps(inst.spec.model_dump(mode="json")["layout"]["footer"])


def test_an_instance_at_the_release_gets_everything(repo, capsys):
    spec = load_spec(read(written(capsys, repo, "brand")))
    inst = for_instance(spec, source(repo), lambda: "6.1.0")
    assert inst.spec is spec and inst.held == []


def test_the_release_is_asked_only_when_something_could_be_held(tmp_path, capsys):
    plain = make_repo(tmp_path / "plain", {"org.yaml": "name: org\ndefault: true\n"}).parent
    spec = load_spec(read(spec_file(plain, data=SPEC)))

    def never():
        raise AssertionError("asked for the release")

    assert for_instance(spec, source(plain), never).spec is spec
    assert for_instance(spec, None, never).spec is spec   # no standards folder: no change


def test_the_authors_own_theme_is_never_held(repo, capsys):
    path = written(capsys, repo, "brand")
    data = read(path)
    data["dashboard"]["theme"] = "My Theme"               # released: the author's now
    spec = load_spec(data)
    inst = for_instance(spec, source(repo), lambda: "5.0.0")
    assert inst.spec.dashboard.theme == "My Theme"
    assert "dashboard.theme" not in [h["item"] for h in inst.held]


def test_an_unknown_release_holds_nothing_and_says_so(repo, capsys):
    spec = load_spec(read(written(capsys, repo, "brand")))
    inst = for_instance(spec, source(repo), lambda: None)
    assert inst.spec is spec and "--superset-version" in inst.warning


def cli_check(capsys, monkeypatch, path, version, *argv):
    client = Client(version)
    monkeypatch.setattr(cli, "_client", lambda profile: client)
    return run(capsys, "check", str(path), "--profile", "p", *argv)


def test_cli_check_on_an_older_instance_holds_instead_of_refusing(repo, capsys, monkeypatch):
    path = written(capsys, repo, "brand")
    code, out = cli_check(capsys, monkeypatch, path, "5.0.0", "--design", "off")
    assert code == 0 and out["errors"] == [], out
    assert [h["item"] for h in out["held"]] == [
        "dashboard.css[brand]", "dashboard.theme", "layout.header[brand][0]"]
    code, out = cli_check(capsys, monkeypatch, path, "6.1.0", "--design", "off")
    assert code == 0 and "held" not in out


def test_cli_check_advice_does_not_expect_held_items(repo, capsys, monkeypatch):
    path = spec_file(repo, data={**SPEC}, standard="brand")
    apply(capsys, str(path))
    data = read(path)
    del data["dashboard"]["theme"]                         # never deployed to 5.0 anyway
    data["design"]["standard_written"].pop("dashboard.theme")
    path.write_text(json.dumps(data), encoding="utf-8")
    code, out = cli_check(capsys, monkeypatch, path, "5.0.0")
    stale = [f["where"] for f in out["advice"]["findings"] if f["rule"].startswith("standard.")]
    assert "dashboard.theme" not in stale


def test_without_holding_an_older_instance_refuses_the_theme(repo, capsys, monkeypatch):
    """What a standard's theme did to a fleet before: every deploy to 5.0 refused."""
    path = written(capsys, repo, "brand")
    code, out = cli_check(capsys, monkeypatch, path, "5.0.0", "--design", "off",
                          "--standards", str(repo / "nowhere"))
    assert code == 1 and out["errors"][0]["code"] in ("superset_version_too_old",)
    assert "standard content not held back" in out["warnings"][0]


def test_cli_plan_holds_too(repo, capsys, monkeypatch):
    path = written(capsys, repo, "brand")
    client = Client("5.0.0", dashboard={"id": 9, "uuid": "not-ours"})
    monkeypatch.setattr(cli, "_client", lambda profile: client)
    code, out = run(capsys, "plan", str(path), "--profile", "p")
    assert out["dashboard"] == "blocked" and "held" in out   # blocked on ownership, not version
    assert [h["item"] for h in out["held"]][1] == "dashboard.theme"


def test_mcp_check_spec_holds_like_the_cli(repo, capsys, monkeypatch):
    pytest.importorskip("mcp")
    import chartwright.mcp_server as server
    from test_mcp_server import _call

    path = written(capsys, repo, "brand")
    monkeypatch.setattr(server, "_client", lambda profile: Client("5.0.0"))
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    out = _call("check_spec", {"spec_json": path.read_text(), "design": "off"}, "p")
    assert out["ok"] and [h["item"] for h in out["held"]][1] == "dashboard.theme"
    out = mcp_call("standards_check", spec_json=path.read_text(), superset_version="5.0.0")
    assert [h["item"] for h in out["held"]][1] == "dashboard.theme"


# -- a stated release is checked against the instance -------------------------------


def test_a_stated_release_the_instance_contradicts_is_refused(repo, capsys, monkeypatch):
    """--superset-version 5.0.0 against a 6.1.0 instance used to hold the theme back
    unchecked; holding by a release the instance doesn't run drops content it takes."""
    path = written(capsys, repo, "brand")
    import chartwright.apply as apply_mod

    monkeypatch.setattr(apply_mod, "apply", lambda *a, **k: pytest.fail("apply ran"))
    code, out = cli_check(capsys, monkeypatch, path, "6.1.0", "--design", "off",
                          "--superset-version", "5.0.0")
    assert code == 1 and out["stage"] == "version"
    assert out["errors"][0]["code"] == "superset_version_mismatch"
    assert "reports 6.1.0" in out["errors"][0]["detail"]
    code, out = run(capsys, "apply", str(path), "--profile", "p", "--design", "off",
                    "--superset-version", "5.0.0")
    assert code == 1 and out["errors"][0]["code"] == "superset_version_mismatch"
    code, out = run(capsys, "plan", str(path), "--profile", "p", "--superset-version", "5.0")
    assert code == 1 and out["errors"][0]["code"] == "superset_version_mismatch"


def test_a_stated_release_that_agrees_is_used(repo, capsys, monkeypatch):
    path = written(capsys, repo, "brand")
    code, out = cli_check(capsys, monkeypatch, path, "5.0.0", "--design", "off",
                          "--superset-version", "5.0")
    assert code == 0 and [h["item"] for h in out["held"]][1] == "dashboard.theme"
    assert "warnings" not in out


def test_a_stated_release_the_instance_cannot_confirm_holds_with_a_warning(repo, capsys,
                                                                          monkeypatch):
    path = written(capsys, repo, "brand")
    code, out = cli_check(capsys, monkeypatch, path, None, "--design", "off",
                          "--superset-version", "5.0.0")
    assert code == 0 and out["held"] and "unconfirmed" in out["warnings"][0]


def test_mcp_refuses_a_contradicted_release_too(repo, capsys, monkeypatch):
    pytest.importorskip("mcp")
    import chartwright.mcp_server as server
    from test_mcp_server import _call

    path = written(capsys, repo, "brand")
    monkeypatch.setattr(server, "_client", lambda profile: Client("6.1.0"))
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    for tool in ("check_spec", "plan_dashboard", "build_dashboard"):
        out = _call(tool, {"spec_json": path.read_text(), "superset_version": "5.0.0",
                           **({"design": "off"} if tool != "plan_dashboard" else {})}, "p")
        assert out["errors"][0]["code"] == "superset_version_mismatch", tool


# -- a floor suspends its file's locks ------------------------------------------------

FUTURE = """\
name: org
default: true
min_superset: "7.0"
content:
  footer:
    - [{markdown: "Acme Corp. Internal.", width: 12, height: 1}]
  label_colors: {Revenue: "#1FA8C9"}
locked:
  content: [footer, label_colors]
"""


def test_a_floor_over_locked_content_warns_when_the_folder_loads(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo", {"org.yaml": FUTURE}).parent
    standards = load_standards(repo / "standards")
    assert [w["code"] for w in standards.warnings] == ["floor_suspends_lock"] * 2
    assert "content.footer" in standards.warnings[0]["detail"]
    assert "min_superset 7.0.0" in standards.warnings[0]["detail"]
    path = written(capsys, repo)
    code, out = run(capsys, "standards", "check", str(path))
    assert out["standards_warnings"] == standards.warnings
    shown = run_ok(capsys, "standards", "show", "--standards", str(repo / "standards"),
                   "--json")
    assert shown["warnings"] == standards.warnings
    # Without the floor, or with nothing locked under it, there is nothing to say.
    assert load_standards(make_repo(tmp_path / "b", {
        "org.yaml": FUTURE.replace('min_superset: "7.0"\n', "")}).parent / "standards"
    ).warnings == []
    assert load_standards(repo.parent / "repo" / "standards").files["org"].min_superset


def test_holding_locked_content_names_it_and_says_the_lock_is_off(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo", {"org.yaml": FUTURE}).parent
    spec = load_spec(read(written(capsys, repo)))
    inst = for_instance(spec, source(repo), lambda: "6.1.0")
    assert {h["item"]: h.get("locked_by") for h in inst.held} == {
        "dashboard.label_colors[Revenue]": "org", "layout.footer[org][0]": "org"}
    assert "its lock does not apply here" in inst.warning
