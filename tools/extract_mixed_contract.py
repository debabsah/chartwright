"""Extract the mixed_timeseries entry of tools/contracts/params-contract.json
from the Mixed Chart plugin's own source, at each supported Superset tag.

The other entries were extracted from their control panels; the Mixed entry
used to list only the keys the compiler emits (each checked by hand), so
`tools/params_drift.py` could not say which Mixed controls exist. This reads
`MixedTimeseries/controlPanel.tsx` and everything it pulls in:

- the query section, built twice by createQuerySection (suffix '' and '_b');
- the per-query display controls, built twice by createCustomizeSection
  (suffix '' and 'B');
- the advanced-analytics section, cloned per query with the '_b' suffix;
- shared sections (`sections.annotationsAndLayersControls`, `sections.titleControls`);
- controls imported from `../controls` (legend, tooltip, x-axis label controls);
- every control named in the config itself, by string (`['color_scheme']`)
  or by `name:`.

    python tools/extract_mixed_contract.py            # print each tag's key set
    python tools/extract_mixed_contract.py --check    # exit 1 if the JSON differs
    python tools/extract_mixed_contract.py --write    # update the JSON's mixed_timeseries entries

Needs network access (raw.githubusercontent.com); `--src DIR` reads
DIR/<tag>/<path> instead, e.g. a cache of earlier downloads.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONTRACT = REPO / "tools" / "contracts" / "params-contract.json"
TAGS = ("4.1.4", "5.0.0", "6.1.0")
RAW = "https://raw.githubusercontent.com/apache/superset/{tag}/{path}"

EC = "superset-frontend/plugins/plugin-chart-echarts/src"
SECTIONS = "superset-frontend/packages/superset-ui-chart-controls/src/sections"
PANEL = f"{EC}/MixedTimeseries/controlPanel.tsx"
CONTROLS = f"{EC}/controls.tsx"
SECTION_FILES = {
    "annotationsAndLayersControls": f"{SECTIONS}/annotationsAndLayers.tsx",
    "titleControls": f"{SECTIONS}/chartTitle.tsx",
    "advancedAnalyticsControls": f"{SECTIONS}/advancedAnalytics.tsx",
}
# Every source file read, by role (the tests build a --src tree from these).
SOURCE_PATHS = {"panel": PANEL, "controls": CONTROLS, **SECTION_FILES}
# The panel's section builders and the suffixes the config calls them with.
BUILDERS = ("createQuerySection", "createCustomizeSection", "createAdvancedAnalyticsSection")

_DEF = re.compile(r"^(?:export\s+)?(?:const|function)\s+(\w+)", re.M)
_NAME = re.compile(r"name:\s*['`](\w+)['`]")
_TEMPLATE_NAME = re.compile(r"name:\s*`(\w+)\$\{controlSuffix\}`")
_STRING_ROW = re.compile(r"\[\s*'(\w+)'\s*\]")
_IDENT = re.compile(r"\b([A-Za-z_]\w*)\b")


def _read(tag: str, path: str, src: Path | None) -> str:
    if src is not None:
        return (src / tag / path).read_text(encoding="utf-8")
    with urllib.request.urlopen(RAW.format(tag=tag, path=path), timeout=30) as r:
        return r.read().decode("utf-8")


def _bodies(text: str) -> dict[str, str]:
    """Each top-level const/function, by name: its text up to the next one."""
    starts = [(m.start(), m.group(1)) for m in _DEF.finditer(text)]
    return {name: text[start:(starts[i + 1][0] if i + 1 < len(starts) else len(text))]
            for i, (start, name) in enumerate(starts)}


def _names_in(body: str, defs: dict[str, str], seen: set[str]) -> set[str]:
    """Control names in a body, following references to other definitions."""
    out = set(_NAME.findall(body)) | set(_STRING_ROW.findall(body))
    for ident in set(_IDENT.findall(body)):
        if ident in defs and ident not in seen:
            seen.add(ident)
            out |= _names_in(defs[ident], defs, seen)
    return out


def extract(tag: str, src: Path | None = None) -> list[str]:
    panel = _read(tag, PANEL, src)
    panel_defs = _bodies(panel)
    control_defs = _bodies(_read(tag, CONTROLS, src))
    config = panel_defs["config"]
    keys: set[str] = set()

    # Section builders: every name they template, with each suffix the config passes.
    calls = re.findall(r"(\w+)\(\s*t\('[^']*'\),\s*'([^']*)'\s*\)", config)
    for builder, suffix in calls:
        if builder not in BUILDERS:
            raise ValueError(f"{tag}: unknown section builder {builder}")
        body = panel_defs[builder]
        if builder == "createAdvancedAnalyticsSection":
            section = _read(tag, SECTION_FILES["advancedAnalyticsControls"], src)
            keys |= {n + suffix for n in _NAME.findall(section)}
        else:
            keys |= {n + suffix for n in _TEMPLATE_NAME.findall(body)}

    # Shared sections referenced as sections.<name>.
    for section in set(re.findall(r"sections\.(\w+)", config)):
        if section not in SECTION_FILES:
            raise ValueError(f"{tag}: unknown shared section sections.{section}")
        keys |= set(_NAME.findall(_read(tag, SECTION_FILES[section], src)))

    # Controls named in the config, and those imported from ../controls.
    keys |= set(_NAME.findall(config)) | set(_STRING_ROW.findall(config))
    imported = re.search(r"import\s*\{([^}]*)\}\s*from\s*'\.\./controls'", panel)
    for ident in re.findall(r"\w+", imported.group(1) if imported else ""):
        if re.search(rf"\b{ident}\b", config) and ident in control_defs:
            keys |= _names_in(control_defs[ident], control_defs, {ident})
    return sorted(keys)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 if the contract JSON differs")
    mode.add_argument("--write", action="store_true", help="update the contract JSON")
    ap.add_argument("--src", type=Path, default=None, help="read DIR/<tag>/<path> instead of fetching")
    args = ap.parse_args(argv)

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    stale = []
    for tag in TAGS:
        keys = extract(tag, args.src)
        if contract[tag].get("mixed_timeseries") != keys:
            stale.append(tag)
        contract[tag]["mixed_timeseries"] = keys
        if not (args.check or args.write):
            print(f"{tag} ({len(keys)}): {' '.join(keys)}")
    if args.write:
        CONTRACT.write_text(json.dumps(contract, indent=1) + "\n", encoding="utf-8")
        print(f"wrote mixed_timeseries for {', '.join(TAGS)} into {CONTRACT}")
    if args.check and stale:
        print(f"mixed_timeseries differs from the source at: {', '.join(stale)}; run with --write")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
