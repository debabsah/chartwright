"""Property-style round-trip: seeded random spec-mutation chains (the soak
harness's generator over the kitchen-sink vocabulary) must survive
compile -> decompile losslessly at every step. Offline twin of tools/soak.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from soak import SpecMutator

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

FIXTURE = Path(__file__).parent / "fixtures" / "kitchen_sink.json"


def _lookup(res):
    by_uuid = {
        ds.uuid: {"database": ds.database_name, "schema": ds.schema, "table": ds.table}
        for ds in res.datasets.values()
    }
    return lambda u: by_uuid.get(u)


@pytest.mark.parametrize("seed", range(20))
def test_mutation_chain_round_trips(seed):
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mut = SpecMutator(base, seed, "sdc-prop")
    for step in range(6):
        if step:
            mut.mutate()
        spec = load_spec(mut.spec)
        res = stub_resolution(spec)
        bundle = compile_bundle(spec, res)
        out = decompile_bundle(bundle, _lookup(res))
        assert out.losses == [], (seed, step, [loss.as_dict() for loss in out.losses])
        assert _normalize(load_spec(out.spec)) == _normalize(spec), (seed, step)
