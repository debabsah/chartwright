"""Extract the entries of tools/contracts/params-contract.json for chart plugins whose
control panel is one config object, from the plugin's own source at each supported
Superset tag. The Mixed Chart, whose panel builds its sections twice, has its own
extractor (tools/extract_mixed_contract.py); this one reads the plain panels:

- every control the config names, by string (`['metric']`, or a string after an
  object in the same row) or by `name:`;
- shared sections it pulls in (`sections.titleControls`);
- controls imported from `../controls` (the waterfall's `showValueControl`).

    python tools/extract_panel_contract.py            # print each tag's key set
    python tools/extract_panel_contract.py --check    # exit 1 if the JSON differs
    python tools/extract_panel_contract.py --write    # update the JSON's entries

Needs network access (raw.githubusercontent.com); `--src DIR` reads
DIR/<tag>/<path> instead, e.g. a cache of earlier downloads.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_mixed_contract import (  # noqa: E402
    _NAME, _STRING_ROW, CONTRACT, CONTROLS, EC, SECTION_FILES, TAGS, _bodies, _names_in, _read,
)

# viz_type -> its control panel.
PANELS = {
    "waterfall": f"{EC}/Waterfall/controlPanel.tsx",
}
# A control named by string after an object in its row: [{ name: 'time_grain_sqla', ...},
# 'temporal_columns_lookup'].
_TRAILING_STRING = re.compile(r"\}\s*,\s*'(\w+)'\s*,?\s*\]")


def source_paths(viz: str) -> dict[str, str]:
    """Every source file read for one plugin, by role (the tests build a --src tree)."""
    return {"panel": PANELS[viz], "controls": CONTROLS, **SECTION_FILES}


def extract(viz: str, tag: str, src: Path | None = None) -> list[str]:
    panel = _read(tag, PANELS[viz], src)
    config = _bodies(panel)["config"]
    keys = (set(_NAME.findall(config)) | set(_STRING_ROW.findall(config))
            | set(_TRAILING_STRING.findall(config)))
    for section in set(re.findall(r"sections\.(\w+)", config)):
        if section not in SECTION_FILES:
            raise ValueError(f"{viz} {tag}: unknown shared section sections.{section}")
        keys |= set(_NAME.findall(_read(tag, SECTION_FILES[section], src)))
    imported = re.search(r"import\s*\{([^}]*)\}\s*from\s*'\.\./controls'", panel)
    if imported:
        control_defs = _bodies(_read(tag, CONTROLS, src))
        for ident in re.findall(r"\w+", imported.group(1)):
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
    for viz in PANELS:
        for tag in TAGS:
            keys = extract(viz, tag, args.src)
            if contract[tag].get(viz) != keys:
                stale.append(f"{viz} at {tag}")
            contract[tag][viz] = keys
            if not (args.check or args.write):
                print(f"{viz} {tag} ({len(keys)}): {' '.join(keys)}")
    if args.write:
        CONTRACT.write_text(json.dumps(contract, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {', '.join(PANELS)} for {', '.join(TAGS)} into {CONTRACT.as_posix()}")
    if args.check and stale:
        print(f"the contract differs from the source for: {', '.join(stale)}; run with --write")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
