"""The params contract of a plain control panel (the waterfall's) is extracted from
the plugin's source (tools/extract_panel_contract.py), as the Mixed Chart's is."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from extract_panel_contract import PANELS, extract, source_paths  # noqa: E402
from params_drift import CONTRACT  # noqa: E402

PANEL = """\
import { showValueControl } from '../controls';

const config: ControlPanelConfig = {
  controlPanelSections: [
    { controlSetRows: [['x_axis'], ['metric'],
      [{ name: 'time_grain_sqla', config: {} }, 'temporal_columns_lookup']] },
    sections.titleControls,
    { controlSetRows: [[showValueControl],
      [{ name: 'x_ticks_layout', config: { choices: formatSelectOptions(['auto', 'flat']) } }],
      [{ name: 'whiskerOptions', config: { choices: [['Tukey', t('Tukey')]] } }]] },
  ],
  controlOverrides: { groupby: { label: t('Breakdowns') } },
};

export default config;
"""

CONTROLS = """\
export const showValueControl = { name: 'show_value' };
export const notUsedHere = { name: 'should_not_appear' };
"""


def _tree(tmp_path: Path, tag: str) -> Path:
    files = {**{rel: "" for rel in source_paths("waterfall").values()},
             PANELS["waterfall"]: PANEL, source_paths("waterfall")["controls"]: CONTROLS,
             source_paths("waterfall")["titleControls"]: "x = [{ name: 'x_axis_title' }]"}
    for rel, text in files.items():
        path = tmp_path / tag / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_extractor_reads_rows_sections_and_imports(tmp_path):
    keys = extract("waterfall", "6.1.0", _tree(tmp_path, "6.1.0"))
    # Choices and labels are not controls; an import the config doesn't use adds nothing.
    assert keys == sorted(["x_axis", "metric", "time_grain_sqla", "temporal_columns_lookup",
                           "x_axis_title", "show_value", "x_ticks_layout", "whiskerOptions"])


def test_the_contract_holds_the_full_waterfall_panel():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for tag in ("4.1.4", "5.0.0", "6.1.0"):
        waterfall = set(contract[tag]["waterfall"])
        assert {"x_axis", "metric", "groupby", "increase_color", "decrease_color", "total_color",
                "x_ticks_layout", "x_axis_label", "y_axis_label", "currency_format",
                "show_value", "show_legend"} <= waterfall, tag
    assert {"increase_label", "decrease_label", "show_total", "total_label"} <= set(
        contract["6.1.0"]["waterfall"])
    assert not {"increase_label", "show_total"} & set(contract["5.0.0"]["waterfall"])
