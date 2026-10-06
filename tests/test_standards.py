"""Standards: rule settings a repository shares across its dashboards.

A `standards/` folder of YAML files, each a standard with an optional single parent
(`extends`, at most two files, so with the spec's own design block three layers) in
design.yaml's vocabulary plus `locked`. A spec follows the standard its
`design.standard` names, or the repository's default. Standards change advice only
(never the bundle), apply in every advice run, and are the same on every machine; a
locked rule can't be silenced by a lower layer, the spec's design.ignore, --ignore, the
per-machine design.yaml or the polish skip. `standards check` runs that advice over a
folder, setting design.yaml aside, and `--report` sums it up for the fleet.
"""

import hashlib
import json
from pathlib import Path

import pytest

from chartwright.cli import _advice_payload, main
from chartwright.design import advise, model
from chartwright.design.presets import Overlay, params_for
from chartwright.design.standards import (StandardsError, StandardsSource, discover,
                                          load_standards)
from chartwright.spec import load_spec

pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parent.parent
DS = {"database": "db", "table": "orders"}
AXIS = "size.axis-min-height"   # warn on DATA: height 4 < analytical's 6
COUNT = "default.count-format"  # info on DATA

DATA = {
    "spec_version": "1",
    "dashboard": {"title": "T", "slug": "t"},
    "charts": [{"type": "timeseries_line", "name": "L", "dataset": DS,
                "metrics": ["COUNT(*)"], "time_column": "ts", "height": 4,
                "x_axis_title": "Day", "y_axis_title": "Rows"}],
    "filters": [{"type": "time_range", "name": "Date", "default": "Last month"}],
    "layout": {"rows": [["L"]]},
}

ORG = """\
name: org
default: true
params: {fold_units: 40, recommended_heights: {table: 10, pie: 9}}
audiences: {executive: {kpi_height: 6}}
severity: {narrative.title-style: warn}
disable: [chart.dupe]
locked:
  rules: [size.axis-min-height]
  params: [fold_units]
"""

FINANCE = """\
name: finance
extends: org
params: {min_axis_height: 7, recommended_heights: {table: 12}}
audiences: {executive: {kpi_row_max: 4}}
severity: {size.axis-min-height: error, filters.time-default: warn}
disable: [chart.series-limit]
locked:
  rules: [layout.kpi-first]
"""


@pytest.fixture(autouse=True)
def no_overlay(tmp_path, monkeypatch):
    """No per-machine design.yaml unless a test writes one; no server standards."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(home))
    monkeypatch.delenv("CHARTWRIGHT_STANDARDS_DIR", raising=False)
    return home


def make_repo(root: Path, files: dict[str, str], marker: bool = True) -> Path:
    """A repository at `root` (a `.git` folder marks its root) with standards files."""
    if marker:
        (root / ".git").mkdir(parents=True, exist_ok=True)
    std = root / "standards"
    for rel, text in files.items():
        (std / rel).parent.mkdir(parents=True, exist_ok=True)
        (std / rel).write_text(text, encoding="utf-8")
    return std


def write_spec(path: Path, data: dict | None = None, **design) -> Path:
    data = json.loads(json.dumps(data or DATA))
    if design:
        data["design"] = design
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def run(capsys, *argv) -> tuple[int, dict | str]:
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    out = capsys.readouterr().out
    try:
        return exc.value.code, json.loads(out)
    except json.JSONDecodeError:
        return exc.value.code, out


def run_ok(capsys, *argv) -> dict | str:
    """For verbs that return without SystemExit on success."""
    main(list(argv))
    out = capsys.readouterr().out
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


@pytest.fixture
def repo(tmp_path):
    std = make_repo(tmp_path / "repo", {"org.yaml": ORG, "teams/finance.yaml": FINANCE})
    return std.parent


# -- resolution: extends and the merge per key ---------------------------------


def test_extends_merges_each_key_as_documented(repo):
    s = load_standards(repo / "standards")
    fin = s.get("finance")
    assert fin.chain == ["org", "finance"]
    # params and audiences: the lower layer overrides per parameter; recommended
    # heights per chart type.
    assert fin.params == {"fold_units": 40, "min_axis_height": 7,
                          "recommended_heights": {"table": 12, "pie": 9}}
    assert fin.audiences == {"executive": {"kpi_height": 6, "kpi_row_max": 4}}
    assert fin.origins["params.fold_units"] == "org"
    assert fin.origins["params.min_axis_height"] == "finance"
    assert fin.origins["params.recommended_heights.table"] == "finance"
    assert fin.origins["params.recommended_heights.pie"] == "org"
    # severity: per rule, the lower layer wins (a locked one only upward).
    assert fin.severity == {"narrative.title-style": "warn", AXIS: "error",
                            "filters.time-default": "warn"}
    assert fin.origins[f"severity.{AXIS}"] == "finance"
    # disable and locked add up; nothing below re-enables or unlocks.
    assert fin.disable == {"chart.dupe": "org", "chart.series-limit": "finance"}
    assert fin.locked_rules == {AXIS: "org", "layout.kpi-first": "finance"}
    assert fin.locked_params == {"fold_units": "org"}
    assert s.default == "org"


def test_the_standard_sits_between_the_preset_and_the_overlay(repo):
    fin = load_standards(repo / "standards").get("finance")
    p = params_for("executive", Overlay(params={"min_axis_height": 9, "fold_units": 99}), fin)
    assert p.kpi_height == 6 and p.kpi_row_max == 4        # the standard's audience block
    assert p.min_axis_height == 9                          # the overlay, on an open key
    assert p.fold_units == 40                              # locked: the overlay can't move it
    assert p.recommended_heights["table"] == 12
    assert params_for("executive") == params_for("executive", None, None)


@pytest.mark.parametrize("files,code,words", [
    ({"a.yaml": "name: a\nextends: b\n", "b.yaml": "name: b\nextends: a\n"},
     "standards_cycle", "a -> b -> a"),
    ({"a.yaml": "name: a\nextends: a\n"}, "standards_cycle", "extends itself"),
    ({"a.yaml": "name: a\nextends: ghost\n"}, "unknown_parent", "ghost"),
    ({"org.yaml": "name: org\n", "unit.yaml": "name: unit\nextends: org\n",
      "team.yaml": "name: team\nextends: unit\n", "squad.yaml": "name: squad\nextends: team\n"},
     "standards_depth", "org -> unit -> team -> squad"),
    ({"a.yaml": "name: a\nseverity: {size.pie-geometri: warn}\n"}, "unknown_rule",
     "size.pie-geometry"),
    ({"a.yaml": "name: a\ndisable: [nope.rule]\n"}, "unknown_rule", "nope.rule"),
    ({"a.yaml": "name: a\nlocked: {rules: [size.min-widht]}\n"}, "unknown_rule", "size.min-width"),
    ({"a.yaml": "name: a\ndisable: ['size.min-width@Orders']\n"}, "standards_file", "rule ids only"),
    ({"a.yaml": "name: a\nlocked: {params: [fold_unit]}\n"}, "standards_file", "fold_unit"),
    ({"a.yaml": "name: a\nparams: {fold_unit: 3}\n"}, "standards_file", "fold_unit"),
    ({"a.yaml": "name: a\naudiences: {board: {fold_units: 3}}\n"}, "standards_file", "board"),
    ({"a.yaml": "name: a\nseverity: {size.min-width: fatal}\n"}, "standards_file", "fatal"),
    ({"a.yaml": "name: a\ncss: x\n"}, "standards_file", "unknown keys ['css']"),
    ({"a.yaml": "extends: b\n"}, "standards_file", "`name` is required"),
    ({"a.yaml": "name: a\n", "b.yaml": "name: a\n"}, "standards_file", "two files name"),
    ({"a.yaml": "name: a\ndefault: true\n", "b.yaml": "name: b\ndefault: true\n"},
     "standards_file", "one default"),
    ({"a.yaml": "name: a b\n"}, "standards_file", "name"),
    ({"a.yaml": "name: [a\n"}, "standards_file", "not valid YAML"),
])
def test_a_broken_standards_directory_is_a_typed_error(tmp_path, files, code, words):
    std = make_repo(tmp_path / "r", files)
    with pytest.raises(StandardsError) as e:
        load_standards(std)
    assert e.value.code == code, str(e.value)
    assert words in str(e.value)


def test_a_three_file_chain_is_the_cap(tmp_path):
    """org -> unit -> team, with the spec's design block as the fourth layer."""
    std = make_repo(tmp_path / "r", {"org.yaml": "name: org\nparams: {fold_units: 40}\n",
                                     "unit.yaml": "name: unit\nextends: org\n"
                                                  "params: {min_axis_height: 7}\n",
                                     "team.yaml": "name: team\nextends: unit\n"
                                                  "params: {fold_units: 30}\n"})
    team = load_standards(std).get("team")
    assert team.chain == ["org", "unit", "team"]
    assert team.params == {"fold_units": 30, "min_axis_height": 7}
    assert team.origins["params.min_axis_height"] == "unit"


def test_a_four_file_chain_names_every_file(tmp_path):
    std = make_repo(tmp_path / "r", {"org.yaml": "name: org\n",
                                     "unit.yaml": "name: unit\nextends: org\n",
                                     "team.yaml": "name: team\nextends: unit\n",
                                     "squad.yaml": "name: squad\nextends: team\n"})
    with pytest.raises(StandardsError) as e:
        load_standards(std)
    assert e.value.code == "standards_depth"
    assert "a chain over three files" in str(e.value)
    for f in ("org.yaml", "unit.yaml", "team.yaml", "squad.yaml"):
        assert f in str(e.value)


def test_renamed_rule_ids_resolve_through_the_alias_table(tmp_path, monkeypatch):
    monkeypatch.setitem(model.RULE_ALIASES, "size.axis-height", AXIS)
    std = make_repo(tmp_path / "r", {"a.yaml": "name: a\nlocked: {rules: [size.axis-height]}\n"
                                               "severity: {size.axis-height: error}\n"})
    a = load_standards(std).get("a")
    assert a.locked_rules == {AXIS: "a"} and a.severity == {AXIS: "error"}


ORG_LOCKS = ("name: org\nseverity: {narrative.title-style: warn}\n"
             "params: {fold_units: 40, recommended_heights: {table: 10}}\n"
             "locked: {rules: [size.axis-min-height, narrative.title-style], "
             "params: [fold_units, recommended_heights]}\n")


@pytest.mark.parametrize("files", [
    # Locked with no value anywhere: the audience preset would decide it.
    {"org.yaml": "name: org\nlocked: {params: [min_axis_height]}\n"},
    # A value for some audiences only.
    {"org.yaml": "name: org\naudiences: {executive: {min_axis_height: 8}}\n"
                 "locked: {params: [min_axis_height]}\n"},
    # A team locks a parameter nobody set.
    {"org.yaml": "name: org\n",
     "team.yaml": "name: team\nextends: org\nlocked: {params: [fold_units]}\n"},
])
def test_a_lock_holds_a_value_not_a_slot(tmp_path, files):
    with pytest.raises(StandardsError) as e:
        load_standards(make_repo(tmp_path / "r", files))
    assert e.value.code == "standards_file" and "lock a value, not a slot" in str(e.value)


@pytest.mark.parametrize("files", [
    {"org.yaml": "name: org\nparams: {min_axis_height: 7}\nlocked: {params: [min_axis_height]}\n"},
    {"org.yaml": "name: org\naudiences: {executive: {min_axis_height: 8}, "
                 "analytical: {min_axis_height: 7}, operational: {min_axis_height: 6}}\n"
                 "locked: {params: [min_axis_height]}\n"},
    # The value comes from the parent; the team locks it.
    {"org.yaml": "name: org\nparams: {min_axis_height: 7}\n",
     "team.yaml": "name: team\nextends: org\nlocked: {params: [min_axis_height]}\n"},
])
def test_a_lock_on_a_value_set_at_or_above_the_locking_layer_loads(tmp_path, files):
    assert load_standards(make_repo(tmp_path / "r", files))


def test_a_looser_audience_cant_move_a_locked_threshold(tmp_path, capsys):
    """The reviewer's case: min_axis_height locked; a height-5 axis chart under the
    operational preset (whose own minimum is 5) still gets the locked minimum."""
    repo = make_repo(tmp_path / "r", {"org.yaml": "name: org\ndefault: true\n"
                                                  "params: {min_axis_height: 6}\n"
                                                  "locked: {rules: [size.axis-min-height], "
                                                  "params: [min_axis_height]}\n"}).parent
    data = json.loads(json.dumps(DATA))
    data["charts"][0]["height"] = 5
    spec = write_spec(repo / "specs" / "s.json", data, audience="operational")
    code, payload = run(capsys, "standards", "check", str(spec), "--strict")
    assert code == 1
    assert AXIS in [f["rule"] for f in payload["specs"][0]["findings"]]


@pytest.mark.parametrize("team,words", [
    ("disable: [size.axis-min-height]", "can't be disabled"),
    ("severity: {narrative.title-style: info}", "lowers narrative.title-style from warn to info"),
    # No level set above: the baseline is the most severe the rule emits.
    ("severity: {size.axis-min-height: info}", "lowers size.axis-min-height"),
    ("params: {fold_units: 99}", "params.fold_units is locked by 'org'"),
    ("audiences: {executive: {fold_units: 99}}", "audiences.executive.fold_units is locked"),
    ("params: {recommended_heights: {table: 3}}", "params.recommended_heights is locked"),
])
def test_a_lower_layer_cant_loosen_a_lock(tmp_path, team, words):
    std = make_repo(tmp_path / "r", {"org.yaml": ORG_LOCKS,
                                     "team.yaml": f"name: team\nextends: org\n{team}\n"})
    with pytest.raises(StandardsError) as e:
        load_standards(std)
    assert e.value.code == "locked" and words in str(e.value)
    assert "team.yaml" in str(e.value)


def test_a_lower_layer_may_raise_a_locked_severity(tmp_path):
    std = make_repo(tmp_path / "r", {"org.yaml": ORG_LOCKS, "team.yaml":
                                     "name: team\nextends: org\n"
                                     "severity: {narrative.title-style: error}\n"})
    assert load_standards(std).get("team").severity["narrative.title-style"] == "error"


@pytest.mark.parametrize("files", [
    {"org.yaml": "name: org\ndisable: [chart.dupe]\nlocked: {rules: [chart.dupe]}\n"},
    {"org.yaml": "name: org\ndisable: [chart.dupe]\n",
     "team.yaml": "name: team\nextends: org\nlocked: {rules: [chart.dupe]}\n"},
])
def test_a_rule_cant_be_locked_and_disabled(tmp_path, files):
    with pytest.raises(StandardsError) as e:
        load_standards(make_repo(tmp_path / "r", files))
    assert e.value.code == "locked" and "can't be disabled" in str(e.value)


def test_every_file_is_checked_even_one_no_spec_names(tmp_path):
    std = make_repo(tmp_path / "r", {"org.yaml": "name: org\ndefault: true\n",
                                     "unused.yaml": "name: unused\nextends: nowhere\n"})
    with pytest.raises(StandardsError, match="nowhere"):
        load_standards(std)


# -- discovery: one directory, at or above the spec, inside the repository ----


def test_discovery_walks_up_to_the_repository_root(repo):
    nested = repo / "specs" / "finance" / "q3"
    nested.mkdir(parents=True)
    assert discover(nested) == (repo / "standards").resolve()


def test_nothing_is_discovered_outside_a_repository(tmp_path):
    make_repo(tmp_path / "loose", {"org.yaml": "name: org\n"}, marker=False)
    (tmp_path / "loose" / "specs").mkdir()
    assert discover(tmp_path / "loose" / "specs") is None


def test_discovery_stops_at_the_repository_root(tmp_path):
    make_repo(tmp_path, {"org.yaml": "name: org\n"}, marker=False)   # above the repo
    (tmp_path / "inner" / ".git").mkdir(parents=True)
    assert discover(tmp_path / "inner") is None


def test_two_standards_directories_on_the_way_up_are_an_error(repo):
    make_repo(repo / "specs", {"x.yaml": "name: x\n"}, marker=False)
    with pytest.raises(StandardsError, match="two standards directories"):
        discover(repo / "specs")


UNRELATED = "# the team's coding standards\nlinters: [ruff, mypy]\nline_length: 100\n"


def test_an_unrelated_standards_folder_changes_nothing(tmp_path, capsys):
    """A repo whose standards/ holds other YAML (linters, style guides) never opted in:
    no folder is found, and advise and the strict check gate behave as without it."""
    repo = make_repo(tmp_path / "r", {"coding.yaml": UNRELATED, "ci/lint.yml": "- a\n- b\n"}).parent
    assert discover(repo) is None
    spec = write_spec(repo / "specs" / "s.json")
    code, payload = run(capsys, "advise", str(spec))
    assert code == 0 and payload == advise(load_spec(DATA)).payload()
    advice = _advice_payload(load_spec(DATA), strict=True,
                             standards=StandardsSource.for_cli(None, spec))
    assert advice == _advice_payload(load_spec(DATA), strict=True)


def test_a_standards_folder_still_fails_closed_on_any_other_yaml(tmp_path, capsys):
    """One file with a `name` makes the folder a standards folder; from then on every
    YAML file in it must be a valid standard."""
    repo = make_repo(tmp_path / "r", {"org.yaml": "name: org\n", "coding.yaml": UNRELATED}).parent
    assert discover(repo) == (repo / "standards").resolve()
    spec = write_spec(repo / "specs" / "s.json")
    code, payload = run(capsys, "advise", str(spec))
    assert code == 1 and payload["stage"] == "standards"
    assert "coding.yaml" in payload["errors"][0]["detail"]


def test_a_broken_yaml_file_alone_doesnt_qualify_a_folder(tmp_path):
    repo = make_repo(tmp_path / "r", {"x.yaml": "name: [a\n"}).parent
    assert discover(repo) is None


def test_a_folder_named_standards_without_yaml_is_not_a_standards_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "README.md").write_text("our standards", encoding="utf-8")
    assert discover(tmp_path) is None


# -- assignment: design.standard, the default, `standards assign` -------------


def test_a_spec_follows_its_named_standard_or_the_default(repo):
    source = StandardsSource(repo / "standards")
    assert source.standard_for(load_spec({**DATA, "design": {"standard": "finance"}})).via \
        == "design.standard"
    std = source.standard_for(load_spec(DATA))
    assert (std.name, std.via) == ("org", "default")


def test_no_default_means_no_standard_for_an_unassigned_spec(tmp_path):
    std = make_repo(tmp_path / "r", {"org.yaml": "name: org\n"})
    assert StandardsSource(std).standard_for(load_spec(DATA)) is None


def test_an_unknown_standard_is_named_with_a_suggestion(repo):
    with pytest.raises(StandardsError) as e:
        StandardsSource(repo / "standards").standard_for(
            load_spec({**DATA, "design": {"standard": "finanse"}}))
    assert e.value.code == "unknown_standard" and "did you mean 'finance'" in str(e.value)


def test_a_named_standard_without_a_directory_is_an_error_not_a_pass(tmp_path, capsys):
    spec = write_spec(tmp_path / "loose" / "s.json", standard="finance")
    code, payload = run(capsys, "advise", str(spec))
    assert code == 1 and payload["stage"] == "standards"
    assert payload["errors"][0]["code"] == "no_standards_dir"
    assert "--standards DIR" in payload["errors"][0]["detail"]


def test_the_field_is_validated_by_the_schema():
    with pytest.raises(Exception, match="pattern"):
        load_spec({**DATA, "design": {"standard": "../finance"}})


def test_assign_writes_the_field_and_leaves_the_rest(repo, capsys):
    a = write_spec(repo / "specs" / "a.json")
    b = write_spec(repo / "specs" / "b.json", standard="finance", audience="executive")
    c = write_spec(repo / "specs" / "c.json", standard="org")
    before_b = b.read_bytes()
    code, payload = run(capsys, "standards", "assign", str(repo / "specs"), "--standard", "finance")
    assert code == 0 and payload["ok"]
    assert payload["written"] == [{"spec": a.as_posix(), "was": None}, {"spec": c.as_posix(), "was": "org"}]
    assert payload["unchanged"] == [b.as_posix()]
    assert b.read_bytes() == before_b
    assert json.loads(a.read_text())["design"] == {"standard": "finance"}
    assert json.loads(c.read_text())["design"] == {"standard": "finance"}
    # Everything else in the spec is as it was.
    assert {k: v for k, v in json.loads(a.read_text()).items() if k != "design"} == DATA


def test_assign_refuses_an_unknown_standard_before_writing(repo, capsys):
    a = write_spec(repo / "specs" / "a.json")
    before = a.read_bytes()
    code, payload = run(capsys, "standards", "assign", str(a), "--standard", "ops")
    assert code == 1 and payload["errors"][0]["code"] == "unknown_standard"
    assert a.read_bytes() == before


def test_assign_never_writes_a_file_that_is_not_a_valid_spec(repo, capsys):
    bad = repo / "specs" / "bad.json"
    bad.parent.mkdir(parents=True)
    bad.write_text('{"spec_version": "1"}', encoding="utf-8")
    good = write_spec(repo / "specs" / "good.json")
    code, payload = run(capsys, "standards", "assign", str(repo / "specs" / "*.json"),
                        "--standard", "org")
    assert code == 1
    assert [e["spec"] for e in payload["errors"]] == [bad.as_posix()]
    assert bad.read_text() == '{"spec_version": "1"}'
    assert json.loads(good.read_text())["design"] == {"standard": "org"}


# -- advise, explain, check and apply apply the standard ----------------------


def test_advise_applies_the_standard_and_names_each_findings_layer(repo, capsys):
    spec = write_spec(repo / "specs" / "s.json", standard="finance")
    code, payload = run(capsys, "advise", str(spec))
    assert code == 1  # finance raises size.axis-min-height to error
    axis = next(f for f in payload["findings"] if f["rule"] == AXIS)
    assert (axis["severity"], axis["layer"], axis["locked"]) == ("error", "finance", True)
    count = next(f for f in payload["findings"] if f["rule"] == COUNT)
    assert (count["layer"], count["locked"]) == ("rulebook", False)
    assert payload["standard"] == {
        "name": "finance", "chain": ["org", "finance"], "via": "design.standard",
        "locked": {"rules": {"layout.kpi-first": "finance", AXIS: "org"},
                   "params": {"fold_units": "org"}}}


def test_the_standards_parameters_reach_the_rules(repo, capsys):
    # min_axis_height 7 from finance: a height-6 axis chart is short there.
    data = json.loads(json.dumps(DATA))
    data["charts"][0]["height"] = 6
    plain = advise(load_spec(data), overlay=Overlay())
    assert AXIS not in [f.rule for f in plain.findings]
    spec = write_spec(repo / "specs" / "s.json", data, standard="finance")
    _, payload = run(capsys, "advise", str(spec))
    assert AXIS in [f["rule"] for f in payload["findings"]]


def test_the_standards_disable_list_is_reported_as_ignored(repo, capsys):
    data = json.loads(json.dumps(DATA))
    data["charts"].append({**data["charts"][0], "name": "L2"})
    data["layout"] = {"rows": [["L", "L2"]]}
    assert "chart.dupe" in [f.rule for f in advise(load_spec(data), overlay=Overlay()).findings]
    spec = write_spec(repo / "specs" / "s.json", data)  # the default: org disables chart.dupe
    _, payload = run(capsys, "advise", str(spec))
    assert "chart.dupe" not in [f["rule"] for f in payload["findings"]]
    assert any(k.startswith("chart.dupe") for k in payload["ignored"])


def test_explain_names_the_standard_chain(repo, capsys):
    spec = write_spec(repo / "specs" / "s.json", standard="finance")
    text = run_ok(capsys, "explain", str(spec))
    assert text.splitlines()[0] == ("Design defaults (design brain 17, audience analytical, "
                                    "standard org -> finance)")
    payload = run_ok(capsys, "explain", str(spec), "--json")
    assert payload["standard"] == {"name": "finance", "chain": ["org", "finance"],
                                   "via": "design.standard"}


def test_check_and_apply_advice_carries_the_standard_and_fails_closed(repo, tmp_path):
    spec_path = write_spec(repo / "specs" / "s.json", standard="finance")
    spec = load_spec(json.loads(spec_path.read_text()))
    source = StandardsSource.for_cli(None, spec_path)
    advice = _advice_payload(spec, strict=True, standards=source)
    assert advice["standard"]["chain"] == ["org", "finance"]
    assert advice["counts"]["error"] == 1
    # A standard that can't be resolved degrades to an error note, which a strict gate
    # turns into a block (it fails closed), as a broken design.yaml does.
    from chartwright.design import gate_block

    broken = StandardsSource.for_cli(None, write_spec(tmp_path / "x" / "s.json",
                                                      standard="finance"))
    note = _advice_payload(spec, strict=True, standards=broken)
    assert note["errors"][0]["code"] == "no_standards_dir"
    assert gate_block(note) is not None


def test_the_standards_flag_names_the_directory(repo, tmp_path, capsys):
    spec = write_spec(tmp_path / "elsewhere" / "s.json", standard="finance")
    code, payload = run(capsys, "advise", str(spec), "--standards", str(repo / "standards"))
    assert payload["standard"]["chain"] == ["org", "finance"]


# -- locks against the spec, --ignore, the overlay and the polish skip --------


def test_design_ignore_cant_silence_a_locked_rule(repo, capsys):
    spec = write_spec(repo / "specs" / "s.json", standard="finance",
                      ignore=[f"{AXIS}@L", COUNT])
    code, payload = run(capsys, "advise", str(spec))
    assert code == 1
    assert AXIS in [f["rule"] for f in payload["findings"]]
    assert f"{COUNT}@L" in payload["ignored"]           # an open rule still ignores
    assert payload["standard"]["refused_ignores"] == [{"entry": f"{AXIS}@L", "locked_by": "org"}]
    # The gate names the lock instead of suggesting design.ignore for it.
    assert f"a standard locks {AXIS}, so design.ignore can't silence it" in \
        payload["errors"][0]["detail"]


def test_the_ignore_flag_cant_silence_a_locked_rule(repo, capsys):
    spec = write_spec(repo / "specs" / "s.json", standard="finance")
    code, payload = run(capsys, "advise", str(spec), "--ignore", AXIS)
    assert code == 1 and AXIS in [f["rule"] for f in payload["findings"]]
    assert payload["standard"]["refused_ignores"][0]["entry"] == AXIS


def test_a_fractional_height_doesnt_silence_a_locked_rule(repo, capsys):
    data = json.loads(json.dumps(DATA))
    data["charts"][0]["height"] = 4.5
    assert AXIS not in [f.rule for f in advise(load_spec(data), overlay=Overlay()).findings]
    spec = write_spec(repo / "specs" / "s.json", data, standard="finance")
    _, payload = run(capsys, "advise", str(spec))
    assert AXIS in [f["rule"] for f in payload["findings"]]
    assert "polished" not in payload


@pytest.mark.parametrize("overlay", [
    f"disable: [{AXIS}]\n", f"severity: {{{AXIS}: info}}\n", "params: {fold_units: 3}\n"])
def test_the_overlay_cant_loosen_a_lock_even_outside_a_strict_gate(repo, no_overlay, capsys,
                                                                   overlay):
    (no_overlay / "design.yaml").write_text(overlay, encoding="utf-8")
    spec = write_spec(repo / "specs" / "s.json", standard="finance")
    code, payload = run(capsys, "advise", str(spec))
    axis = next(f for f in payload["findings"] if f["rule"] == AXIS)
    assert (axis["severity"], axis["layer"]) == ("error", "finance")
    assert payload["overlay"]["strict"] is False
    assert payload["overlay"]["set_aside"] in (
        {"disable": [AXIS]}, {"severity": {AXIS: "info"}}, {"params": ["fold_units"]})
    assert payload["overlay"]["changed"] == []


def test_the_overlay_still_tunes_what_no_standard_locks(repo, no_overlay, capsys):
    (no_overlay / "design.yaml").write_text(f"severity: {{{COUNT}: warn}}\n", encoding="utf-8")
    spec = write_spec(repo / "specs" / "s.json", standard="finance")
    _, payload = run(capsys, "advise", str(spec))
    count = next(f for f in payload["findings"] if f["rule"] == COUNT)
    assert (count["severity"], count["layer"]) == ("warn", "overlay")
    assert payload["overlay"]["changed"] == [{"finding": f"{COUNT}@L",
                                              "severity": ["info", "warn"]}]


# -- lock edges ----------------------------------------------------------------


def test_a_renamed_rule_id_cant_slip_past_a_lock(repo, no_overlay, monkeypatch, capsys):
    """The ignore and disable lists resolve aliases, so the lock check must too."""
    monkeypatch.setitem(model.RULE_ALIASES, "size.axis-height", AXIS)
    (no_overlay / "design.yaml").write_text("disable: [size.axis-height]\n", encoding="utf-8")
    spec = write_spec(repo / "specs" / "s.json", standard="finance",
                      ignore=["size.axis-height@L"])
    code, payload = run(capsys, "advise", str(spec))
    assert code == 1 and AXIS in [f["rule"] for f in payload["findings"]]
    assert payload["standard"]["refused_ignores"] == [
        {"entry": "size.axis-height@L", "locked_by": "org"}]
    assert payload["overlay"]["set_aside"] == {"disable": ["size.axis-height"]}


@pytest.mark.parametrize("team", [
    "severity: {narrative.title-style: warn}",   # the level the org set
    "severity: {size.axis-min-height: warn}",    # the rule's own (most severe) level
])
def test_restating_a_locked_level_is_allowed(tmp_path, team):
    std = make_repo(tmp_path / "r", {"org.yaml": ORG_LOCKS,
                                     "team.yaml": f"name: team\nextends: org\n{team}\n"})
    assert load_standards(std).get("team")


def test_the_overlays_heights_cant_move_locked_recommended_heights(tmp_path):
    std = make_repo(tmp_path / "r", {"org.yaml": ORG_LOCKS})
    org = load_standards(std).get("org")
    overlay = Overlay(recommended_heights={"table": 3, "pie": 4},
                      params={"recommended_heights": {"table": 5}},
                      audiences={"executive": {"recommended_heights": {"table": 6}}})
    assert params_for("executive", overlay, org).recommended_heights == {"table": 10}
    assert params_for("executive", overlay).recommended_heights == {"table": 6, "pie": 4}


def test_the_overlays_audience_block_cant_move_a_locked_parameter(repo):
    fin = load_standards(repo / "standards").get("finance")
    overlay = Overlay(audiences={"executive": {"fold_units": 3, "kpi_height": 2}})
    p = params_for("executive", overlay, fin)
    assert p.fold_units == 40 and p.kpi_height == 2


def test_a_fleet_run_over_two_repositories_is_refused(repo, tmp_path, capsys):
    other = make_repo(tmp_path / "other", {"org.yaml": "name: org\n"}).parent
    a = write_spec(repo / "specs" / "a.json")
    b = write_spec(other / "specs" / "b.json")
    code, payload = run(capsys, "standards", "check", str(a), str(b))
    assert code == 1 and payload["errors"][0]["code"] == "standards_dir"
    assert "different standards directories" in payload["errors"][0]["detail"]
    loose = write_spec(tmp_path / "loose" / "c.json")
    code, payload = run(capsys, "standards", "check", str(a), str(loose))
    assert code == 1 and "none for" in payload["errors"][0]["detail"]


def test_the_highest_layer_owns_a_repeated_disable_or_lock(tmp_path, capsys):
    """Below the first layer to disable or lock a rule, repeating it changes nothing,
    so the first layer is the one named."""
    std = make_repo(tmp_path / "r", {
        "org.yaml": "name: org\ndisable: [chart.dupe]\nlocked: {rules: [size.axis-min-height]}\n",
        "team.yaml": "name: team\nextends: org\ndefault: true\ndisable: [chart.dupe]\n"
                     "locked: {rules: [size.axis-min-height]}\n"})
    team = load_standards(std).get("team")
    assert team.disable == {"chart.dupe": "org"}
    assert team.locked_rules == {AXIS: "org"}
    spec = write_spec(std.parent / "specs" / "s.json", ignore=[AXIS])
    _, payload = run(capsys, "advise", str(spec))
    assert payload["standard"]["refused_ignores"] == [{"entry": AXIS, "locked_by": "org"}]
    shown = run_ok(capsys, "standards", "show", "team", "--standards", str(std), "--json")
    assert shown["disable"] == {"chart.dupe": {"layer": "org"}}
    assert shown["locked"]["rules"] == {AXIS: "org"}


# -- standards check: exit codes, immunity, MCP parity, the report ------------


def test_standards_check_fails_on_an_error_finding(repo, capsys):
    write_spec(repo / "specs" / "fin.json", standard="finance")  # axis is error there
    write_spec(repo / "specs" / "org.json")                      # axis stays warn
    code, payload = run(capsys, "standards", "check", str(repo / "specs"))
    assert code == 1 and payload["ok"] is False
    by_spec = {Path(e["spec"]).name: e for e in payload["specs"]}
    assert by_spec["fin.json"]["ok"] is False and by_spec["org.json"]["ok"] is True
    assert by_spec["fin.json"]["chain"] == ["org", "finance"]
    assert by_spec["org.json"]["via"] == "default"
    assert by_spec["fin.json"]["errors"][0]["code"] == "design_gate"
    assert payload["totals"] == {"specs": 2, "passed": 1, "failed": 1}


def test_standards_check_strict_fails_on_a_warn(repo, capsys):
    write_spec(repo / "specs" / "org.json")
    code, _ = run(capsys, "standards", "check", str(repo / "specs"))
    assert code == 0
    code, payload = run(capsys, "standards", "check", str(repo / "specs"), "--strict")
    assert code == 1 and payload["specs"][0]["errors"][0]["code"] == "design_gate"


def test_standards_check_passes_a_clean_spec(repo, capsys):
    data = json.loads(json.dumps(DATA))
    data["charts"][0]["height"] = 8
    write_spec(repo / "specs" / "ok.json", data)
    code, payload = run(capsys, "standards", "check", str(repo / "specs"), "--strict")
    assert code == 0 and payload["ok"] is True, payload


@pytest.mark.parametrize("overlay", [
    f"disable: [{AXIS}]\n", f"severity: {{{AXIS}: info}}\n",
    "params: {min_axis_height: 2}\n", f"severity: {{{COUNT}: error}}\n", "disable: [nonsense\n"])
def test_standards_check_reads_nothing_from_design_yaml(repo, no_overlay, capsys, overlay):
    write_spec(repo / "specs" / "fin.json", standard="finance")
    write_spec(repo / "specs" / "org.json")
    _, clean = run(capsys, "standards", "check", str(repo / "specs"))
    (no_overlay / "design.yaml").write_text(overlay, encoding="utf-8")
    code, payload = run(capsys, "standards", "check", str(repo / "specs"))
    assert payload.pop("overlay") == {"path": (no_overlay / "design.yaml").as_posix(), "set_aside": True}
    assert payload == clean and code == 1


def test_standards_check_reports_a_bad_spec_and_an_unknown_standard(repo, capsys):
    (repo / "specs").mkdir()
    (repo / "specs" / "broken.json").write_text("{", encoding="utf-8")
    write_spec(repo / "specs" / "ghost.json", standard="ghost")
    code, payload = run(capsys, "standards", "check", str(repo / "specs"))
    assert code == 1
    codes = {Path(e["spec"]).name: e["errors"][0]["code"] for e in payload["specs"]}
    assert codes == {"broken.json": "unreadable_spec", "ghost.json": "unknown_standard"}


def test_standards_check_fails_on_a_broken_standards_file(repo, capsys):
    (repo / "standards" / "bad.yaml").write_text("name: bad\nextends: org\n"
                                                 f"disable: [{AXIS}]\n", encoding="utf-8")
    write_spec(repo / "specs" / "org.json")
    code, payload = run(capsys, "standards", "check", str(repo / "specs"))
    assert code == 1 and payload["errors"][0]["code"] == "locked"


def test_standards_check_needs_a_standards_directory(tmp_path, capsys):
    spec = write_spec(tmp_path / "s.json")
    code, payload = run(capsys, "standards", "check", str(spec))
    assert code == 1 and payload["errors"][0]["code"] == "no_standards_dir"


@pytest.mark.parametrize("other", ['{"name": "web", "version": "1.0.0"}', "[1, 2]", '"x"'])
def test_json_files_that_are_not_specs_are_skipped_and_listed(repo, capsys, other):
    """A folder of specs often holds other JSON (package.json, tsconfig.json): a file
    without a top-level spec_version is no spec, so it is listed, never failed."""
    spec = write_spec(repo / "specs" / "ok.json", standard="org",
                      data={**DATA, "charts": [{**DATA["charts"][0], "height": 8}]})
    pkg = repo / "specs" / "package.json"
    pkg.write_text(other, encoding="utf-8")
    code, payload = run(capsys, "standards", "check", str(repo / "specs"))
    assert code == 0 and payload["ok"] is True, payload
    assert [e["spec"] for e in payload["specs"]] == [spec.as_posix()]
    assert payload["skipped"] == [pkg.as_posix()]
    assert payload["totals"] == {"specs": 1, "passed": 1, "failed": 0}
    before = pkg.read_bytes()
    code, payload = run(capsys, "standards", "assign", str(repo / "specs"), "--standard", "finance")
    assert code == 0 and payload["skipped"] == [pkg.as_posix()]
    assert [w["spec"] for w in payload["written"]] == [spec.as_posix()]
    assert pkg.read_bytes() == before


def test_a_folder_of_no_specs_is_an_error(repo, capsys):
    (repo / "specs").mkdir()
    (repo / "specs" / "package.json").write_text("{}", encoding="utf-8")
    code, payload = run(capsys, "standards", "check", str(repo / "specs"))
    assert code == 1 and payload["errors"][0]["code"] == "no_specs"


def test_the_fleet_report(repo, capsys):
    write_spec(repo / "specs" / "fin.json", standard="finance", ignore=[AXIS])
    write_spec(repo / "specs" / "org.json")
    (repo / "specs" / "broken.json").write_text('{"spec_version": "1"}', encoding="utf-8")
    (repo / "specs" / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    code, payload = run(capsys, "standards", "check", str(repo / "specs"), "--report")
    assert code == 1
    assert set(payload) == {"stage", "report", "ok", "strict", "standards_dir", "specs",
                            "skipped", "totals"}
    assert payload["skipped"] == [(repo / "specs" / "package.json").as_posix()]
    rows = {Path(r["spec"]).name: r for r in payload["specs"]}
    assert rows["fin.json"] == {
        "spec": (repo / "specs" / "fin.json").as_posix(), "standard": "finance", "ok": False,
        "counts": {"error": 1, "warn": 0, "info": 1},
        "by_rule": {COUNT: {"info": 1}, AXIS: {"error": 1}}, "locks_hit": [AXIS]}
    assert rows["org.json"]["ok"] is True and rows["org.json"]["by_rule"] == {
        COUNT: {"info": 1}, AXIS: {"warn": 1}}
    assert rows["broken.json"]["errors"][0]["code"] == "schema"
    assert payload["totals"] == {
        "specs": 3, "passed": 1, "failed": 2,
        "counts": {"error": 1, "warn": 1, "info": 2},
        "by_rule": {COUNT: {"info": 2}, AXIS: {"error": 1, "warn": 1}},
        "by_standard": {"(none)": {"specs": 1, "passed": 0, "failed": 1},
                        "finance": {"specs": 1, "passed": 0, "failed": 1},
                        "org": {"specs": 1, "passed": 1, "failed": 0}},
        "locks_hit": {AXIS: 2}}


def test_standards_show_gives_each_key_its_layer_and_lock(repo, capsys):
    payload = run_ok(capsys, "standards", "show", "finance",
                     "--standards", str(repo / "standards"), "--json")
    assert [c["name"] for c in payload["chain"]] == ["org", "finance"]
    assert payload["params"]["fold_units"] == {"value": 40, "layer": "org", "locked": True}
    assert payload["params"]["recommended_heights.table"] == {"value": 12, "layer": "finance",
                                                              "locked": False}
    assert payload["severity"][AXIS] == {"value": "error", "layer": "finance", "locked": True}
    assert payload["disable"] == {"chart.dupe": {"layer": "org"},
                                  "chart.series-limit": {"layer": "finance"}}
    assert payload["audiences"]["executive"]["kpi_row_max"]["layer"] == "finance"
    text = run_ok(capsys, "standards", "show", "--standards", str(repo / "standards"))
    assert text.startswith("Standard org: org  (the default)")
    assert "locked" in text


def test_standards_show_for_a_spec(repo, capsys):
    spec = write_spec(repo / "specs" / "s.json", standard="finance")
    payload = run_ok(capsys, "standards", "show", "--for", str(spec), "--json")
    assert payload["standard"] == "finance" and payload["via"] == "design.standard"


# -- MCP: the same results, from the server's configured directory ------------


def mcp_call(tool: str, **kwargs) -> dict:
    pytest.importorskip("mcp")
    import asyncio

    from chartwright.mcp_server import mcp

    out = asyncio.run(mcp.call_tool(tool, kwargs))
    if hasattr(out, "content"):
        text = out.content[0].text
    else:
        text = out[0][0].text if isinstance(out, tuple) else out[0].text
    return json.loads(text)


def test_mcp_standards_check_matches_the_cli(repo, monkeypatch, capsys):
    spec = write_spec(repo / "specs" / "fin.json", standard="finance", ignore=[f"{AXIS}@L"])
    _, cli = run(capsys, "standards", "check", str(spec))
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    out = mcp_call("standards_check", spec_json=spec.read_text())
    entry = {k: v for k, v in cli["specs"][0].items() if k != "spec"}
    assert out == {"stage": "standards", "standards_dir": out["standards_dir"],
                   "strict": False, **entry}
    assert Path(out["standards_dir"]).resolve() == (repo / "standards").resolve()


def test_mcp_advice_tools_apply_the_servers_standard(repo, monkeypatch, capsys):
    spec = write_spec(repo / "specs" / "fin.json", standard="finance")
    _, cli = run(capsys, "advise", str(spec))
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    assert mcp_call("advise_spec", spec_json=spec.read_text()) == {
        k: v for k, v in cli.items()}
    explained = mcp_call("explain_spec", spec_json=spec.read_text())
    assert explained["standard"]["chain"] == ["org", "finance"]
    fixed = mcp_call("fix_spec", spec_json=spec.read_text())
    assert fixed["advice"]["standard"]["name"] == "finance"
    shown = mcp_call("standards_show", spec_json=spec.read_text())
    assert shown == run_ok(capsys, "standards", "show", "--for", str(spec), "--json") | {
        "standards_dir": shown["standards_dir"]}


def test_mcp_without_a_configured_directory_says_how_to_set_one(repo, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # no repository here: nothing to discover
    spec = write_spec(repo / "specs" / "fin.json", standard="finance")
    out = mcp_call("advise_spec", spec_json=spec.read_text())
    assert out["ok"] is False and out["errors"][0]["code"] == "no_standards_dir"
    detail = out["errors"][0]["detail"]
    assert "CHARTWRIGHT_STANDARDS_DIR" in detail and "working directory" in detail
    assert "\n" not in detail
    out = mcp_call("standards_check", spec_json=json.dumps(DATA))
    assert out["errors"][0]["code"] == "no_standards_dir"
    # A spec that names no standard gets no note at all, as on the CLI.
    plain = mcp_call("advise_spec", spec_json=json.dumps(DATA))
    assert "standard" not in plain and "errors" not in plain


def test_mcp_discovers_from_its_working_directory_without_the_variable(repo, monkeypatch,
                                                                         capsys):
    """Started inside the repository, the server applies what the CLI applies: here the
    default standard to a spec that names none."""
    spec = write_spec(repo / "specs" / "s.json")
    _, cli = run(capsys, "advise", str(spec))
    assert cli["standard"]["name"] == "org"
    monkeypatch.chdir(repo / "specs")
    assert mcp_call("advise_spec", spec_json=spec.read_text()) == cli
    checked = mcp_call("standards_check", spec_json=spec.read_text())
    assert checked["standard"] == "org" and checked["via"] == "default"


# -- the team without standards, and the bundle -------------------------------


def test_no_standards_directory_changes_nothing(tmp_path, capsys):
    """The path of a team without standards: no new keys anywhere in advice."""
    spec = write_spec(tmp_path / "s.json")
    _, payload = run(capsys, "advise", str(spec))
    assert "standard" not in payload
    assert all("layer" not in f and "locked" not in f for f in payload["findings"])
    assert payload == advise(load_spec(DATA)).payload()
    text = run_ok(capsys, "explain", str(spec))
    assert text.splitlines()[0] == "Design defaults (design brain 17, audience analytical)"


def _bundle_hash(data: dict) -> str:
    from chartwright.compiler import compile_bundle
    from chartwright.testing import stub_resolution

    spec = load_spec(data)
    return hashlib.sha256(compile_bundle(spec, stub_resolution(spec))).hexdigest()


@pytest.mark.parametrize("path", sorted([*REPO.glob("examples/*.json"),
                                         *REPO.glob("tests/fixtures/*.json")]),
                         ids=lambda p: p.name)
def test_design_standard_never_reaches_the_bundle(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    tagged = json.loads(json.dumps(data))
    tagged.setdefault("design", {})["standard"] = "finance"
    assert _bundle_hash(tagged) == _bundle_hash(data)


def test_the_example_in_the_design_brain_page_is_what_the_tool_does(tmp_path, monkeypatch,
                                                                    capsys):
    """DESIGN-BRAIN.md sec.18's two files, exactly as printed, resolve as described, and
    the `standards show` output printed there is what the command prints for them."""
    text = (REPO / "docs" / "DESIGN-BRAIN.md").read_text(encoding="utf-8")
    section = text[text.index("## 18. Standards"):]
    blocks = [b.split("```", 1)[0] for b in section.split("```yaml\n")[1:3]]
    files = {}
    for block in blocks:
        first = block.splitlines()[0]
        assert first.startswith("# standards/"), first
        files[first[len("# standards/"):].strip()] = block
    std = make_repo(tmp_path / "r", files)
    resolved = load_standards(std)
    assert resolved.default == "org"
    assert resolved.get("finance").chain == ["org", "finance"]
    printed = section.split("```\nStandard finance", 1)[1].split("```", 1)[0]
    monkeypatch.chdir(tmp_path / "r")
    assert run_ok(capsys, "standards", "show", "finance") == "Standard finance" + printed
