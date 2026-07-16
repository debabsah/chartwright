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


AUDIENCES: dict[str, Params] = {
    "executive": Params("executive", fold_units=22, max_row_charts=3, kpi_row_min=3,
                        kpi_row_max=5, kpi_height=5, min_axis_height=8,
                        table_visible_ratio=0.5, vbar_max_categories=6,
                        pie_max_slices=5, series_max=5),
    "analytical": Params("analytical", fold_units=66, max_row_charts=4, kpi_row_min=2,
                         kpi_row_max=6, kpi_height=4, min_axis_height=6,
                         table_visible_ratio=0.25, vbar_max_categories=8,
                         pie_max_slices=7, series_max=10),
    "operational": Params("operational", fold_units=26, max_row_charts=5, kpi_row_min=2,
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


def load_overlay(path: Path | None = None) -> Overlay:
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
    return Overlay(**{k: v for k, v in data.items() if k in known})


def params_for(audience: str, overlay: Overlay | None = None) -> Params:
    if audience not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}; one of {sorted(AUDIENCES)}")
    p = AUDIENCES[audience]
    overlay = overlay or Overlay()
    updates: dict = {}
    valid = {f.name for f in fields(Params)} - {"audience"}
    for source in (overlay.params, overlay.audiences.get(audience) or {}):
        bad = sorted(set(source) - valid)
        if bad:
            raise ValueError(f"design overlay: unknown parameters {bad} (known: {sorted(valid)})")
        updates.update(source)
    if overlay.recommended_heights:
        updates["recommended_heights"] = {
            **p.recommended_heights, **overlay.recommended_heights,
            **(updates.get("recommended_heights") or {}),
        }
    return replace(p, **updates) if updates else p
