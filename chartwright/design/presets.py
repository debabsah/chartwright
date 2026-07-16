"""Audience presets + the house-style overlay.

Rules never test the audience name, only parameters: adding an audience is
adding a row here. The optional overlay file (~/.config/chartwright/design.yaml,
or $CHARTWRIGHT_DESIGN_DIR/design.yaml) lets an org tune parameters, disable
rules, append house guidance to the brief, and carry heights learned by
`chartwright calibrate` -- without forking the rulebook.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

DEFAULT_AUDIENCE = "analytical"


@dataclass(frozen=True)
class Params:
    audience: str
    fold_units: int              # height budget per tab (1 unit = 40 px)
    max_row_charts: int          # axis charts per band
    kpi_row_min: int
    kpi_row_max: int
    kpi_height: int
    min_axis_height: int
    table_visible_ratio: float   # min visible rows / row_limit
    vbar_max_categories: int
    pie_max_slices: int
    series_max: int              # lines per timeseries
    # chart type -> height (spec units); calibrate/overlay feed this,
    # size-rule autofixes target it.
    recommended_heights: dict[str, float] = field(default_factory=dict)


# max_row_charts stays <= floor(12 / 3): the 3/12 minimum readable width is a
# hard floor (size.min-width errors below it), so a preset must never invite a
# row the rulebook's own error branch condemns.
AUDIENCES: dict[str, Params] = {
    "executive": Params("executive", fold_units=22, max_row_charts=3, kpi_row_min=2,
                        kpi_row_max=5, kpi_height=5, min_axis_height=8,
                        table_visible_ratio=0.5, vbar_max_categories=6,
                        pie_max_slices=5, series_max=5),
    "analytical": Params("analytical", fold_units=66, max_row_charts=4, kpi_row_min=2,
                         kpi_row_max=6, kpi_height=4, min_axis_height=6,
                         table_visible_ratio=0.25, vbar_max_categories=8,
                         pie_max_slices=7, series_max=10),
    "operational": Params("operational", fold_units=22, max_row_charts=4, kpi_row_min=2,
                          kpi_row_max=8, kpi_height=3, min_axis_height=5,
                          table_visible_ratio=0.25, vbar_max_categories=8,
                          pie_max_slices=7, series_max=8),
}
AUDIENCE_NAMES = tuple(AUDIENCES)


@dataclass
class Overlay:
    """Parsed design.yaml. Empty overlay == no file == today's behavior."""

    params: dict = field(default_factory=dict)            # applies to every audience
    audiences: dict = field(default_factory=dict)         # audience -> param dict
    disable: list[str] = field(default_factory=list)      # rule ids to suppress
    brief_extra: str = ""                                 # appended to the brief
    recommended_heights: dict = field(default_factory=dict)  # chart type -> units


def design_dir() -> Path:
    custom = os.environ.get("CHARTWRIGHT_DESIGN_DIR")
    return Path(custom) if custom else Path.home() / ".config" / "chartwright"


def overlay_path() -> Path:
    return design_dir() / "design.yaml"


def _check_heights(rec, where: str) -> dict:
    """recommended_heights must be {chart type: 1..100}; the fix loop targets
    these values, so a bad entry would corrupt specs and blame the user."""
    if not isinstance(rec, dict):
        raise ValueError(f"design overlay: {where} must be a mapping of chart type -> height")
    for t, h in rec.items():
        if not isinstance(h, (int, float)) or isinstance(h, bool) or not 1 <= h <= 100:
            raise ValueError(
                f"design overlay: {where}[{t!r}] must be a number in 1..100, got {h!r}")
    return rec


def _check_param_block(block, where: str) -> dict:
    if not isinstance(block, dict):
        raise ValueError(f"design overlay: {where} must be a mapping")
    valid = {f.name for f in fields(Params)} - {"audience"}
    bad = sorted(set(block) - valid)
    if bad:
        raise ValueError(f"design overlay: {where}: unknown parameters {bad} (known: {sorted(valid)})")
    for k, v in block.items():
        if k == "recommended_heights":
            _check_heights(v, f"{where}.recommended_heights")
        elif not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"design overlay: {where}.{k} must be a number, got {v!r}")
    return block


def load_overlay(path: Path | None = None) -> Overlay:
    """Parse and VALIDATE design.yaml. The overlay is an org-wide, hand-edited
    trust boundary: every value is checked here, once, with a typed error --
    never a TypeError three modules later."""
    p = path or overlay_path()
    if not p.exists():
        return Overlay()
    import yaml

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"design overlay {p} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"design overlay {p} must be a YAML mapping")
    known = {f.name for f in fields(Overlay)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"design overlay {p}: unknown keys {unknown} (known: {sorted(known)})")

    ov = Overlay(**{k: v for k, v in data.items() if k in known})
    if not isinstance(ov.disable, list) or not all(isinstance(x, str) for x in ov.disable):
        raise ValueError(f"design overlay {p}: disable must be a list of rule-id strings")
    if not isinstance(ov.brief_extra, str):
        raise ValueError(f"design overlay {p}: brief_extra must be a string")
    _check_param_block(ov.params, "params")
    _check_heights(ov.recommended_heights, "recommended_heights")
    if not isinstance(ov.audiences, dict):
        raise ValueError(f"design overlay {p}: audiences must be a mapping")
    bad_aud = sorted(set(ov.audiences) - set(AUDIENCES))
    if bad_aud:
        raise ValueError(
            f"design overlay {p}: unknown audiences {bad_aud} (known: {sorted(AUDIENCES)})")
    for aud, block in ov.audiences.items():
        _check_param_block(block, f"audiences.{aud}")
    return ov


def params_for(audience: str, overlay: Overlay | None = None) -> Params:
    if audience not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}; one of {sorted(AUDIENCES)}")
    p = AUDIENCES[audience]
    overlay = overlay or Overlay()
    per_audience = _check_param_block(overlay.audiences.get(audience) or {}, f"audiences.{audience}")
    updates: dict = {}
    updates.update(_check_param_block(overlay.params, "params"))
    updates.update(per_audience)
    # recommended_heights MERGES per key across all four layers (preset,
    # overlay top-level, overlay params, overlay per-audience) -- a wholesale
    # replace would silently drop org-wide calibration on any audience that
    # tunes a single type.
    rec = {
        **p.recommended_heights,
        **_check_heights(overlay.recommended_heights, "recommended_heights"),
        **(overlay.params.get("recommended_heights") or {}),
        **(per_audience.get("recommended_heights") or {}),
    }
    if rec:
        updates["recommended_heights"] = rec
    return replace(p, **updates) if updates else p
