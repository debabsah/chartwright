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
    ("disable: [standard.css-hides]", "can't be disabled"),
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


@pytest.mark.parametrize("files", [
    # the locking file disables the rule itself
    {"org.yaml": "name: org\ncontent: {footer: [[{markdown: L}]]}\nlocked: {content: [footer]}\n"
                 "disable: [standard.content-locked]\n"},
    # a parent disabled it before a lower layer locks content
    {"org.yaml": "name: org\ndisable: [standard.css-hides]\n",
     "team.yaml": "name: team\nextends: org\ncontent: {footer: [[{markdown: L}]]}\n"
                  "locked: {content: [footer]}\n"},
])
def test_the_rules_locked_content_needs_cant_be_disabled_anywhere_in_its_chain(tmp_path, files):
    std_dir = make_repo(tmp_path / "r", files)
    with pytest.raises(StandardsError) as e:
        load_standards(std_dir)
    assert e.value.code == "locked" and "locks content" in str(e.value)


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
        "/* cw:std org " + C.css_hash(".dashboard-markdown { font-family: Inter; }")
        + " */\n.dashboard-markdown { font-family: Inter; }\n/* cw:end org */\n"
        "/* cw:std finance " + C.css_hash(".dashboard-markdown h2 { color: #003366; }")
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
                                                "hash": C.row_hash(ORG_FOOTER)}
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
    assert code == 0 and out["no_standard"] == [path.as_posix()] and path.read_bytes() == before


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
    assert data["design"]["standard_written"]["layout.header[finance][0]"] == {
        "layer": "finance", "hash": C.row_hash(FIN_HEADER), "released": True}
    # Released, not deleted: the edited row (same shape, other text) stands where it was.
    ours = [{"markdown": "Finance (ours)", "width": 12, "height": 1}]
    assert {"item": "layout.header[finance][0]", "action": "release", "layer": "finance",
            "was": C.row_hash(FIN_HEADER), "to": C.row_hash(ours)} in out["specs"][0][
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
    assert data["design"]["standard_written"]["layout.header[finance][0]"]["released"] is True
    # Deleting the entry takes the standard's row again.
    edit(path, lambda d: d["design"]["standard_written"].pop("layout.header[finance][0]"))
    apply(capsys, str(path))
    assert read(path)["layout"]["header"] == [FIN_HEADER]


def test_a_locked_row_the_author_edits_is_a_violation_only_locked_rewrites(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["footer"][1][0].update(markdown="Confidential. Acme Corp. Share freely."))
    locked = findings(capsys, path, "standard.content-locked")
    assert len(locked) == 1 and locked[0]["severity"] == "error"
    assert locked[0]["where"] == "layout.footer[org][0]" and "org locks" in locked[0]["detail"]
    assert "standards apply --locked" in locked[0]["detail"]
    # apply reports it and leaves it (exit 1), keeping the record.
    code, out = apply(capsys, str(path))
    assert code == 1 and out["specs"][0]["locked"] == ["layout.footer[org][0]"]
    data = read(path)
    assert data["layout"]["footer"][1][0]["markdown"] == "Confidential. Acme Corp. Share freely."
    assert data["design"]["standard_written"]["layout.footer[org][0]"] is not None
    # --locked puts it back in place, as a visible change with what was there.
    code, out = apply(capsys, str(path), "--locked")
    assert code == 0
    change = next(c for c in out["specs"][0]["changes"] if c["item"] == "layout.footer[org][0]")
    assert change["action"] == "rewrite" and change["locked_by"] == "org"
    assert change["was"] == C.row_hash([{"markdown": "Confidential. Acme Corp. Share freely.", "width": 12,
                                             "height": 1}])
    assert read(path)["layout"]["footer"] == [[{"markdown": "my note", "width": 12}], ORG_FOOTER]
    assert findings(capsys, path, "standard.content-locked") == []


def test_locked_never_overwrites_a_same_shape_row_that_reads_differently(repo, capsys):
    """An author's own row of the same shape where the locked row stood is not the
    locked row, edited: --locked adds the standard's row beside it, and says so."""
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["footer"][-1][0].update(markdown="My disclaimer, keep me"))
    [f] = findings(capsys, path, "standard.content-locked")
    assert "layout.footer[1] has its shape but reads differently" in f["detail"]
    _, out = apply(capsys, str(path), "--locked")
    change = next(c for c in out["specs"][0]["changes"] if c["item"] == "layout.footer[org][0]")
    assert change["action"] == "rewrite" and change["kept"] == "layout.footer[1]"
    assert [r[0]["markdown"] for r in read(path)["layout"]["footer"]] == [
        "my note", "My disclaimer, keep me", "Confidential. Acme Corp."]


def test_a_locked_item_keeps_the_copy_left_of_two_identical_rows(repo, capsys):
    """org (locked) and finance both write the same footer row; the author deletes one
    copy. The copy left is the locked item's, and finance's is the one released."""
    fin = repo / "standards" / "teams" / "finance.yaml"
    fin.write_text(FINANCE + "  footer:\n    - [{markdown: \"Confidential. Acme Corp.\", "
                             "width: 12, height: 1}]\n", encoding="utf-8")
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    assert read(path)["layout"]["footer"].count(ORG_FOOTER) == 2
    edit(path, lambda d: d["layout"]["footer"].pop(1))
    assert findings(capsys, path, "standard.content-locked") == []
    _, out = apply(capsys, str(path))
    written = read(path)["design"]["standard_written"]
    assert not written["layout.footer[org][0]"].get("released")
    assert written["layout.footer[finance][0]"]["released"] is True
    assert read(path)["layout"]["footer"].count(ORG_FOOTER) == 1


def test_on_the_headers_author_side_only_the_next_row_can_be_the_edited_one(
        tmp_path, capsys):
    """Below a standard's header rows sit the author's: a row there that reads like the
    standard's is not taken for it unless it is the one right below the managed rows."""
    org = ("name: org\ndefault: true\ncontent: {header: [[{markdown: 'Finance dashboard', "
           "width: 12, height: 1}]]}\nlocked: {content: [header]}\n")
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"].__setitem__("header", [
        [{"markdown": "Banner", "width": 6}, {"markdown": "Banner", "width": 6}],
        md("Finance dashboard v2")]))
    apply(capsys, str(path), "--locked")
    assert [[i["markdown"] for i in r] for r in read(path)["layout"]["header"]] == [
        ["Finance dashboard"], ["Banner", "Banner"], ["Finance dashboard v2"]]


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
    assert data["design"]["standard_written"]["dashboard.css[finance]"]["released"] is True
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
    assert data["design"]["standard_written"]["dashboard.css[finance]"]["released"] is True
    apply(capsys, str(path))
    assert "cw:std finance" not in read(path)["dashboard"]["css"], "never re-added"


def test_a_scalar_the_author_edits_or_deletes_stays_theirs(repo, capsys):
    """The approved rule: an author's edit or deletion wins. A released record keeps what
    the standard wrote, so deleting a value after editing it is never undone (unlike a
    design.filled fill, whose record goes on an edit)."""
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(color_scheme="bnbColors"))
    _, out = apply(capsys, str(path))
    assert any(c["action"] == "release" and c["item"] == "dashboard.color_scheme"
               for c in out["specs"][0]["changes"])
    data = read(path)
    assert data["dashboard"]["color_scheme"] == "bnbColors"
    released = {"layer": "finance", "value": "supersetColors", "released": True}
    assert data["design"]["standard_written"]["dashboard.color_scheme"] == released
    # The author deletes their own value: the deletion stands.
    edit(path, lambda d: d["dashboard"].pop("color_scheme"))
    apply(capsys, str(path))
    assert "color_scheme" not in read(path)["dashboard"]
    assert read(path)["design"]["standard_written"]["dashboard.color_scheme"] == released
    # Writing a value again: the author's, the record unchanged.
    edit(path, lambda d: d["dashboard"].update(color_scheme="googleCategory10c"))
    _, out = apply(capsys, str(path))
    assert out["specs"][0]["changes"] == []
    assert read(path)["dashboard"]["color_scheme"] == "googleCategory10c"


def test_a_scalar_deleted_while_the_standards_is_a_tombstone(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].pop("color_scheme"))
    _, out = apply(capsys, str(path))
    assert {"item": "dashboard.color_scheme", "action": "tombstone", "layer": "finance",
            "was": "supersetColors"} in out["specs"][0]["changes"]
    apply(capsys, str(path))
    assert "color_scheme" not in read(path)["dashboard"]
    assert read(path)["design"]["standard_written"]["dashboard.color_scheme"] == {
        "layer": "finance", "value": "supersetColors", "released": True}
    # Deleting the entry takes the standard's value again.
    edit(path, lambda d: d["design"]["standard_written"].pop("dashboard.color_scheme"))
    apply(capsys, str(path))
    assert read(path)["dashboard"]["color_scheme"] == "supersetColors"


def test_label_colours_are_owned_per_key(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: (d["dashboard"]["label_colors"].pop("Cost"),
                          d["dashboard"]["label_colors"].update(Mine="#00FF00")))
    apply(capsys, str(path))
    data = read(path)
    assert data["dashboard"]["label_colors"] == {"Revenue": "#1FA8C9", "Mine": "#00FF00"}
    assert data["design"]["standard_written"]["dashboard.label_colors[Cost]"]["released"]
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


# -- edge cases a review found --------------------------------------------------


def two_layer_css(tmp_path, css: str):
    a = "name: a\ndefault: true\ncontent: {css: '.x { color: red; }'}\n"
    b = "name: b\nextends: a\ncontent: {css: '.y { color: blue; }'}\n"
    repo = make_repo(tmp_path / "r", {"a.yaml": a, "b.yaml": b}).parent
    return spec_file(repo, standard="b", data={**DATA, "dashboard": {**DATA["dashboard"],
                                                                     "css": css}})


@pytest.mark.parametrize("css", [
    ".x { color: red; }\n.y { color: blue; }",              # both unmarked (decompiled)
    "/* mine */\n.x { color: red; }\n.y { color: blue; }",  # with the author's CSS first
    ".y { color: blue; }",                                  # one unmarked, one to add
])
def test_claim_marks_each_layers_css_once(tmp_path, capsys, css):
    path = two_layer_css(tmp_path, css)
    code, out = apply(capsys, str(path), "--claim")
    assert code == 0, out
    got = read(path)["dashboard"]["css"]
    segs = C.parse_css(got)
    assert [s.layer for s in segs if isinstance(s, C.Block)] == ["a", "b"]
    assert got.count(".x { color: red; }") == 1 and got.count(".y { color: blue; }") == 1
    assert "*//*" not in got
    assert apply(capsys, str(path))[1]["specs"][0]["changes"] == []


def test_locked_css_in_a_comment_is_missing_not_present(repo, capsys):
    css = "/* .dashboard-markdown { font-family: Inter; } */\n.mine { color: red; }"
    path = spec_file(repo, standard="finance",
                     data={**DATA, "dashboard": {**DATA["dashboard"], "css": css}})
    assert apply(capsys, str(path), "--check")[1]["specs"][0]["locked_stale"] == [
        "layout.footer[org][0]", "dashboard.css[org]"]
    apply(capsys, str(path))
    assert read(path)["dashboard"]["css"].startswith("/* cw:std org ")


def test_claim_never_marks_part_of_an_authors_rule(tmp_path, capsys):
    """`.sidebar .dashboard-markdown {...}` holds the standard's text, not its rule."""
    a = "name: a\ndefault: true\ncontent: {css: '.dashboard-markdown { font-family: Inter; }'}\n"
    repo = make_repo(tmp_path / "r", {"a.yaml": a}).parent
    css = ".sidebar .dashboard-markdown { font-family: Inter; }\n.mine { color: red; }"
    path = spec_file(repo, data={**DATA, "dashboard": {**DATA["dashboard"], "css": css}})
    _, out = apply(capsys, str(path), "--claim")
    assert [c["action"] for c in out["specs"][0]["changes"]] == ["add"]
    got = read(path)["dashboard"]["css"]
    assert got.endswith("/* cw:end a */\n" + css), "the author's rule is left whole"
    assert C.find_unmarked(css, ".dashboard-markdown { font-family: Inter; }") is None
    assert C.find_unmarked("a{}\n/* x */ .b { c: d; }\n", ".b { c: d; }") == (12, 24)


def test_locked_unmarked_css_conforms_only_once_claimed(repo, capsys):
    css = ".dashboard-markdown { font-family: Inter; }\n.mine { color: red; }"
    path = spec_file(repo, standard="finance",
                     data={**DATA, "dashboard": {**DATA["dashboard"], "css": css}})
    locked = findings(capsys, path, "standard.content-locked")
    assert "dashboard.css[org]" in [f["where"] for f in locked]
    apply(capsys, str(path), "--claim")
    got = read(path)["dashboard"]["css"]
    assert got.count("font-family: Inter") == 1 and got.startswith("/* cw:std org ")
    assert "dashboard.css[org]" not in [
        f["where"] for f in findings(capsys, path, "standard.content-locked")]


def test_claim_brings_an_unedited_earlier_block_up_to_date(repo, capsys):
    """The marker's hash says whether a block is the standard's own, unedited: after a
    decompile (no record) --claim refreshes such a block to the standard's current text."""
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["design"].pop("standard_written"))
    fin = repo / "standards" / "teams" / "finance.yaml"
    fin.write_text(FINANCE.replace("#003366", "#112233"), encoding="utf-8")
    _, out = apply(capsys, str(path))
    assert "dashboard.css[finance]" not in [c["item"] for c in out["specs"][0]["changes"]]
    _, out = apply(capsys, str(path), "--claim")
    change = next(c for c in out["specs"][0]["changes"] if c["item"] == "dashboard.css[finance]")
    assert change["action"] == "refresh"
    css = read(path)["dashboard"]["css"]
    assert "#112233" in css and "#003366" not in css
    marker = C.parse_css(css)[2]
    assert marker.layer == "finance" and marker.stamp == C.css_hash(marker.body)


def test_an_edited_block_isnt_taken_for_the_standards_earlier_one(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: (d["design"].pop("standard_written"), d["dashboard"].update(
        css=d["dashboard"]["css"].replace("#003366", "#654321"))))
    _, out = apply(capsys, str(path), "--claim")
    assert "dashboard.css[finance]" not in [c["item"] for c in out["specs"][0]["changes"]]
    assert "#654321" in read(path)["dashboard"]["css"]


def test_new_blocks_follow_the_authors_line_endings(tmp_path, capsys):
    path = two_layer_css(tmp_path, ".mine{}\r\n.other{}\r\n")
    apply(capsys, str(path))
    css = read(path)["dashboard"]["css"]
    assert "\n" not in css.replace("\r\n", "")
    assert css.endswith("/* cw:end b */\r\n.mine{}\r\n.other{}\r\n")
    assert apply(capsys, str(path))[1]["specs"][0]["changes"] == []


def test_blocks_go_after_the_authors_imports(tmp_path, capsys):
    css = "@import url('https://fonts.example.com/inter.css');\n.mine { color: red; }"
    path = two_layer_css(tmp_path, css)
    apply(capsys, str(path))
    got = read(path)["dashboard"]["css"]
    assert got.startswith("@import url('https://fonts.example.com/inter.css');\n/* cw:std a ")
    assert got.endswith("/* cw:end b */\n.mine { color: red; }")


@pytest.mark.parametrize("css", ["\n\n.mine{}", "\n.mine{}\n", ".mine{}", "a{}\r\nb{}"])
def test_adding_and_removing_blocks_leaves_the_authors_css_byte_for_byte(tmp_path, capsys, css):
    path = two_layer_css(tmp_path, css)
    std_dir = path.parent.parent / "standards"
    for _ in range(2):
        apply(capsys, str(path))
        (std_dir / "a.yaml").write_text("name: a\ndefault: true\n", encoding="utf-8")
        (std_dir / "b.yaml").write_text("name: b\nextends: a\n", encoding="utf-8")
        apply(capsys, str(path))
        assert read(path)["dashboard"]["css"] == css
        (std_dir / "a.yaml").write_text(
            "name: a\ndefault: true\ncontent: {css: '.x { color: red; }'}\n", encoding="utf-8")
        (std_dir / "b.yaml").write_text(
            "name: b\nextends: a\ncontent: {css: '.y { color: blue; }'}\n", encoding="utf-8")


def test_line_endings_inside_a_block_are_no_edit(tmp_path, capsys):
    path = two_layer_css(tmp_path, "")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(css=d["dashboard"]["css"].replace("\n", "\r\n")))
    _, out = apply(capsys, str(path), "--check")
    assert out["specs"][0]["changes"] == []


def rows_repo(tmp_path, header: list[str]):
    rows = ", ".join(f"[{{markdown: '{t}', width: 12, height: 1}}]" for t in header)
    repo = make_repo(tmp_path / "r", {"a.yaml": f"name: a\ndefault: true\ncontent: "
                                                f"{{header: [{rows}]}}\n"}).parent
    return repo


def md(text):
    return [{"markdown": text, "width": 12, "height": 1}]


def test_a_released_row_stays_released_when_the_standard_adds_a_row_above_it(
        tmp_path, capsys):
    repo = rows_repo(tmp_path, ["Title", "Contact"])
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["header"].__setitem__(0, md("My own title")))
    apply(capsys, str(path))
    (repo / "standards" / "a.yaml").write_text(
        "name: a\ndefault: true\ncontent: {header: [[{markdown: 'NEW banner', width: 12, "
        "height: 1}], [{markdown: Title, width: 12, height: 1}], [{markdown: Contact, "
        "width: 12, height: 1}]]}\n", encoding="utf-8")
    apply(capsys, str(path))
    header = [r[0]["markdown"] for r in read(path)["layout"]["header"]]
    assert sorted(header) == ["Contact", "My own title", "NEW banner"]
    written = read(path)["design"]["standard_written"]
    assert written["layout.header[a][1]"]["released"] is True
    assert written["layout.header[a][1]"]["hash"] == C.row_hash(md("Title"))
    assert apply(capsys, str(path))[1]["specs"][0]["changes"] == []


def test_a_released_row_stays_released_when_the_standard_drops_a_row_above_it(
        tmp_path, capsys):
    repo = rows_repo(tmp_path, ["R0", "R1", "R2"])
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["header"].__setitem__(1, md("R1 mine")))
    apply(capsys, str(path))
    (repo / "standards" / "a.yaml").write_text(
        "name: a\ndefault: true\ncontent: {header: [[{markdown: R1, width: 12, height: 1}], "
        "[{markdown: R2, width: 12, height: 1}]]}\n", encoding="utf-8")
    apply(capsys, str(path))
    assert [r[0]["markdown"] for r in read(path)["layout"]["header"]] == ["R1 mine", "R2"]
    # The released record moved with its row (R1 is now the standard's row 0), and the
    # dropped R0's key went to it, not lost under the removal.
    assert read(path)["design"]["standard_written"] == {
        "layout.header[a][0]": {"layer": "a", "hash": C.row_hash(md("R1")), "released": True},
        "layout.header[a][1]": {"layer": "a", "hash": C.row_hash(md("R2"))}}
    assert apply(capsys, str(path))[1]["specs"][0]["changes"] == []


def test_a_row_reads_alike_with_or_without_its_defaults_written(tmp_path, capsys):
    """Decompile writes a markdown block's width and height back explicitly."""
    org = ("name: org\ndefault: true\ncontent: {footer: [[{markdown: 'Legal'}]]}\n"
           "locked: {content: [footer]}\n")
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: (d["layout"]["footer"].__setitem__(
        -1, [{"markdown": "Legal", "width": 12, "height": 4}]), d.pop("design")))
    assert apply(capsys, str(path), "--check")[0] == 0
    code, out = apply(capsys, str(path), "--claim")
    assert {c["action"] for c in out["specs"][0]["changes"]} == {"claim"}
    assert [r[0]["markdown"] for r in read(path)["layout"]["footer"]].count("Legal") == 1


def test_locked_never_takes_an_authors_own_row_for_the_standards(tmp_path, capsys):
    org = ("name: org\ndefault: true\ncontent: {footer: [[{markdown: Legal, width: 12, "
           "height: 1}]]}\nlocked: {content: [footer]}\n")
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: (d["layout"]["footer"].pop(),
                          d["layout"]["footer"].insert(0, md("My disclaimer, keep me"))))
    apply(capsys, str(path), "--locked")
    footer = [r[0]["markdown"] for r in read(path)["layout"]["footer"]]
    assert footer == ["My disclaimer, keep me", "my note", "Legal"]


def test_certification_details_wait_for_a_certified_by_the_author_removed(tmp_path, capsys):
    org = "name: org\ndefault: true\ncontent: {certified_by: Platform, footer: [[{markdown: A}]]}\n"
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].pop("certified_by"))
    apply(capsys, str(path))
    (repo / "standards" / "org.yaml").write_text(
        "name: org\ndefault: true\ncontent: {certified_by: Platform, certification_details: "
        "Reviewed, footer: [[{markdown: B}]]}\n", encoding="utf-8")
    code, out = apply(capsys, str(path))
    assert code == 0, out
    data = read(path)
    assert "certification_details" not in data["dashboard"]
    assert data["layout"]["footer"][-1] == [{"markdown": "B"}]
    [f] = [f for f in findings(capsys, path, "standard.content-released")
           if f["where"] == "dashboard.certification_details"]
    assert "wait for dashboard.certified_by" in f["detail"]


def test_a_renamed_chart_leaves_an_entry_that_check_names_and_apply_drops(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: (d["charts"][0].update(name="K2"),
                          d["layout"].__setitem__("rows", [["K2", "R"]])))
    assert run_ok(capsys, "validate", str(path)) == {"ok": True, "stage": "schema"}
    [f] = [f for f in findings(capsys, path, "standard.content-stale")
           if f["where"] == "charts[K].number_format"]
    assert "no longer has" in f["detail"]
    apply(capsys, str(path))
    written = read(path)["design"]["standard_written"]
    assert "charts[K].number_format" not in written
    # K2 holds the standard's value under no record: --claim records it.
    apply(capsys, str(path), "--claim")
    assert read(path)["design"]["standard_written"]["charts[K2].number_format"] == {
        "layer": "finance", "value": ",.0f"}


def test_held_locked_certification_details_say_they_wait(tmp_path, capsys):
    org = ("name: org\ndefault: true\ncontent: {certified_by: Platform, "
           "certification_details: Quarterly}\n"
           "locked: {content: [certified_by, certification_details]}\n")
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo)
    apply(capsys, str(path))
    edit(path, lambda d: (d["dashboard"].pop("certified_by"),
                          d["dashboard"].pop("certification_details")))
    details = [f for f in findings(capsys, path, "standard.content-locked")
               if f["where"] == "dashboard.certification_details"]
    assert len(details) == 1 and "wait for dashboard.certified_by" in details[0]["detail"]
    assert "differs" not in details[0]["detail"]
    code, _ = apply(capsys, str(path), "--locked")
    assert code == 0 and read(path)["dashboard"]["certification_details"] == "Quarterly"


def test_check_strict_fails_on_any_change_apply_would_make(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    (repo / "standards" / "teams" / "finance.yaml").write_text(
        FINANCE.replace('"Finance"', '"Finance v2"'), encoding="utf-8")
    assert apply(capsys, str(path), "--check")[0] == 0          # unlocked: listed, passes
    code, out = apply(capsys, str(path), "--check", "--strict")
    assert code == 1 and out["strict"] and out["specs"][0]["pending"]
    code, out = run(capsys, "standards", "apply", str(path), "--strict")
    assert code == 1 and out["errors"][0]["code"] == "usage"
    apply(capsys, str(path))
    assert apply(capsys, str(path), "--check", "--strict")[0] == 0


def test_mcp_check_strict(repo, monkeypatch, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    (repo / "standards" / "teams" / "finance.yaml").write_text(
        FINANCE.replace('"Finance"', '"Finance v2"'), encoding="utf-8")
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    assert mcp_call("standards_apply", spec_json=path.read_text(), check=True)["ok"] is True
    assert mcp_call("standards_apply", spec_json=path.read_text(), check=True,
                    strict=True)["ok"] is False


# -- the live fixture -----------------------------------------------------------


def test_the_live_fixture_applies_cleanly_offline():
    """tests/fixtures/standards_live is what tools/ci_live_standards.py applies to a real
    Superset: its standard must write content, settle in one run, and compile."""
    from chartwright.compiler import compile_bundle
    from chartwright.design.standards import StandardsSource, apply_spec
    from chartwright.testing import stub_resolution

    folder = REPO / "tests" / "fixtures" / "standards_live"
    source = StandardsSource(folder / "standards")
    data = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    new, entry = apply_spec(data, load_spec(data), source.standard_for(load_spec(data)))
    assert not entry["errors"] and len(entry["changes"]) == 9
    assert new["dashboard"]["css"].startswith("@import url(")
    again, entry2 = apply_spec(new, load_spec(new), source.standard_for(load_spec(new)))
    assert again == new and entry2["changes"] == []
    spec = load_spec(new)
    assert compile_bundle(spec, stub_resolution(spec))


def test_the_live_job_runs_the_standards_check():
    import yaml

    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    runs = [s.get("run", "") for s in ci["jobs"]["live"]["steps"]]
    assert any("tools/ci_live_standards.py" in r for r in runs)


# -- pins for behaviour a mutation run showed untested ---------------------------


def test_footer_rows_go_innermost_layer_first(tmp_path, capsys):
    """The org's row sits at the very bottom: footer rows are innermost layer first."""
    org = "name: org\ndefault: true\ncontent: {footer: [[{markdown: Org}]]}\n"
    team = "name: team\nextends: org\ncontent: {footer: [[{markdown: Team}]]}\n"
    repo = make_repo(tmp_path / "r", {"org.yaml": org, "team.yaml": team}).parent
    path = spec_file(repo, standard="team")
    apply(capsys, str(path))
    assert [r[0]["markdown"] for r in read(path)["layout"]["footer"]] == [
        "my note", "Team", "Org"]


def test_check_fails_when_locked_css_markers_cant_be_read(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(
        css=d["dashboard"]["css"].replace("/* cw:end org */", "")))
    code, out = apply(capsys, str(path), "--check")
    assert code == 1 and out["specs"][0]["locked_stale"] == ["dashboard.css"]


def test_check_fails_on_an_error_even_with_nothing_locked(tmp_path, capsys):
    org = "name: org\ndefault: true\ncontent: {css: '.a { color: red; }'}\n"
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo, data={**DATA, "dashboard": {
        **DATA["dashboard"], "css": "/* cw:std org 0123456789ab */\n.a {}\n"}})
    code, out = apply(capsys, str(path), "--check")
    assert code == 1 and out["specs"][0]["locked_stale"] == []
    assert out["specs"][0]["errors"][0]["code"] == "css_markers"


def test_a_released_row_is_the_authors_for_repairs_too(tmp_path, capsys):
    """The markdown-height repair leaves a standard's rows alone, not rows released to
    the author."""
    org = "name: org\ndefault: true\ncontent: {footer: [[{markdown: Legal notice}]]}\n"
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo, data={**DATA, "layout": {"rows": [["K", "R"]]}})
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["footer"][0][0].update(markdown="Legal notice!"))
    apply(capsys, str(path))
    assert read(path)["design"]["standard_written"]["layout.footer[org][0]"]["released"]
    fixed, _ = advise_and_fix(read(path))
    assert fixed["layout"]["footer"][0][0]["height"] == 2


def test_a_released_row_put_back_word_for_word_is_still_the_authors(tmp_path, capsys):
    """A row the author edited and then restored to the standard's exact text matches the
    released record's hash; it is still theirs, so repairs may touch it."""
    org = "name: org\ndefault: true\ncontent: {footer: [[{markdown: Legal notice}]]}\n"
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo, data={**DATA, "layout": {"rows": [["K", "R"]]}})
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["footer"][0][0].update(markdown="Legal notice!"))
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["footer"][0][0].update(markdown="Legal notice"))
    assert C.owned_rows(load_spec(read(path))) == set()
    fixed, _ = advise_and_fix(read(path))
    assert fixed["layout"]["footer"][0][0]["height"] == 2


def test_blocks_go_after_a_leading_namespace(tmp_path, capsys):
    css = "@namespace svg url(http://www.w3.org/2000/svg);\n.mine { color: red; }"
    path = two_layer_css(tmp_path, css)
    apply(capsys, str(path))
    assert read(path)["dashboard"]["css"].startswith(
        "@namespace svg url(http://www.w3.org/2000/svg);\n/* cw:std a ")


def test_a_row_with_another_background_is_not_the_edited_row(repo, capsys):
    """Shape includes the background: a white card where a transparent row stood is not
    that row, edited."""
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["layout"]["footer"].__setitem__(-1, {
        "row": [{"markdown": "Confidential. Acme Corp. 2026.", "width": 12, "height": 1}],
        "background": "white"}))
    _, out = apply(capsys, str(path), "--locked")
    footer = read(path)["layout"]["footer"]
    assert footer[-2]["background"] == "white" and footer[-1] == ORG_FOOTER


def test_mcp_standards_apply_returns_a_typed_error_not_a_raise(repo, monkeypatch):
    monkeypatch.setenv("CHARTWRIGHT_STANDARDS_DIR", str(repo / "standards"))
    import chartwright.design.standards as st

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(st, "apply_spec", boom)
    out = mcp_call("standards_apply", spec_json=json.dumps(DATA))
    assert out["ok"] is False and out["errors"][0]["code"] == "unexpected"


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


def test_reclassifying_swaps_a_locked_classification_row_with_a_warning(repo, capsys):
    """As DESIGN-BRAIN sec.18 documents: the lock holds the rows of the classification
    the spec has; the classification is the author's field."""
    data = {**DATA, "dashboard": {**DATA["dashboard"], "classification": "confidential"}}
    org = repo / "standards" / "org.yaml"
    org.write_text(ORG.replace("content: [footer, css]",
                               "content: [footer, css, footer_by_classification]"),
                   encoding="utf-8")
    path = spec_file(repo, standard="finance", data=data)
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(classification="public"))
    stale = [f for f in findings(capsys, path) if f["where"].startswith(
        "layout.footer[org][classification=confidential]")]
    assert [(f["rule"], f["severity"]) for f in stale] == [("standard.content-stale", "warn")]
    apply(capsys, str(path))
    assert "Named recipients only." not in json.dumps(read(path)["layout"]["footer"])


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
    # The org block is locked: unmarked text conforms only once --claim marks it.
    assert "dashboard.css[org]" in apply(capsys, str(path), "--check")[1]["specs"][0][
        "locked_stale"]
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
                                  "specs": [paths[1].as_posix(), paths[2].as_posix()]}]
    assert scheme["released"] == [paths[0].as_posix()] and scheme["current"] == 0
    footer = items["layout.footer[org][0]"]
    assert footer["locked_by"] == "org" and footer["locked"] == [paths[1].as_posix()]
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
            f"{C.row_hash(ORG_FOOTER)})") in text


def test_one_pull_request_per_team(repo, capsys):
    fin = spec_file(repo, "f.json", standard="finance")
    org = spec_file(repo, "o.json")
    code, out = apply(capsys, str(fin), str(org), "--standard", "finance")
    assert code == 0 and out["other_standards"] == [org.as_posix()]
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
    assert data["design"]["standard_written"]["charts[K].number_format"]["released"]
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


@pytest.mark.parametrize("css, shown", [
    (".a{height:0;overflow:hidden}", "height: 0; overflow: hidden"),
    (".a{max-height:0px;overflow-y:clip}", "max-height: 0px; overflow-y: clip"),
    (".a{position:absolute;left:-9999px}", "left: -9999px"),
    (".a{text-indent:-10000em}", "text-indent: -10000em"),
    (".a{color:transparent}", "color: transparent"),
    (".a{opacity:.0}", "opacity: .0"),
    (".a{opacity:0%}", "opacity: 0%"),
    (".a{opacity:0.00 !important}", "opacity: 0.00"),
    (".a{display:/**/none}", "display: none"),
    (".a{clip-path:inset(100%)}", "clip-path: inset(100%)"),
    (".a{clip:rect(0 0 0 0)}", "clip: rect(0 0 0 0)"),
    (".a{transform:scale(0)}", "transform: scale(0)"),
    (".a{transform:translateX(0) scaleY(0.0)}", "transform: translateX(0) scaleY(0.0)"),
    (".a{font-size:0}", "font-size: 0"),
    (".a{display:none}", "display: none"),
    ("[data-test='x']:nth-last-child(1){visibility:hidden}", "visibility: hidden"),
    ("@media screen { .a { visibility: collapse } }", "visibility: collapse"),
])
def test_css_hides_knows_the_common_ways_to_hide(repo, capsys, css, shown):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(css=d["dashboard"]["css"] + "\n" + css))
    [f] = findings(capsys, path, "standard.css-hides")
    assert f"{{ {shown} }}" in f["detail"], f["detail"]


@pytest.mark.parametrize("css", [
    ".a{opacity:0.5}", ".a{height:0}", ".a{left:-20px}", ".a{color:#000}",
    ".a{clip-path:none}", ".a{transform:scale(1)}", "/* .a{display:none} */ .b{}",
    ".a{background-color:transparent}",
])
def test_css_hides_leaves_visible_css_alone(repo, capsys, css):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(css=d["dashboard"]["css"] + "\n" + css))
    assert findings(capsys, path, "standard.css-hides") == []


def test_css_hides_names_the_selector_not_a_comment(repo, capsys):
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(
        css=d["dashboard"]["css"] + "\n/* hide { } it */ .x .y { display: none }"))
    [f] = findings(capsys, path, "standard.css-hides")
    assert "`.x .y { display: none }`" in f["detail"]


def test_css_hides_reads_a_lower_layers_block(repo, capsys):
    """finance's own block can hide the footer org locks: only the locking layers'
    blocks are trusted."""
    fin = repo / "standards" / "teams" / "finance.yaml"
    fin.write_text(FINANCE.replace("color: #003366;", "display: none;"), encoding="utf-8")
    path = spec_file(repo, standard="finance")
    apply(capsys, str(path))
    [f] = findings(capsys, path, "standard.css-hides")
    assert "the finance block" in f["detail"]


def test_css_hides_needs_locked_rows(tmp_path, capsys):
    org = ("name: org\ndefault: true\ncontent: {css: '.a { color: red; }'}\n"
           "locked: {content: [css]}\n")
    repo = make_repo(tmp_path / "r", {"org.yaml": org}).parent
    path = spec_file(repo, data={**DATA, "dashboard": {**DATA["dashboard"],
                                                       "css": ".b { display: none }"}})
    apply(capsys, str(path))
    assert findings(capsys, path, "standard.css-hides") == []


def test_css_hides_is_locked_with_the_content(repo, capsys):
    assert load_standards(repo / "standards").get("finance").locked_rules[
        "standard.css-hides"] == "org"
    path = spec_file(repo, standard="finance", ignore=["standard.css-hides"])
    apply(capsys, str(path))
    edit(path, lambda d: d["dashboard"].update(css=d["dashboard"]["css"] + "\n.a{display:none}"))
    _, out = run(capsys, "standards", "check", str(path))
    entry = out["specs"][0]
    assert [f["rule"] for f in entry["findings"] if f["rule"] == "standard.css-hides"]
    assert entry["locks"]["refused_ignores"] == [{"entry": "standard.css-hides",
                                                  "locked_by": "org"}]


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
    assert footer["value"] == ORG_FOOTER and footer["recorded"]["hash"] == C.row_hash(ORG_FOOTER)
    assert "org's standards file" in footer["override"]
    scheme = rows["dashboard.color_scheme"]
    assert (scheme["source"], scheme["value"], scheme["standard"]) == (
        "released", "bnbColors", "supersetColors")
    assert scheme["override"].startswith("it is yours")
    text = run_ok(capsys, "explain", str(path))
    assert text.splitlines()[0].startswith("Design defaults (design brain 8")
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
         lambda d: d["layout"]["footer"][1][0].update(markdown="Acme Corp. Internal data: share inside the company only."))
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


# -- a header or footer row a UI dashboard already holds in its body ----------------------------


def _ui_board(header_first: bool = False) -> dict:
    """As decompile (and adopt) read a dashboard built in the UI: no header or footer,
    the legal line its last body row, maybe the team banner its first."""
    rows = [["K", "R"], [ORG_FOOTER[0]]]
    if header_first:
        rows.insert(0, [FIN_HEADER[0]])
    return {**DATA, "dashboard": {k: v for k, v in DATA["dashboard"].items() if k != "css"},
            "layout": {"rows": rows}}


def test_a_footer_row_already_in_the_body_is_never_added_twice(repo, capsys):
    """Fleet review 3, #1: the adopted board's legal line sat in the body, and apply
    added the standard's footer below it, so the dashboard showed it twice."""
    path = spec_file(repo, data=_ui_board(), standard="org")
    code, out = apply(capsys, str(path))
    layout = read(path)["layout"]
    assert layout["rows"].count([ORG_FOOTER[0]]) == 1
    assert ORG_FOOTER not in (layout.get("footer") or []), "not added a second time"
    # Present and locked: --check has nothing to fail on.
    assert "layout.footer[org][0]" not in apply(capsys, str(path), "--check")[1]["specs"][0][
        "locked_stale"]


def test_claim_moves_the_body_row_into_the_footer_and_records_it(repo, capsys):
    path = spec_file(repo, data=_ui_board(header_first=True), standard="finance")
    code, out = apply(capsys, str(path), "--claim")
    assert code == 0, out
    layout = read(path)["layout"]
    assert layout["rows"] == [["K", "R"]]
    assert layout["footer"] == [ORG_FOOTER] and layout["header"] == [FIN_HEADER]
    written = read(path)["design"]["standard_written"]
    assert "layout.footer[org][0]" in written and "layout.header[finance][0]" in written
    again = path.read_bytes()
    apply(capsys, str(path), "--claim")
    assert path.read_bytes() == again, "a second --claim is a no-op"


def test_only_the_bodys_edge_counts(repo, capsys):
    """The same words in the middle of the body are the author's own: the footer is
    added as usual."""
    data = _ui_board()
    data["layout"]["rows"] = [[ORG_FOOTER[0]], ["K", "R"]]
    path = spec_file(repo, data=data, standard="org")
    apply(capsys, str(path))
    assert read(path)["layout"]["footer"][0] == ORG_FOOTER


def test_explain_says_the_body_already_holds_it(repo, capsys):
    path = spec_file(repo, data=_ui_board(), standard="org")
    rows = C.explain_rows(std(repo, "org"), load_spec(read(path)))
    row = next(r for r in rows if r["item"] == "layout.footer[org][0]")
    assert "already hold" in row["pending"] and "--claim" in row["pending"]
