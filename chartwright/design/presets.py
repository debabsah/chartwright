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
    # Min visible rows / row_limit before size.table-window warns on an unpaged table.
    # 1 everywhere: smoke warns after apply when any row the data fills row_limit
    # with hides behind the grid's inner scrollbar, so at 0.5 advise passed a
    # 25-row table at 13 units that smoke then said needed 21. Still a knob
    # (design.yaml, per audience) for tables that rarely reach their row_limit; the
    # rule's target height is every row whatever it is set to.
    table_visible_ratio: float
    vbar_max_categories: int
    pie_max_slices: int
    series_max: int              # lines per timeseries
    max_filter_selects: int = 6  # select pickers in the native filter bar
    # Thresholds of the design defaults (default.* fills, docs/DESIGN-BRAIN.md sec.16),
    # the same for every audience until one is shown to differ. The value-label pair
    # was checked against rendered Superset 4.1.4, 5.0.0 and 6.1.0 (DESIGN-BRAIN.md,
    # "Calibration"); search_min_rows and page_min_rows are usability judgement.
    search_min_rows: int = 20          # raw table: row_limit above this gets a search box
    value_label_max_bars: int = 12     # bar: values on the bars up to this many bars
    # ... on a vertical bar at least this many twelfths wide: 12 labels like '12,345'
    # overlapped at 4/12 on every release and cleared each other by 12+ px at 6/12.
    value_label_min_width: int = 6
    page_min_rows: int = 3             # table: a smaller page than this isn't worth paging
    # '%d %b' names a single date only within 365 days: a 366-day span without a
    # 29 February starts and ends on the same day and month.
    day_label_max_span_days: int = 365
    # chart type -> height (spec units); calibrate/overlay feed this,
    # size-rule autofixes target it.
    recommended_heights: dict[str, float] = field(default_factory=dict)


# max_row_charts stays <= floor(12 / 3): the 3/12 minimum readable width is a
# hard floor (size.min-width errors below it), so a preset must never invite a
# row the rulebook's own error branch condemns.
AUDIENCES: dict[str, Params] = {
    "executive": Params("executive", fold_units=22, max_row_charts=3, kpi_row_min=2,
                        kpi_row_max=5, kpi_height=5, min_axis_height=8,
                        table_visible_ratio=1.0, vbar_max_categories=6,
                        pie_max_slices=5, series_max=5, max_filter_selects=5),
    "analytical": Params("analytical", fold_units=66, max_row_charts=4, kpi_row_min=2,
                         kpi_row_max=6, kpi_height=4, min_axis_height=6,
                         table_visible_ratio=1.0, vbar_max_categories=8,
                         pie_max_slices=7, series_max=10, max_filter_selects=6),
    "operational": Params("operational", fold_units=22, max_row_charts=4, kpi_row_min=2,
                          kpi_row_max=8, kpi_height=3, min_axis_height=5,
                          table_visible_ratio=1.0, vbar_max_categories=8,
                          pie_max_slices=7, series_max=8, max_filter_selects=7),
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
    severity: dict = field(default_factory=dict)          # rule id -> error|warn|info
    # Where it was read from (not a design.yaml key): reported in every advice
    # payload, so a run that an overlay changed says which file changed it.
    source: Path | None = None

    @property
    def active(self) -> bool:
        """Loaded from a file, or carrying any setting: advice says so when it is."""
        return self.source is not None or any(
            getattr(self, f.name) for f in fields(self) if f.name != "source")

    def param_names(self, audience: str) -> list[str]:
        """The parameters this overlay sets for one audience, by name."""
        names = set(self.params) | set(self.audiences.get(audience) or {})
        if self.recommended_heights:
            names.add("recommended_heights")
        return sorted(names)


# Overlay keys a design.yaml may hold (`source` is where it was read from).
OVERLAY_KEYS = frozenset(f.name for f in fields(Overlay)) - {"source"}


def design_dir() -> Path:
    custom = os.environ.get("CHARTWRIGHT_DESIGN_DIR")
    return Path(custom) if custom else Path.home() / ".config" / "chartwright"


def overlay_path() -> Path:
    return design_dir() / "design.yaml"


# The parameters a design.yaml or a standards file may set (`audience` names the
# preset; it is not a parameter).
PARAM_NAMES = frozenset(f.name for f in fields(Params)) - {"audience"}


def _check_heights(rec, where: str, what: str = "design overlay") -> dict:
    """recommended_heights must be {chart type: 1..100}; the fix loop targets
    these values, so a bad entry would corrupt specs and blame the user."""
    if not isinstance(rec, dict):
        raise ValueError(f"{what}: {where} must be a mapping of chart type -> height")
    for t, h in rec.items():
        if not isinstance(h, (int, float)) or isinstance(h, bool) or not 1 <= h <= 100:
            raise ValueError(
                f"{what}: {where}[{t!r}] must be a number in 1..100, got {h!r}")
    return rec


def _check_param_block(block, where: str, what: str = "design overlay") -> dict:
    """`what` names the file in an error: the design overlay, or a standards file."""
    if not isinstance(block, dict):
        raise ValueError(f"{what}: {where} must be a mapping")
    bad = sorted(set(block) - PARAM_NAMES)
    if bad:
        raise ValueError(f"{what}: {where}: unknown parameters {bad} (known: {sorted(PARAM_NAMES)})")
    for k, v in block.items():
        if k == "recommended_heights":
            _check_heights(v, f"{where}.recommended_heights", what)
        elif not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"{what}: {where}.{k} must be a number, got {v!r}")
    return block


def load_overlay(path: Path | None = None) -> Overlay:
    """Parse and VALIDATE design.yaml. The overlay is an org-wide, hand-edited
    trust boundary: every value is checked here, once, with a typed error --
    never a TypeError three modules later."""
    p = path or overlay_path()
    if not p.exists():
        return Overlay()
    where = p.as_posix()   # forward slashes in every message, on every platform
    import yaml

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"design overlay {where} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"design overlay {where} must be a YAML mapping")
    unknown = sorted(set(data) - OVERLAY_KEYS)
    if unknown:
        raise ValueError(f"design overlay {where}: unknown keys {unknown} (known: {sorted(OVERLAY_KEYS)})")

    ov = Overlay(**{k: v for k, v in data.items() if k in OVERLAY_KEYS}, source=p)
    if not isinstance(ov.disable, list) or not all(isinstance(x, str) for x in ov.disable):
        raise ValueError(f"design overlay {where}: disable must be a list of rule-id strings")
    if not isinstance(ov.brief_extra, str):
        raise ValueError(f"design overlay {where}: brief_extra must be a string")
    _check_param_block(ov.params, "params")
    _check_heights(ov.recommended_heights, "recommended_heights")
    if not isinstance(ov.audiences, dict):
        raise ValueError(f"design overlay {where}: audiences must be a mapping")
    bad_aud = sorted(set(ov.audiences) - set(AUDIENCES))
    if bad_aud:
        raise ValueError(
            f"design overlay {where}: unknown audiences {bad_aud} (known: {sorted(AUDIENCES)})")
    for aud, block in ov.audiences.items():
        _check_param_block(block, f"audiences.{aud}")
    if not isinstance(ov.severity, dict):
        raise ValueError(f"design overlay {where}: severity must be a mapping of rule id -> level")
    from .model import known_rule_ids  # function-level: no import cycle

    known = known_rule_ids()
    for rule_id, level in ov.severity.items():
        if rule_id not in known:
            raise ValueError(f"design overlay {where}: severity for unknown rule {rule_id!r}")
        if level not in ("error", "warn", "info"):
            raise ValueError(
                f"design overlay {where}: severity[{rule_id!r}] must be error|warn|info, got {level!r}")
    return ov


def params_for(audience: str, overlay: Overlay | None = None, standard=None) -> Params:
    """One audience's parameters. Each layer overrides the one before it, parameter by
    parameter: the audience preset, then the standard's `params` and its block for this
    audience (design/standards.py), then the overlay's `params` and its block for this
    audience. The overlay never sets a parameter the standard locks."""
    if audience not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}; one of {sorted(AUDIENCES)}")
    p = AUDIENCES[audience]
    overlay = overlay or Overlay()
    per_audience = _check_param_block(overlay.audiences.get(audience) or {}, f"audiences.{audience}")
    general = _check_param_block(overlay.params, "params")
    top_heights = _check_heights(overlay.recommended_heights, "recommended_heights")
    std_general: dict = {}
    std_audience: dict = {}
    if standard is not None:
        std_general = standard.params
        std_audience = standard.audiences.get(audience) or {}
        locked = standard.locked_params
        general = {k: v for k, v in general.items() if k not in locked}
        per_audience = {k: v for k, v in per_audience.items() if k not in locked}
        if "recommended_heights" in locked:
            top_heights = {}
    updates: dict = {}
    for block in (std_general, std_audience, general, per_audience):
        updates.update(block)
    # recommended_heights MERGES per key across every layer (preset, standard,
    # standard per-audience, overlay top-level, overlay params, overlay per-audience)
    # -- a wholesale replace would silently drop org-wide calibration on any audience
    # that tunes a single type.
    rec = {
        **p.recommended_heights,
        **(std_general.get("recommended_heights") or {}),
        **(std_audience.get("recommended_heights") or {}),
        **top_heights,
        **(general.get("recommended_heights") or {}),
        **(per_audience.get("recommended_heights") or {}),
    }
    if rec:
        updates["recommended_heights"] = rec
    return replace(p, **updates) if updates else p
