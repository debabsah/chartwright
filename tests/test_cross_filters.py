"""cross_filters: spec-owned on/off for Superset dashboard cross-filtering.
The default (False) must leave every existing bundle byte-identical; True must
reach the dashboard metadata, decompile back, and survive a round-trip."""

import io
import json
import zipfile
from pathlib import Path

import yaml

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

FIXTURE = Path(__file__).parent / "fixtures" / "sales_overview.json"


def _spec(cross_filters=None):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if cross_filters is not None:
        data["dashboard"]["cross_filters"] = cross_filters
    return load_spec(data)


def _dashboard_yaml(bundle: bytes) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    name = next(n for n in zf.namelist() if "/dashboards/" in n)
    return yaml.safe_load(zf.read(name))


def _lookup(res):
    by_uuid = {
        ds.uuid: {"database": ds.database_name, "schema": ds.schema, "table": ds.table}
        for ds in res.datasets.values()
    }
    return lambda u: by_uuid.get(u)


def test_default_is_off_and_unchanged():
    spec = _spec()
    assert spec.dashboard.cross_filters is False
    dash = _dashboard_yaml(compile_bundle(spec, stub_resolution(spec)))
    assert dash["metadata"]["cross_filters_enabled"] is False


def test_true_reaches_metadata():
    spec = _spec(True)
    dash = _dashboard_yaml(compile_bundle(spec, stub_resolution(spec)))
    assert dash["metadata"]["cross_filters_enabled"] is True


def test_round_trips_both_ways():
    for value in (False, True):
        spec = _spec(value)
        res = stub_resolution(spec)
        out = decompile_bundle(compile_bundle(spec, res), _lookup(res))
        assert out.losses == []
        assert out.spec["dashboard"]["cross_filters"] is value
        assert _normalize(load_spec(out.spec)) == _normalize(spec)
