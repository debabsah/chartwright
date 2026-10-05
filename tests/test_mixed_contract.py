"""The mixed_timeseries contract is extracted from the Mixed Chart plugin's
source (tools/extract_mixed_contract.py), so the drift check can see every
Mixed control, not only the ones the compiler emits."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from extract_mixed_contract import SOURCE_PATHS, extract  # noqa: E402
from params_drift import CONTRACT  # noqa: E402

PANEL = """\
import { legendSection, minorTicks } from '../controls';

function createQuerySection(label: string, controlSuffix: string) {
  return { controlSetRows: [[{ name: `metrics${controlSuffix}` }], [{ name: `groupby${controlSuffix}` }]] };
}

function createCustomizeSection(label: string, controlSuffix: string) {
  return [[{ name: `seriesType${controlSuffix}` }]];
}

function createAdvancedAnalyticsSection(label: string, controlSuffix: string) {
  return cloneDeep(sections.advancedAnalyticsControls);
}

const config: ControlPanelConfig = {
  controlPanelSections: [
    { controlSetRows: [['x_axis'], ['time_grain_sqla']] },
    createQuerySection(t('Query A'), ''),
    createAdvancedAnalyticsSection(t('Advanced analytics Query A'), ''),
    createQuerySection(t('Query B'), '_b'),
    createAdvancedAnalyticsSection(t('Advanced analytics Query B'), '_b'),
    sections.annotationsAndLayersControls,
    sections.titleControls,
    { controlSetRows: [['color_scheme'], ...createCustomizeSection(t('Query A'), ''),
      ...createCustomizeSection(t('Query B'), 'B'), [minorTicks], ...legendSection,
      [{ name: 'logAxis', config: {} }]] },
  ],
};

export default config;
"""

CONTROLS = """\
const showLegendControl = { name: 'show_legend' };
const legendTypeControl = { name: 'legendType' };
export const legendSection = [[showLegendControl], [legendTypeControl]];
export const minorTicks = { name: 'minorTicks' };
export const notUsedHere = { name: 'should_not_appear' };
"""


def _tree(tmp_path: Path, tag: str) -> Path:
    files = {
        SOURCE_PATHS["panel"]: PANEL,
        SOURCE_PATHS["controls"]: CONTROLS,
        SOURCE_PATHS["annotationsAndLayersControls"]: "x = { name: 'annotation_layers' }",
        SOURCE_PATHS["titleControls"]: "x = [{ name: 'x_axis_title' }, { name: 'y_axis_title' }]",
        SOURCE_PATHS["advancedAnalyticsControls"]: "x = [{ name: 'rolling_type' }]",
    }
    for rel, text in files.items():
        path = tmp_path / tag / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_extractor_follows_builders_sections_and_imports(tmp_path):
    keys = extract("6.1.0", _tree(tmp_path, "6.1.0"))
    assert keys == sorted([
        "x_axis", "time_grain_sqla", "metrics", "groupby", "metrics_b", "groupby_b",
        "rolling_type", "rolling_type_b", "annotation_layers", "x_axis_title", "y_axis_title",
        "color_scheme", "seriesType", "seriesTypeB", "minorTicks", "show_legend", "legendType",
        "logAxis",
    ])


def test_the_contract_holds_the_full_mixed_panel():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for tag in ("4.1.4", "5.0.0", "6.1.0"):
        mixed = set(contract[tag]["mixed_timeseries"])
        # Controls the compiler never emits are listed too, so drift checks see them.
        assert {"color_scheme", "annotation_layers", "y_axis_bounds", "stack", "stackB",
                "show_value", "x_axis_title", "rolling_type_b"} <= mixed, tag
    assert "legendSort" in contract["6.1.0"]["mixed_timeseries"]
    assert "legendSort" not in contract["4.1.4"]["mixed_timeseries"]
