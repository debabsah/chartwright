"""Doc truth: the evidence page's hard numbers must match reality.

'125 tests' outlived four batches of new tests across five doc locations,
which is what a hand-maintained count always does. The counts now live in
exactly ONE place and this test fails when they drift -- the same rule the
generated rule table in DESIGN-BRAIN.md follows.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VERIFICATION = REPO / "docs" / "VERIFICATION.md"
_ROW = re.compile(r"Offline suite \((\d+) tests, (\d+) modules\)")


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
