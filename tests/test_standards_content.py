"""Standards content: spec content a standard writes into its specs, and who owns it.

`chartwright standards apply` writes a standard's header and footer rows, CSS blocks,
colour scheme, label colours, certification and number formats into each spec that
follows it, recording each item in design.standard_written. That record is design.filled's
"value written" state machine per item: the spec still holds the value written, the
standard's (refreshed); an unlocked item edited, the author's (released); deleted, a
tombstone (never written again); a locked item that differs, a violation `standards
check` reports and only `apply --locked` rewrites. Locks are recomputed from the files.
Compile never reads any of it, and a spec without standards content builds the same bytes.
"""

import hashlib
import json
from pathlib import Path

import pytest

from chartwright.design import advise, advise_and_fix
from chartwright.design import content as C
from chartwright.design.standards import StandardsError, load_standards
from chartwright.spec import load_spec
from test_standards import make_repo, mcp_call, run, run_ok, write_spec

pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parent.parent
DS = {"database": "db", "table": "orders"}

DATA = {
    "spec_version": "1",
    "dashboard": {"title": "T", "slug": "t", "css": ".mine { color: red; }"},
    "charts": [{"type": "big_number_total", "name": "K", "dataset": DS,
                "metric": "COUNT(*) AS Orders"},
               {"type": "big_number_total", "name": "R", "dataset": DS,
                "metric": "SUM(revenue) AS Revenue"}],
    "layout": {"rows": [["K", "R"]], "footer": [[{"markdown": "my note", "width": 12}]]},
}

ORG = """\
name: org
default: true
classifications: [public, internal, confidential]
content:
  footer:
    - [{markdown: "Confidential. Acme Corp.", width: 12, height: 1}]
  footer_by_classification:
    confidential:
      - [{markdown: "Named recipients only.", width: 12, height: 1}]
  header_by_lifecycle:
    deprecated:
      - [{markdown: "Deprecated: use the successor.", width: 12, height: 1}]
  css: |
    .dashboard-markdown { font-family: Inter; }
  label_colors: {Revenue: "#1FA8C9"}
  certified_by: Data Platform
locked:
  content: [footer, css]
"""

FINANCE = """\
name: finance
extends: org
content:
  header:
    - [{markdown: "Finance", width: 12, height: 1}]
  css: |
    .dashboard-markdown h2 { color: #003366; }
  color_scheme: supersetColors
  label_colors: {Cost: "#FF0000"}
  number_format: {Orders: ",.0f"}
"""

ORG_FOOTER = [{"markdown": "Confidential. Acme Corp.", "width": 12, "height": 1}]
FIN_HEADER = [{"markdown": "Finance", "width": 12, "height": 1}]


@pytest.fixture(autouse=True)
def no_overlay(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(home))
    monkeypatch.delenv("CHARTWRIGHT_STANDARDS_DIR", raising=False)


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo", {"org.yaml": ORG, "teams/finance.yaml": FINANCE}).parent


def spec_file(repo, name="s.json", data=None, **design) -> Path:
    return write_spec(repo / "specs" / name, data or DATA, **design)


def apply(capsys, *argv) -> tuple[int, dict]:
    return run(capsys, "standards", "apply", *argv, "--json")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def edit(path: Path, fn) -> None:
    data = read(path)
    fn(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def findings(capsys, path, rule=None) -> list[dict]:
    _, out = run(capsys, "standards", "check", str(path))
    found = out["specs"][0]["findings"]
    return [f for f in found if rule is None or f["rule"] == rule]


def std(repo, name="finance"):
    return load_standards(repo / "standards").get(name)


# -- the standards file -------------------------------------------------------


def test_content_resolves_through_extends(repo):
    fin = std(repo)
    assert [layer for layer, _ in fin.content_layers] == ["org", "finance"]
    assert fin.content_locks == {"footer": ["org"], "css": ["org"]}
    assert fin.classifications == ["public", "internal", "confidential"]
    # Locking content locks the rule that reports it, so nothing below silences it.
    assert fin.locked_rules["standard.content-locked"] == "org"


@pytest.mark.parametrize("org, words", [
    ("content: {footer: [[K]]}", "names a chart"),
    ("content: {logo: x.png}", "unknown keys ['logo']"),
    ("content: {css: '/* cw:std org abc */'}", "cw:std or cw:end marker"),
    ("content: {label_colors: {Revenue: red}}", "#RRGGBB"),
    ("content: {certification_details: Reviewed}", "without content.certified_by"),
    ("locked: {content: [footer]}", "locks content.footer without a value"),
    ("locked: {content: [logo]}", "unknown slots ['logo']"),
    ("content: {header_by_lifecycle: {retired: [[{markdown: x}]]}}", "unknown states"),
    ("classifications: [public]\ncontent: {footer_by_classification: {secret: [[{markdown: x}]]}}",
     "lacks"),
    ("content: {footer: [[{markdown: a, width: 8}, {markdown: b, width: 8}]]}", "sum to at most"),
])
def test_a_broken_content_block_names_the_file(tmp_path, org, words):
    std_dir = make_repo(tmp_path / "r", {"org.yaml": f"name: org\n{org}\n"})
    with pytest.raises(StandardsError) as e:
        load_standards(std_dir)
    assert words in str(e.value) and "org.yaml" in str(e.value)


@pytest.mark.parametrize("team, words", [
    ("content: {color_scheme: bnbColors}", "locked by 'org'"),
    ("content: {label_colors: {Revenue: '#000000'}}", "which 'org' locks"),
    ("classifications: [public, secret]", "only narrow it"),
    ("disable: [standard.content-locked]", "can't be disabled"),
])
def test_a_lower_layer_cant_loosen_locked_content(tmp_path, team, words):
    org = ("name: org\nclassifications: [public, internal]\n"
           "content: {color_scheme: supersetColors, label_colors: {Revenue: '#1FA8C9'}, "
           "footer: [[{markdown: Legal}]]}\n"
           "locked: {content: [color_scheme, label_colors, footer]}\n")
    std_dir = make_repo(tmp_path / "r", {"org.yaml": org,
                                         "team.yaml": f"name: team\nextends: org\n{team}\n"})
    with pytest.raises(StandardsError) as e:
        load_standards(std_dir)
    assert e.value.code == "locked" and words in str(e.value)


def test_a_lower_layer_may_add_keys_rows_and_blocks_under_a_lock(tmp_path):
    org = ("name: org\ncontent: {label_colors: {Revenue: '#1FA8C9'}, css: 'a {}', "
           "footer: [[{markdown: Legal}]]}\nlocked: {content: [label_colors, css, footer]}\n")
    team = ("name: team\nextends: org\ncontent: {label_colors: {Cost: '#FF0000'}, css: 'b {}', "
            "footer: [[{markdown: Team}]]}\n")
    std_dir = make_repo(tmp_path / "r", {"org.yaml": org, "team.yaml": team})
    t = load_standards(std_dir).get("team")
    items = {i.id: i for i in C.expected_items(t, load_spec(DATA))}
    assert items["dashboard.label_colors[Revenue]"].locked_by == "org"
    assert items["dashboard.label_colors[Cost]"].locked_by is None
    assert items["dashboard.css[org]"].locked_by == "org"
    assert items["dashboard.css[team]"].locked_by is None
    assert items["layout.footer[team][0]"].locked_by is None


# -- apply: writing content, and the record -----------------------------------


def test_apply_writes_every_slot_and_records_each_item(repo, capsys):
    path = spec_file(repo, standard="finance")
    code, out = apply(capsys, str(path))
    assert code == 0 and out["ok"] and out["totals"]["written"] == 1
    data = read(path)
    assert data["layout"]["header"] == [FIN_HEADER]
    # The author's footer row stays first; the org's legal row sits at the very bottom.
    assert data["layout"]["footer"] == [[{"markdown": "my note", "width": 12}], ORG_FOOTER]
    assert data["dashboard"]["css"] == (
        "/* cw:std org " + C.content_hash(".dashboard-markdown { font-family: Inter; }")
        + " */\n.dashboard-markdown { font-family: Inter; }\n/* cw:end org */\n"
        "/* cw:std finance " + C.content_hash(".dashboard-markdown h2 { color: #003366; }")
        + " */\n.dashboard-markdown h2 { color: #003366; }\n/* cw:end finance */\n"
        ".mine { color: red; }")
    assert data["dashboard"]["color_scheme"] == "supersetColors"
    assert data["dashboard"]["label_colors"] == {"Cost": "#FF0000", "Revenue": "#1FA8C9"}
    assert data["dashboard"]["certified_by"] == "Data Platform"
    # A number format reaches the chart whose metric the standard names, and no other.
    assert data["charts"][0]["number_format"] == ",.0f"
    assert "number_format" not in data["charts"][1]
    written = data["design"]["standard_written"]
    assert list(written) == sorted(written)
    assert written["layout.footer[org][0]"] == {"layer": "org",
                                                "hash": C.content_hash(ORG_FOOTER)}
    assert written["dashboard.color_scheme"] == {"layer": "finance", "value": "supersetColors"}
    assert written["charts[K].number_format"] == {"layer": "finance", "value": ",.0f"}
    assert set(written) == {
        "layout.header[finance][0]", "layout.footer[org][0]", "dashboard.css[org]",
        "dashboard.css[finance]", "dashboard.color_scheme", "dashboard.certified_by",
        "dashboard.label_colors[Cost]", "dashboard.label_colors[Revenue]",
        "charts[K].number_format"}
    assert findings(capsys, path, "standard.content-stale") == []
    assert findings(capsys, path, "standard.content-locked") == []


def test_a_second_apply_is_a_no_op(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    before = path.read_bytes()
    code, out = apply(capsys, str(path))
    assert code == 0 and out["totals"] == {"specs": 1, "written": 0, "unchanged": 1,
                                           "stale": 0, "locked_stale": 0}
    assert out["specs"][0]["changes"] == [] and path.read_bytes() == before


def test_apply_is_deterministic_across_copies(repo, capsys):
    a = spec_file(repo, "a.json", standard="finance")
    b = spec_file(repo, "b.json", standard="finance")
    apply(capsys, str(a), str(b))
    assert "standard_written" in read(a)["design"]
    assert a.read_bytes() == b.read_bytes()


def test_apply_touches_only_files_whose_content_changed(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    # Reformat the file (same data, other bytes): apply must leave it alone.
    path.write_text(json.dumps(read(path)), encoding="utf-8")
    before = path.read_bytes()
    _, out = apply(capsys, str(path))
    assert out["specs"][0]["written"] is False and path.read_bytes() == before


def test_a_spec_that_follows_no_standard_is_left_alone(tmp_path, capsys):
    org = "name: org\ncontent: {footer: [[{markdown: Legal}]]}\n"   # no default: true
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo)
    before = path.read_bytes()
    code, out = apply(capsys, str(path))
    assert code == 0 and out["no_standard"] == [str(path)] and path.read_bytes() == before


def test_no_standards_directory_changes_nothing(tmp_path, capsys):
    (tmp_path / "r" / ".git").mkdir(parents=True)
    path = write_spec(tmp_path / "r" / "s.json", DATA)
    code, out = run(capsys, "standards", "apply", str(path))
    assert code == 1 and out["errors"][0]["code"] == "no_standards_dir"
    payload = run(capsys, "advise", str(path))[1]
    assert not any(f["rule"].startswith("standard.") for f in payload["findings"])


# -- ownership, item by item --------------------------------------------------


def test_an_unlocked_row_the_author_edits_is_released_and_never_added_again(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["header"][0][0].update(markdown="Finance (ours)"))
    _, out = apply(capsys, str(path))
    data = read(path)
    assert data["layout"]["header"] == [[{"markdown": "Finance (ours)", "width": 12,
                                          "height": 1}]]
    assert data["design"]["standard_written"]["layout.header[finance][0]"] is None
    # Released, not deleted: the edited row (same shape, other text) stands where it was.
    ours = [{"markdown": "Finance (ours)", "width": 12, "height": 1}]
    assert {"item": "layout.header[finance][0]", "action": "release", "layer": "finance",
            "was": C.content_hash(FIN_HEADER), "to": C.content_hash(ours)} in out["specs"][0][
        "changes"]
    released = findings(capsys, path, "standard.content-released")
    assert [f["where"] for f in released] == ["layout.header[finance][0]"]
    # The standard changes its row: the author's row is untouched, nothing added beside it.
    std_file = repo / "standards" / "teams" / "finance.yaml"
    std_file.write_text(FINANCE.replace('"Finance"', '"Finance v2"'), encoding="utf-8")
    apply(capsys, str(path))
    assert read(path)["layout"]["header"] == data["layout"]["header"]


def test_an_unlocked_row_the_author_deletes_is_a_tombstone(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"].pop("header"))
    apply(capsys, str(path))
    data = read(path)
    assert "header" not in data["layout"]
    assert data["design"]["standard_written"]["layout.header[finance][0]"] is None
    # Deleting the null entry takes the standard's row again.
    edit(path, lambda d: d["design"]["standard_written"].pop("layout.header[finance][0]"))
    apply(capsys, str(path))
    assert read(path)["layout"]["header"] == [FIN_HEADER]


def test_a_locked_row_the_author_edits_is_a_violation_only_locked_rewrites(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["footer"][1][0].update(markdown="Share freely"))
    locked = findings(capsys, path, "standard.content-locked")
    assert len(locked) == 1 and locked[0]["severity"] == "error"
    assert locked[0]["where"] == "layout.footer[org][0]" and "org locks" in locked[0]["detail"]
    assert "standards apply --locked" in locked[0]["detail"]
    # apply reports it and leaves it (exit 1), keeping the record.
    code, out = apply(capsys, str(path))
    assert code == 1 and out["specs"][0]["locked"] == ["layout.footer[org][0]"]
    data = read(path)
    assert data["layout"]["footer"][1][0]["markdown"] == "Share freely"
    assert data["design"]["standard_written"]["layout.footer[org][0]"] is not None
    # --locked puts it back in place, as a visible change with what was there.
    code, out = apply(capsys, str(path), "--locked")
    assert code == 0
    change = next(c for c in out["specs"][0]["changes"] if c["item"] == "layout.footer[org][0]")
    assert change["action"] == "rewrite" and change["locked_by"] == "org"
    assert change["was"] == C.content_hash([{"markdown": "Share freely", "width": 12,
                                             "height": 1}])
    assert read(path)["layout"]["footer"] == [[{"markdown": "my note", "width": 12}], ORG_FOOTER]
    assert findings(capsys, path, "standard.content-locked") == []


def test_a_locked_row_the_author_deletes_is_a_violation(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["footer"].pop())
    assert [f["where"] for f in findings(capsys, path, "standard.content-locked")] == [
        "layout.footer[org][0]"]
    code, _ = apply(capsys, str(path), "--locked")
    assert code == 0 and read(path)["layout"]["footer"][-1] == ORG_FOOTER


def test_author_rows_coexist_with_managed_rows(repo, capsys):
    path = spec_file(repo, standard="finance",
                     data={**DATA, "dashboard": {**DATA["dashboard"], "classification":
                                                 "confidential"}})
    apply(capsys, str(path))
    mine = [{"markdown": "mine", "width": 12}]
    edit(path, lambda d: (d["layout"]["header"].insert(0, mine),
                          d["layout"]["header"].append(mine),
                          d["layout"]["footer"].insert(1, mine)))
    # The standard changes its rows: each managed row is refreshed where it stands.
    (repo / "standards" / "teams" / "finance.yaml").write_text(
        FINANCE.replace('"Finance"', '"Finance v2"'), encoding="utf-8")
    (repo / "standards" / "org.yaml").write_text(
        ORG.replace("Named recipients only.", "Named recipients only, v2."), encoding="utf-8")
    code, out = apply(capsys, str(path))
    data = read(path)
    v2 = [{"markdown": "Finance v2", "width": 12, "height": 1}]
    assert data["layout"]["header"] == [mine, v2, mine]
    assert data["layout"]["footer"] == [
        [{"markdown": "my note", "width": 12}], mine, ORG_FOOTER,
        [{"markdown": "Named recipients only, v2.", "width": 12, "height": 1}]]
    assert sorted(c["action"] for c in out["specs"][0]["changes"]) == ["refresh", "refresh"]


def test_two_identical_rows_from_two_layers_keep_their_own_identity(tmp_path, capsys):
    org = "name: org\ndefault: true\ncontent: {footer: [{divider: true}]}\n"
    team = "name: team\nextends: org\ncontent: {footer: [{divider: true}]}\n"
    repo = make_repo(tmp_path / "r", {"org.yaml": org, "team.yaml": team}).parent
    path = spec_file(repo, standard="team")
    apply(capsys, str(path))
    assert read(path)["layout"]["footer"][-2:] == [{"divider": True}, {"divider": True}]
    _, out = apply(capsys, str(path))
    assert out["specs"][0]["changes"] == []


def test_css_blocks_parse_back_exactly(repo):
    css = ("/* cw:std org 0123456789ab */\na { b: c; }\n/* cw:end org */\n"
           "/* cw:std team ba9876543210 */\nd {}\n/* cw:end team */\nauthor {}")
    segs = C.parse_css(css)
    assert [type(s).__name__ for s in segs] == ["Block", "str", "Block", "str"]
    assert segs[0].body == "a { b: c; }" and segs[2].layer == "team"
    assert C.join_css(segs) == css


@pytest.mark.parametrize("css, words", [
    ("/* cw:std org 0123456789ab */\na {}", "has no /* cw:end org */"),
    ("a {}\n/* cw:end org */", "closes no org block"),
    ("/* cw:std org 0123456789ab */\n/* cw:std team 0123456789ab */\n", "opens again"),
    ("/* cw:std org 0123456789ab */\na\n/* cw:end org */\n"
     "/* cw:std org 0123456789ab */\nb\n/* cw:end org */", "two org blocks"),
])
def test_broken_css_markers_are_named_never_guessed(repo, capsys, css, words):
    with pytest.raises(C.MarkerError) as e:
        C.parse_css(css)
    assert words in str(e.value)
    path = spec_file(repo, standard="finance",
                     data={**DATA, "dashboard": {**DATA["dashboard"], "css": css}})
    before = path.read_bytes()
    code, out = apply(capsys, str(path))
    assert code == 1 and out["specs"][0]["errors"][0]["code"] == "css_markers"
    # The locked org block can't be read: an error, not a silent pass, and --check fails.
    assert findings(capsys, path, "standard.content-locked")
    assert apply(capsys, str(path), "--check")[0] == 1
    assert path.read_bytes() == before, "a spec apply can't fully read is left as it is"


def test_an_author_editing_inside_an_unlocked_block_releases_it(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(css=d["dashboard"]["css"].replace(
        "#003366", "#123456")))
    apply(capsys, str(path))
    data = read(path)
    assert "#123456" in data["dashboard"]["css"]          # the author's edit stands
    assert "dashboard.css[finance]" not in data["design"]["standard_written"]
    # The standard changes its finance block: the released block is left as the author's.
    (repo / "standards" / "teams" / "finance.yaml").write_text(
        FINANCE.replace("#003366", "#000000"), encoding="utf-8")
    apply(capsys, str(path))
    assert read(path)["dashboard"]["css"] == data["dashboard"]["css"]
    assert [f["where"] for f in findings(capsys, path, "standard.content-released")] == [
        "dashboard.css[finance]"]


def test_an_author_editing_inside_a_locked_block_is_a_violation(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(css=d["dashboard"]["css"].replace(
        "Inter", "Comic Sans MS")))
    [f] = findings(capsys, path, "standard.content-locked")
    assert f["where"] == "dashboard.css[org]" and "Comic" not in f["detail"]
    code, _ = apply(capsys, str(path))
    assert code == 1 and "Comic Sans MS" in read(path)["dashboard"]["css"]
    apply(capsys, str(path), "--locked")
    css = read(path)["dashboard"]["css"]
    assert "Comic" not in css and css.endswith(".mine { color: red; }")


def test_a_deleted_css_block_is_a_tombstone(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))

    def drop_finance(d):
        segs = C.parse_css(d["dashboard"]["css"])
        d["dashboard"]["css"] = C.join_css([s for s in segs if not (
            isinstance(s, C.Block) and s.layer == "finance")])

    edit(path, drop_finance)
    apply(capsys, str(path))
    data = read(path)
    assert "cw:std finance" not in data["dashboard"]["css"]
    assert data["design"]["standard_written"]["dashboard.css[finance]"] is None


def test_a_scalar_follows_the_fill_rules(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(color_scheme="bnbColors"))
    _, out = apply(capsys, str(path))
    assert any(c["action"] == "release" and c["item"] == "dashboard.color_scheme"
               for c in out["specs"][0]["changes"])
    data = read(path)
    assert data["dashboard"]["color_scheme"] == "bnbColors"
    assert "dashboard.color_scheme" not in data["design"]["standard_written"]
    # The author deletes their own value: unset with no record, so the standard writes
    # it again, as a fill would (DESIGN-BRAIN sec.16).
    edit(path, lambda d: d["dashboard"].pop("color_scheme"))
    apply(capsys, str(path))
    assert read(path)["dashboard"]["color_scheme"] == "supersetColors"
    # Deleting the standard's own value: a null record, never written again.
    edit(path, lambda d: d["dashboard"].pop("color_scheme"))
    _, out = apply(capsys, str(path))
    assert {"item": "dashboard.color_scheme", "action": "tombstone", "layer": "finance",
            "was": "supersetColors"} in out["specs"][0]["changes"]
    apply(capsys, str(path))
    assert "color_scheme" not in read(path)["dashboard"]
    assert read(path)["design"]["standard_written"]["dashboard.color_scheme"] is None
    # Writing a value after deleting: the author's, and the null goes.
    edit(path, lambda d: d["dashboard"].update(color_scheme="googleCategory10c"))
    apply(capsys, str(path))
    data = read(path)
    assert data["dashboard"]["color_scheme"] == "googleCategory10c"
    assert "dashboard.color_scheme" not in data["design"]["standard_written"]


def test_label_colours_are_owned_per_key(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: (d["dashboard"]["label_colors"].pop("Cost"),
                          d["dashboard"]["label_colors"].update(Mine="#00FF00")))
    apply(capsys, str(path))
    data = read(path)
    assert data["dashboard"]["label_colors"] == {"Revenue": "#1FA8C9", "Mine": "#00FF00"}
    assert data["design"]["standard_written"]["dashboard.label_colors[Cost]"] is None
    # A team move: the old team's keys the standard still owns go; the org's stay.
    edit(path, lambda d: d["design"].update(standard="org"))
    apply(capsys, str(path))
    data = read(path)
    assert data["dashboard"]["label_colors"] == {"Revenue": "#1FA8C9", "Mine": "#00FF00"}
    assert "color_scheme" not in data["dashboard"]
    assert "header" not in data["layout"]
    assert not any("finance" in k for k in data["design"]["standard_written"])


def test_a_standard_dropping_an_item_removes_only_its_own_copy(repo, capsys):
    path = spec_file(repo, "a.json", standard="finance")
    other = spec_file(repo, "b.json", standard="finance")
    apply(capsys, str(path), str(other))
    edit(other, lambda d: d["dashboard"].update(color_scheme="bnbColors"))
    (repo / "standards" / "teams" / "finance.yaml").write_text(
        FINANCE.replace("  color_scheme: supersetColors\n", ""), encoding="utf-8")
    _, out = apply(capsys, str(path), str(other))
    assert "color_scheme" not in read(path)["dashboard"]
    assert read(other)["dashboard"]["color_scheme"] == "bnbColors"
    assert not any(k == "dashboard.color_scheme"
                   for k in read(other)["design"]["standard_written"])


# -- lifecycle and classification ----------------------------------------------


@pytest.mark.parametrize("lifecycle, words", [
    ({"state": "active", "successor": "x"}, "an active dashboard has no successor"),
    ({"state": "deprecated", "successor": "t"}, "own slug"),
    ({"state": "sunset", "sunset_date": "2026-02-30"}, "not a real day"),
    ({"state": "retired"}, "Input should be"),
])
def test_lifecycle_is_validated(lifecycle, words):
    data = {**DATA, "dashboard": {**DATA["dashboard"], "lifecycle": lifecycle}}
    with pytest.raises(Exception) as e:
        load_spec(data)
    assert words in str(e.value)


def test_classification_takes_a_word_not_markup():
    with pytest.raises(Exception):
        load_spec({**DATA, "dashboard": {**DATA["dashboard"], "classification": "a]=b"}})
    assert load_spec({**DATA, "dashboard": {**DATA["dashboard"],
                                            "classification": "Highly Confidential"}})


def _bundle_hash(data: dict) -> str:
    from chartwright.compiler import compile_bundle
    from chartwright.testing import stub_resolution

    spec = load_spec(data)
    return hashlib.sha256(compile_bundle(spec, stub_resolution(spec))).hexdigest()


@pytest.mark.parametrize("path", sorted([*REPO.glob("examples/*.json"),
                                         *REPO.glob("tests/fixtures/*.json")]),
                         ids=lambda p: p.name)
def test_lifecycle_classification_and_the_record_never_reach_the_bundle(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    tagged = json.loads(json.dumps(data))
    tagged["dashboard"]["lifecycle"] = {"state": "deprecated", "successor": "sdc-elsewhere",
                                        "sunset_date": "2027-01-31"}
    tagged["dashboard"]["classification"] = "internal"
    tagged.setdefault("design", {})["standard_written"] = {
        "dashboard.css[org]": None, "dashboard.color_scheme": None}
    assert _bundle_hash(tagged) == _bundle_hash(data)


def test_a_lifecycle_banner_and_a_classification_footer_follow_the_spec(repo, capsys):
    data = {**DATA, "dashboard": {**DATA["dashboard"], "classification": "confidential",
                                  "lifecycle": {"state": "deprecated", "successor": "t2"}}}
    path = spec_file(repo, standard="finance", data=data)
    apply(capsys, str(path))
    got = read(path)
    banner = [{"markdown": "Deprecated: use the successor.", "width": 12, "height": 1}]
    named = [{"markdown": "Named recipients only.", "width": 12, "height": 1}]
    assert got["layout"]["header"] == [banner, FIN_HEADER]
    assert got["layout"]["footer"][-2:] == [ORG_FOOTER, named]
    # The dashboard comes back into use and is reclassified: the rows follow.
    edit(path, lambda d: (d["dashboard"].pop("lifecycle"),
                          d["dashboard"].update(classification="internal")))
    apply(capsys, str(path))
    got = read(path)
    assert got["layout"]["header"] == [FIN_HEADER]
    assert got["layout"]["footer"][-1] == ORG_FOOTER
    assert not any("lifecycle" in k or "classification" in k
                   for k in got["design"]["standard_written"])


def test_a_classification_the_standard_doesnt_list_is_an_error(repo, capsys):
    path = spec_file(repo, standard="finance",
                     data={**DATA, "dashboard": {**DATA["dashboard"], "classification": "secret"}})
    [f] = findings(capsys, path, "standard.classification")
    assert f["severity"] == "error" and "'org' lists" in f["detail"]


# -- check, --check, --claim, the summary ---------------------------------------


def test_check_flags_stale_unlocked_content_as_a_warning(repo, capsys):
    path = spec_file(repo, standard="finance")
    stale = findings(capsys, path, "standard.content-stale")
    assert {f["where"] for f in stale} >= {"layout.header[finance][0]", "dashboard.css[finance]"}
    assert all(f["severity"] == "warn" for f in stale)
    locked = findings(capsys, path, "standard.content-locked")
    assert {f["where"] for f in locked} == {"layout.footer[org][0]", "dashboard.css[org]"}


def test_apply_check_writes_nothing_and_fails_on_missing_locked_content(repo, capsys):
    path = spec_file(repo, standard="finance")
    before = path.read_bytes()
    code, out = apply(capsys, str(path), "--check")
    entry = out["specs"][0]
    assert code == 1 and out["check"] and entry["stale"]
    assert entry["locked_stale"] == ["layout.footer[org][0]", "dashboard.css[org]"]
    assert path.read_bytes() == before
    apply(capsys, str(path))
    code, out = apply(capsys, str(path), "--check")
    assert code == 0 and not out["specs"][0]["stale"]
    # A released unlocked item is the author's: not stale.
    edit(path, lambda d: d["dashboard"].update(color_scheme="bnbColors"))
    assert apply(capsys, str(path), "--check")[0] == 0
    # A changed locked item fails.
    edit(path, lambda d: d["layout"]["footer"].pop())
    assert apply(capsys, str(path), "--check")[0] == 1


def test_apply_check_fails_on_locked_changes_and_lists_unlocked_ones(repo, capsys):
    """The decision record's #7: --check fails when a spec is stale on locked content.
    An unlocked change reaches each team in its own pull request, so it is listed, not
    failed; standards check --strict fails on it (a warn) for a team that wants it."""
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    fin = repo / "standards" / "teams" / "finance.yaml"
    fin.write_text(FINANCE.replace('"Finance"', '"Finance v2"'), encoding="utf-8")
    code, out = apply(capsys, str(path), "--check")
    assert code == 0 and out["specs"][0]["stale"] and out["specs"][0]["locked_stale"] == []
    assert run(capsys, "standards", "check", str(path))[0] == 0
    assert run(capsys, "standards", "check", str(path), "--strict")[0] == 1
    org = repo / "standards" / "org.yaml"
    org.write_text(ORG.replace("Acme Corp.", "Acme Corp. 2026."), encoding="utf-8")
    code, out = apply(capsys, str(path), "--check")
    assert code == 1 and out["specs"][0]["locked_stale"] == ["layout.footer[org][0]"]
    # Plain apply refreshes the locked row: the standard's own, not an author's edit.
    code, out = apply(capsys, str(path))
    assert code == 0 and apply(capsys, str(path), "--check")[0] == 0


def test_claim_records_content_a_decompiled_spec_already_holds(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    # Decompile keeps the content and loses the record.
    edit(path, lambda d: d["design"].pop("standard_written"))
    before = path.read_bytes()
    code, out = apply(capsys, str(path), "--check")
    assert code == 0, "content already there is no reason to fail --check"
    apply(capsys, str(path))
    assert read(path)["layout"]["footer"].count(ORG_FOOTER) == 1, "never a second legal row"
    assert path.read_bytes() == before
    code, out = apply(capsys, str(path), "--claim")
    assert code == 0 and {c["action"] for c in out["specs"][0]["changes"]} == {"claim"}
    assert len(read(path)["design"]["standard_written"]) == 9


def test_claim_marks_the_standards_css_found_unmarked(repo, capsys):
    css = ".dashboard-markdown { font-family: Inter; }\n.mine { color: red; }"
    path = spec_file(repo, standard="org",
                     data={**DATA, "dashboard": {**DATA["dashboard"], "css": css}})
    apply(capsys, str(path))
    assert read(path)["dashboard"]["css"].count("font-family: Inter") == 1
    apply(capsys, str(path), "--claim")
    css = read(path)["dashboard"]["css"]
    assert css.startswith("/* cw:std org ") and css.count("font-family: Inter") == 1
    assert css.endswith("/* cw:end org */\n.mine { color: red; }")


def test_the_summary_groups_a_rollout_by_standard_and_item(repo, capsys):
    paths = [spec_file(repo, f"s{i}.json", standard="finance") for i in range(3)]
    plain = spec_file(repo, "o.json")   # follows the default, org
    apply(capsys, *map(str, paths), str(plain))
    edit(paths[0], lambda d: d["dashboard"].update(color_scheme="bnbColors"))
    edit(paths[1], lambda d: d["layout"]["footer"].pop())
    (repo / "standards" / "teams" / "finance.yaml").write_text(
        FINANCE.replace("supersetColors", "googleCategory10c"), encoding="utf-8")
    code, out = apply(capsys, *map(str, paths), str(plain))
    fin = next(g for g in out["summary"] if g["standard"] == "finance")
    assert fin["specs"] == 3 and fin["chain"] == ["org", "finance"]
    items = {i["item"]: i for i in fin["items"]}
    scheme = items["dashboard.color_scheme"]
    assert scheme["changes"] == [{"action": "refresh", "to": "googleCategory10c",
                                  "specs": [str(paths[1]), str(paths[2])]}]
    assert scheme["released"] == [str(paths[0])] and scheme["current"] == 0
    footer = items["layout.footer[org][0]"]
    assert footer["locked_by"] == "org" and footer["locked"] == [str(paths[1])]
    assert footer["current"] == 2
    text = run(capsys, "standards", "apply", *map(str, paths), "--check")[1]
    assert "dashboard.color_scheme: 1 released by authors, skipped" in text
    assert "layout.footer[org][0] (locked by org): 1 changed by their authors but locked" in text
    assert "2 already current" in text


def test_the_text_summary_says_same_change_times_n(repo, capsys):
    paths = [spec_file(repo, f"s{i}.json", standard="finance") for i in range(3)]
    code, text = run(capsys, "standards", "apply", *map(str, paths))
    assert code == 0 and text.startswith("standards apply: 3 specs following a standard; "
                                         "wrote 3, 0 unchanged")
    assert (f"layout.footer[org][0] (locked by org): same change × 3 (add "
            f"{C.content_hash(ORG_FOOTER)})") in text


def test_one_pull_request_per_team(repo, capsys):
    fin = spec_file(repo, "f.json", standard="finance")
    org = spec_file(repo, "o.json")
    code, out = apply(capsys, str(fin), str(org), "--standard", "finance")
    assert code == 0 and out["other_standards"] == [str(org)]
    assert "standard_written" not in read(org).get("design", {})
    code, out = run(capsys, "standards", "apply", str(fin), "--standard", "fiance")
    assert code == 1 and "did you mean 'finance'" in out["errors"][0]["detail"]


# -- one owner per field: the brain and the standard ---------------------------


def test_a_field_cant_be_recorded_by_the_brain_and_a_standard():
    data = {**DATA, "design": {"filled": {"K": {"number_format": ",.0f"}},
                               "standard_written": {"charts[K].number_format":
                                                    {"layer": "org", "value": ",.0f"}}}}
    with pytest.raises(Exception) as e:
        load_spec(data)
    assert "one owner" in str(e.value)


def test_apply_takes_a_brain_fill_and_drops_its_record(repo, capsys):
    path = spec_file(repo, standard="finance")
    # The brain filled number_format first (default.count-format writes ',.0f').
    fixed, _ = advise_and_fix(read(path))
    assert fixed["design"]["filled"]["K"]["number_format"] == ",.0f"
    path.write_text(json.dumps(fixed, indent=2) + "\n", encoding="utf-8")
    apply(capsys, str(path))
    data = read(path)
    assert "K" not in data["design"].get("filled", {})
    assert data["design"]["standard_written"]["charts[K].number_format"] == {
        "layer": "finance", "value": ",.0f"}
    # The brain now leaves the field alone: its fills never touch it again.
    again, report = advise_and_fix(data)
    assert again == data
    assert not any(f.rule == "default.count-format" and f.chart == "K" for f in report.findings)


def test_the_brain_never_fills_a_field_a_standard_released(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["charts"][0].pop("number_format"))
    apply(capsys, str(path))
    data = read(path)
    assert data["design"]["standard_written"]["charts[K].number_format"] is None
    again, _ = advise_and_fix(data)
    assert "number_format" not in again["charts"][0]


def test_a_repair_never_edits_a_standards_row(tmp_path, capsys):
    # A one-line markdown row without a height is one the markdown-height repair resizes;
    # written by a standard, it is the standard's alone.
    org = "name: org\ndefault: true\ncontent: {footer: [[{markdown: Legal}]]}\n"
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo)
    apply(capsys, str(path))
    data = read(path)
    fixed, _ = advise_and_fix(data)
    assert fixed["layout"]["footer"][0][0]["height"] == 2, "the author's row is repaired"
    assert fixed["layout"]["footer"][-1] == [{"markdown": "Legal"}], "the standard's is not"


def test_number_format_skips_charts_whose_metrics_disagree_or_plot_shares(repo):
    fin = std(repo)
    spec = load_spec({**DATA, "charts": [
        {"type": "bar", "name": "B", "dataset": DS, "x_column": "x",
         "metrics": ["COUNT(*) AS Orders", "SUM(x) AS Other"]},
        {"type": "bar", "name": "S", "dataset": DS, "x_column": "x",
         "metrics": ["COUNT(*) AS Orders"], "contribution": "row"},
        {"type": "bar", "name": "O", "dataset": DS, "x_column": "x",
         "metrics": ["COUNT(*) AS Orders"]}],
        "layout": {"rows": [["B", "S", "O"]]}})
    ids = [i.id for i in C.expected_items(fin, spec) if i.slot == "number_format"]
    assert ids == ["charts[O].number_format"]


# -- locks can't be silenced, and CSS that could defeat them ---------------------


def test_design_ignore_cant_silence_locked_content(repo, capsys):
    path = spec_file(repo, standard="finance", ignore=["standard.content-locked"])
    _, out = run(capsys, "standards", "check", str(path))
    entry = out["specs"][0]
    assert not entry["ok"]
    assert entry["locks"]["refused_ignores"] == [{"entry": "standard.content-locked",
                                                  "locked_by": "org"}]


def test_css_that_could_hide_locked_rows_is_a_warning(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(
        css=d["dashboard"]["css"] + "\n.dashboard-markdown { display: none !important; }"))
    [f] = findings(capsys, path, "standard.css-hides")
    assert f["severity"] == "warn" and "the dashboard's own CSS" in f["detail"]
    assert ".dashboard-markdown { display: none }" in f["detail"] and "org locks" in f["detail"]


def test_css_hides_is_checked_when_the_standard_writes_no_css(tmp_path, capsys):
    org = ("name: org\ndefault: true\ncontent: {footer: [[{markdown: Legal}]]}\n"
           "locked: {content: [footer]}\n")
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    data = {**DATA, "dashboard": {**DATA["dashboard"],
                                  "css": "#MARKDOWN-sdc-footer-2-1 { visibility: hidden }"}}
    path = spec_file(repo, data=data)
    apply(capsys, str(path))
    [f] = findings(capsys, path, "standard.css-hides")
    assert "#MARKDOWN-sdc-footer-2-1 { visibility: hidden }" in f["detail"]


def test_a_block_of_the_locking_layer_may_hide_things(tmp_path, capsys):
    org = ("name: org\ndefault: true\ncontent: {footer: [[{markdown: Legal}]], "
           "css: '.x { display: none }'}\nlocked: {content: [footer]}\n")
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo)
    apply(capsys, str(path))
    assert findings(capsys, path, "standard.css-hides") == []


# -- explain and show ---------------------------------------------------------


def test_explain_has_a_dashboard_section(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(color_scheme="bnbColors"))
    payload = run_ok(capsys, "explain", str(path), "--json")
    rows = {r["item"]: r for r in payload["dashboard"]}
    footer = rows["layout.footer[org][0]"]
    assert (footer["layer"], footer["locked"], footer["locked_by"], footer["source"]) == (
        "org", True, "org", "standard")
    assert footer["value"] == ORG_FOOTER and footer["recorded"]["hash"] == C.content_hash(ORG_FOOTER)
    assert "org's standards file" in footer["override"]
    scheme = rows["dashboard.color_scheme"]
    assert (scheme["source"], scheme["value"], scheme["standard"]) == (
        "released", "bnbColors", "supersetColors")
    assert scheme["override"].startswith("it is yours")
    text = run_ok(capsys, "explain", str(path))
    assert text.splitlines()[0].startswith("Design defaults (design brain 6")
    assert "Dashboard content (standard org -> finance, via design.standard)" in text
    # A chart field the standard wrote says so in the chart section too.
    k = next(c for c in payload["charts"] if c["chart"] == "K")
    nf = next(r for r in k["fields"] if r["field"] == "number_format")
    assert nf["source"] == "standard" and "finance standard wrote it" in nf["reason"]
    # A rules-only standard explains as before: no dashboard section.
    rules_only = make_repo(repo.parent / "r2", {"org.yaml": "name: org\ndefault: true\n"})
    plain = spec_file(rules_only.parent)
    assert "dashboard" not in run_ok(capsys, "explain", str(plain), "--json")


def test_show_lists_content_with_layer_and_lock(repo, capsys):
    payload = run_ok(capsys, "standards", "show", "finance",
                     "--standards", str(repo / "standards"), "--json")
    content = payload["content"]
    assert content["content.footer[org][0]"] == {"slot": "footer", "value": ORG_FOOTER,
                                                 "layer": "org", "locked": True}
    assert content["content.color_scheme"]["layer"] == "finance"
    assert content["content.number_format[Orders]"]["value"] == ",.0f"
    assert "content.header[org][lifecycle=deprecated][0]" in content
    assert payload["locked"]["content"] == {"css": "org", "footer": "org"}
    assert payload["classifications"] == {"value": ["public", "internal", "confidential"],
                                          "layer": "org"}
    text = run_ok(capsys, "standards", "show", "finance", "--standards",
                  str(repo / "standards"))
    assert "content.footer[org][0]" in text and "locked" in text


# -- the page that documents it -----------------------------------------------


def test_the_content_example_in_the_design_brain_page_is_what_the_tool_does(
        tmp_path, monkeypatch, capsys):
    """DESIGN-BRAIN.md sec.18 "Content": its two files, exactly as printed, give the
    record, the two summaries and the explain lines printed there, for three ops
    dashboards (delays is confidential)."""
    text = (REPO / "docs" / "DESIGN-BRAIN.md").read_text(encoding="utf-8")
    section = text[text.index("### Content"):]
    files = {}
    for block in [b.split("```", 1)[0] for b in section.split("```yaml\n")[1:3]]:
        first = block.splitlines()[0]
        files[first[len("# standards/"):].strip()] = block
    root = tmp_path / "r"
    make_repo(root, files)
    monkeypatch.chdir(root)
    for title, cls in (("Delays", "confidential"), ("Fleet", "internal"), ("Routes", "internal")):
        write_spec(Path("specs/ops") / f"{title.lower()}.json", {
            "spec_version": "1",
            "dashboard": {"title": title, "slug": f"ops-{title.lower()}", "classification": cls},
            "charts": [{"type": "big_number_total", "name": "Flights",
                        "dataset": {"database": "examples", "table": "flights"},
                        "metric": "COUNT(*) AS Flights"}],
            "layout": {"rows": [["Flights"]],
                       "footer": [[{"markdown": "Source: DOT on-time data", "width": 12}]]},
        }, standard="ops")

    def printed(after: str) -> str:
        return section.split(f"```\n{after}", 1)[1].split("```", 1)[0]

    code, out = run(capsys, "standards", "apply", "specs/ops")
    assert code == 0 and out == "standards apply:" + printed("standards apply:")
    record = json.loads("{" + section.split("```json\n\"design\": {", 1)[1].split("```")[0])
    assert read(Path("specs/ops/delays.json"))["design"] == record
    edit(Path("specs/ops/fleet.json"), lambda d: d["dashboard"].update(color_scheme="bnbColors"))
    edit(Path("specs/ops/routes.json"),
         lambda d: d["layout"]["footer"][1][0].update(markdown="Acme Corp."))
    ops = root / "standards" / "teams" / "ops.yaml"
    ops.write_text(ops.read_text().replace("#ops-data", "#ops-analytics"), encoding="utf-8")
    code, out = run(capsys, "standards", "apply", "specs/ops", "--check")
    assert code == 1 and out == "standards apply --check:" + printed("standards apply --check:")
    explained = run_ok(capsys, "explain", "specs/ops/routes.json")
    for line in printed("Dashboard content").splitlines()[1:]:
        assert line.strip() in explained, line


# -- MCP parity ---------------------------------------------------------------


def test_mcp_standards_apply_matches_the_cli(repo, monkeypatch, capsys):
    path = spec_file(repo, standard="finance")
    original = path.read_text()
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    checked = mcp_call("standards_apply", spec_json=original, check=True)
    assert checked["ok"] is False and checked["stale"] and "spec" not in checked
    out = mcp_call("standards_apply", spec_json=original)
    assert out["ok"] and out["written"]
    _, cli = apply(capsys, str(path))
    assert out["spec"] == read(path)
    assert out["changes"] == cli["specs"][0]["changes"]
    assert mcp_call("standards_apply", spec_json=path.read_text(), check=True)["ok"] is True
    # explain_spec carries the same dashboard section the CLI prints.
    explained = mcp_call("explain_spec", spec_json=path.read_text())
    assert explained == run_ok(capsys, "explain", str(path), "--json")
    shown = mcp_call("standards_show", spec_json=path.read_text())
    assert shown["content"] == run_ok(capsys, "standards", "show", "--for", str(path),
                                      "--json")["content"]


def test_mcp_standards_apply_locked_and_claim(repo, monkeypatch, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    data = read(path)
    data["layout"]["footer"].pop()
    data["design"].pop("standard_written")
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    plain = mcp_call("standards_apply", spec_json=json.dumps(data))
    # The decompiled spec lost the record: the footer is simply missing, so apply adds it.
    assert plain["spec"]["layout"]["footer"][-1] == ORG_FOOTER
    claimed = mcp_call("standards_apply", spec_json=json.dumps(data), claim=True)
    assert {c["action"] for c in claimed["changes"]} == {"add", "claim"}
