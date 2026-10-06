"""Repo hygiene: claims that silently drift out of true.

'125 tests' outlived four batches of new tests across five doc locations,
which is what a hand-maintained count always does. The counts now live in
exactly ONE place and this test fails when they drift -- the same rule the
generated rule table in DESIGN-BRAIN.md follows.
"""

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VERIFICATION = REPO / "docs" / "VERIFICATION.md"
_ROW = re.compile(r"Offline suite \((\d+) tests, (\d+) modules\)")


def test_design_brain_rule_table_matches_the_registry():
    """DESIGN-BRAIN.md section 7 claimed "GENERATED from the registry" while
    pointing at a placeholder snippet, so it was hand-maintained and had
    drifted (four rules that vary severity all printed as their default).
    There is a real generator now, and this runs its --check path."""
    sys.path.insert(0, str(REPO))
    from tools.gen_rule_table import main as gen

    assert gen(["--check"]) == 0, "run: python tools/gen_rule_table.py --write"


def test_no_test_module_imports_through_the_tests_package():
    """`tests` has no __init__.py, so pytest puts tests/ on sys.path, not the
    repo root. `from tests.x import ...` resolves only under `python -m pytest`
    (which adds the CWD) and dies under the bare `pytest` console script CI
    runs -- a module that imports that way is uncollectable in CI while
    passing locally."""
    bad = [p.name for p in (REPO / "tests").glob("test_*.py")
           if re.search(r"^\s*(from|import)\s+tests\b", p.read_text(encoding="utf-8"), re.M)]
    assert not bad, f"import siblings directly (`from test_x import ...`), not via `tests.`: {bad}"


def test_features_cli_table_covers_every_verb():
    """v2 roadmap item 21 (the CLI Verbs table is missing the design-brain
    verbs) was reported as executed and never landed: `brief`, `advise`,
    `redesign` and `calibrate` shipped undocumented. A hand-maintained table
    of a growing list drifts; this fails until it is caught up."""
    cli = (REPO / "chartwright" / "cli.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'sub\.add_parser\(\s*"(\w+)"', cli))
    registered |= {"check", "apply", "plan"}          # added in a loop, not literally
    # A verb with sub-verbs (`standards check`) is documented by its sub-verb rows.
    documented = set(re.findall(r"\| `chartwright (\w+)(?: \w+)?`",
                                (REPO / "docs" / "FEATURES.md").read_text(encoding="utf-8")))
    assert not registered - documented, (
        f"docs/FEATURES.md CLI Verbs table is missing: {sorted(registered - documented)}")
    assert not documented - registered, (
        f"docs/FEATURES.md documents verbs that do not exist: {sorted(documented - registered)}")


def test_documented_mcp_tool_count_is_right():
    """The other half of item 21's sibling, item 20: 'Six tools' outlived the
    design brain adding four more."""
    words = {"Six": 6, "Seven": 7, "Eight": 8, "Nine": 9, "Ten": 10,
             "Eleven": 11, "Twelve": 12, "Thirteen": 13, "Fourteen": 14}
    actual = len(re.findall(r"^@mcp\.tool\(\)",
                            (REPO / "chartwright" / "mcp_server.py").read_text(encoding="utf-8"),
                            re.M))
    for doc in (REPO / "README.md", REPO / "docs" / "FEATURES.md"):
        text = doc.read_text(encoding="utf-8")
        for claim in re.findall(r"\b([A-Z][a-z]+|\d+) tools\b", text):
            n = words.get(claim, int(claim) if claim.isdigit() else None)
            if n is not None:
                assert n == actual, f"{doc.name} says {claim} MCP tools; there are {actual}"


def test_verification_is_the_only_place_with_a_test_count():
    """Counts stated in several places drift in several places."""
    stale = []
    for doc in (REPO / "README.md", REPO / "docs" / "FEATURES.md", REPO / "skill" / "SKILL.md"):
        for hit in re.findall(r"\b(\d+) tests\b", doc.read_text(encoding="utf-8")):
            stale.append(f"{doc.name}: '{hit} tests'")
    assert not stale, f"move these to docs/VERIFICATION.md, the single source: {stale}"


def test_documented_counts_match_what_pytest_collects(request):
    # The documented count is what CI runs: `pip install -e ".[dev,mcp]"`.
    # Without the mcp extra, test_mcp_server.py skips at COLLECTION and the
    # total is legitimately lower -- assert nothing rather than fail a
    # contributor's partial environment.
    pytest.importorskip("mcp", reason="counts are pinned to the full [dev,mcp] suite CI runs")
    collected = request.session.testscollected
    if collected < 100:
        pytest.skip("partial run; the count is only meaningful for the whole suite")
    match = _ROW.search(VERIFICATION.read_text(encoding="utf-8"))
    assert match, "docs/VERIFICATION.md lost its 'Offline suite (N tests, M modules)' row"
    tests, modules = int(match.group(1)), int(match.group(2))
    actual_modules = len(list((REPO / "tests").glob("test_*.py")))
    assert (tests, modules) == (collected, actual_modules), (
        f"docs/VERIFICATION.md says {tests} tests / {modules} modules; "
        f"pytest collected {collected} across {actual_modules} modules. "
        f"Update that one row.")


def test_the_schema_file_matches_the_schema_command():
    """schema/dashboard_spec.schema.json sat at 0.1.0 for months while the spec
    gained fields; anyone validating against the file got the old contract."""
    from chartwright.spec import json_schema

    on_disk = (REPO / "schema" / "dashboard_spec.schema.json").read_text(encoding="utf-8")
    assert on_disk == json.dumps(json_schema(), indent=2) + "\n", (
        "regenerate: chartwright schema > schema/dashboard_spec.schema.json")


def test_every_layout_guide_example_compiles():
    """docs/LAYOUT-GUIDE.md says every example on the page compiles as shown: each
    ```json layout there validates and compiles, its charts stubbed by name."""
    from chartwright.compiler import compile_bundle
    from chartwright.spec import load_spec
    from chartwright.testing import stub_resolution

    guide = (REPO / "docs" / "LAYOUT-GUIDE.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", guide, re.S)
    assert len(blocks) >= 5, "the guide lost its json examples"

    def names(node) -> list[str]:
        if isinstance(node, str):
            return [node]
        if isinstance(node, list):
            return [n for x in node for n in names(x)]
        if isinstance(node, dict) and "row" in node:
            return names(node["row"])
        return []

    for block in blocks:
        layout = json.loads("{" + block + "}")["layout"]
        holders = [layout, *layout.get("tabs", [])]
        charts = {v for h in holders for v in (h.get("legend") or {}).values() if isinstance(v, str)}
        charts |= {n for h in holders for key in ("rows", "header", "footer")
                   for n in names(h.get(key) or [])}
        spec = load_spec({
            "spec_version": "1", "dashboard": {"title": "Guide", "slug": "sdc-guide"},
            "charts": [{"name": n, "type": "big_number_total", "metric": "COUNT(*)",
                        "dataset": {"database": "examples", "table": "t"}} for n in sorted(charts)],
            "layout": layout})
        assert compile_bundle(spec, stub_resolution(spec))


def test_package_version_matches_pyproject():
    """__version__ said 0.1.0 while pyproject.toml said 0.2.0."""
    import tomllib

    import chartwright

    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert chartwright.__version__ == project["version"]


FIXTURES = REPO / "tests" / "fixtures"
SPEC_FIXTURES = sorted(p for p in FIXTURES.glob("*.json"))
LIVE_FIXTURES = sorted(FIXTURES.glob("live_*.json"))


@pytest.mark.parametrize("path", SPEC_FIXTURES, ids=lambda p: p.name)
def test_every_spec_fixture_compiles_offline(path):
    """The live fixtures are applied by CI against a real Superset, but they
    must also validate and compile here with stub ids, like every other spec."""
    from chartwright.compiler import compile_bundle
    from chartwright.spec import load_spec
    from chartwright.testing import stub_resolution

    spec = load_spec(json.loads(path.read_text(encoding="utf-8")))
    assert compile_bundle(spec, stub_resolution(spec))


def test_fixture_slugs_are_distinct_and_carry_a_tool_prefix():
    """CI applies several fixtures to one instance, and people apply them to
    instances that hold real dashboards: a slug like `airline-operations`
    collided with one in use. Every fixture slug is unique and prefixed."""
    slugs = {p.name: json.loads(p.read_text(encoding="utf-8"))["dashboard"]["slug"]
             for p in SPEC_FIXTURES}
    assert len(set(slugs.values())) == len(slugs), slugs
    assert all(s.startswith(("sdc-", "cw-")) for s in slugs.values()), slugs


def test_the_live_job_applies_kitchen_sink_and_every_live_fixture():
    """Each Superset version in the live matrix applies the kitchen sink and
    every tests/fixtures/live_*.json spec through tools/ci_live_check.py."""
    import yaml

    assert len(LIVE_FIXTURES) >= 2, "expected the live display and dashboard controls fixtures"
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    live = ci["jobs"]["live"]
    assert set(live["strategy"]["matrix"]["superset"]) == {"4.1.4", "5.0.0", "6.1.0"}
    runs = [s["run"] for s in live["steps"] if "ci_live_check.py" in s.get("run", "")]
    wanted = ["tests/fixtures/kitchen_sink.json"] + [
        f"tests/fixtures/{p.name}" for p in LIVE_FIXTURES]
    for fixture in wanted:
        assert any(f"--spec {fixture}" in r for r in runs), (fixture, runs)
