"""Contract regression: every params key the compiler emits must be declared
by the target Superset version's viz plugin (or be a known non-control key).
Contract source: tools/contracts/params-contract.json, extracted from plugin
controlPanel source at each supported tag (see docs/CONTRACTS.md)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from params_drift import CONTRACT, check, emitted_keys_by_viz_type

VERSIONS = sorted(json.loads(CONTRACT.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def emitted():
    return emitted_keys_by_viz_type()


@pytest.mark.parametrize("version", VERSIONS)
def test_emitted_params_within_contract(version, emitted):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert check(version, contract, emitted) == []


def test_kitchen_sink_covers_all_contract_viz_types(emitted):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    missing = set(contract["6.1.0"]) - set(emitted)
    assert not missing, f"fixture no longer exercises: {sorted(missing)}"
