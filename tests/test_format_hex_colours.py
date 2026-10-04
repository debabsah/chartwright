"""Colour rules take any #RRGGBB besides green / amber / red. A name keeps
its shades (pastel for cells, darker for text); a hex is painted as written,
for cells and text alike, and reads back as itself unless it is the named
shade for its paint."""

import pytest
from pydantic import ValidationError

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import _format_to_spec, decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

from test_design_rules_v2 import mk as design_mk
from test_design_rules_v2 import pivot, run
from test_table_sort import _lookup, aggregate, mk, params_for


def chart(*rules):
    return aggregate(metrics=["MAX(rate) AS Rate", "MAX(st) AS status"],
                     conditional_formatting=list(rules))


def rule(color, paint=None, **over):
    out = {"metric": "status", "operator": "=", "target": 1, "color": color, "apply_to": "Rate"}
    if paint:
        out["paint"] = paint
    return out | over


def test_hex_is_painted_as_written_for_cell_and_text():
    p = params_for(chart(rule("#0057B8"), rule("#0057b8", "text")))
    assert [cf["colorScheme"] for cf in p["conditional_formatting"]] == ["#0057B8", "#0057B8"]
    assert p["conditional_formatting"][1]["objectFormatting"] == "TEXT_COLOR"


def test_names_keep_their_shades():
    p = params_for(chart(rule("green"), rule("green", "text"), rule("red"), rule("amber", "text")))
    assert [cf["colorScheme"] for cf in p["conditional_formatting"]] == \
        ["#ACE1C4", "#1B7F3B", "#EFA1AA", "#8A6100"]


def test_hex_is_normalised_to_upper_case():
    spec = mk(chart(rule("#a1b2c3")))
    assert spec.charts[0].conditional_formatting[0].color == "#A1B2C3"
    assert spec.charts[0].conditional_formatting[0].paint_hex() == "#A1B2C3"


@pytest.mark.parametrize("bad", ["#12345", "#1234567", "ACE1C4", "#GGGGGG", "#ACE1C4FF",
                                 "teal", "Green", "", 123, None])
def test_anything_else_is_rejected(bad):
    with pytest.raises(ValidationError, match=r"green, amber, red or #RRGGBB"):
        mk(chart(rule(bad)))


def test_hex_round_trips_with_no_drift():
    spec = mk(chart(rule("#0057B8"), rule("#7a1fa2", "text", target=2), rule("green", target=3)))
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _lookup(spec))
    assert result.losses == [], result.losses_json()
    colours = [r["color"] for r in result.spec["charts"][0]["conditional_formatting"]]
    assert colours == ["#0057B8", "#7A1FA2", "green"]
    assert _normalize(load_spec(result.spec))["charts"] == _normalize(spec)["charts"]


def test_decompile_names_only_the_shade_for_its_paint():
    base = {"column": "status", "operator": "=", "targetValue": 1}
    assert _format_to_spec({**base, "colorScheme": "#ace1c4"})["color"] == "green"
    text = {**base, "objectFormatting": "TEXT_COLOR"}
    assert _format_to_spec({**text, "colorScheme": "#1B7F3B"})["color"] == "green"
    # the cell shade painting text is not the named text colour: kept as hex
    assert _format_to_spec({**text, "colorScheme": "#ACE1C4"})["color"] == "#ACE1C4"
    assert _format_to_spec({**base, "colorScheme": "#1B7F3B"})["color"] == "#1B7F3B"


def test_format_bands_compare_the_painted_shade():
    def bands(*colours):
        rules = [{"metric": "Val", "operator": "<", "target": 50, "color": colours[0]},
                 {"metric": "Val", "operator": "between", "target_left": 40,
                  "target_right": 60, "color": colours[1]}]
        p = pivot("P", row_limit=20, height=10, metrics=["SUM(v) AS Val"],
                  conditional_formatting=rules)
        return [f for f in run(design_mk([p])).findings if f.rule == "chart.format-bands"]

    assert any(f.severity == "warn" for f in bands("green", "#B3261E"))
    assert not any(f.severity == "warn" for f in bands("green", "#ACE1C4"))  # the same shade
