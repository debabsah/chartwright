"""Markdown heights in fifths of a unit: 0.2 = 8 px, one Superset grid row (offline)."""

import json
from pathlib import Path

import pytest

from chartwright.compiler import _position, compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

from test_decompile import _stub_lookup_for
from test_layout_footer import _renamed

FIXTURES = Path(__file__).parent / "fixtures"


def _kitchen(height) -> dict:
    data = json.loads((FIXTURES / "kitchen_sink.json").read_text())
    data["layout"]["footer"] = [[{"markdown": "A slim strip", "width": 12, "height": height}]]
    return data


def test_a_fifth_compiles_to_whole_grid_rows():
    """1.6 units = 8 Superset grid rows = 64 px; a whole-unit step would give 40 or 80."""
    assert _position(load_spec(_kitchen(1.6)))["MARKDOWN-sdc-footer-1-1"]["meta"]["height"] == 8


@pytest.mark.parametrize("height", [0.2, 1.6, 2.4, 3])
def test_fifths_round_trip_exactly(height):
    spec = load_spec(_kitchen(height))
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _stub_lookup_for(spec))
    assert result.losses == []
    assert result.spec["layout"]["footer"][0][0]["height"] == height
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


@pytest.mark.parametrize("height", [1.5, 1.61, 0.1])
def test_heights_off_the_grid_are_rejected(height):
    with pytest.raises(ValueError):
        load_spec(_kitchen(height))


def test_whole_units_stay_whole_numbers():
    """Existing specs dump exactly as before: 2 stays 2, not 2.0."""
    block = load_spec(_kitchen(2)).model_dump(exclude_none=True)["layout"]["footer"][0][0]
    assert block["height"] == 2 and isinstance(block["height"], int)


def test_a_block_dragged_off_the_unit_grid_reads_back_exactly():
    """A text block resized in Superset's UI to 22 grid rows used to read back
    rounded to 4 units, hiding the change from `plan`; it now reads 4.4."""
    spec = load_spec(_kitchen(4))
    bundle = _renamed(compile_bundle(spec, stub_resolution(spec)), "height: 20\n", "height: 22\n")
    live = decompile_bundle(bundle, _stub_lookup_for(spec)).spec
    assert live["layout"]["footer"][0][0]["height"] == 4.4
    assert _normalize(load_spec(live)) != _normalize(spec)
