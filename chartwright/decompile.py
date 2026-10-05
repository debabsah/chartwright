"""Decompiler: Superset export bundle -> spec (+ explicit lossiness report).

Turns any existing dashboard into a diffable, editable spec. Decompilation is
honest about loss: everything outside the spec surface is NAMED in the loss
report, never silently dropped-and-forgotten.
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from dataclasses import asdict, dataclass, field
from typing import Callable, get_args

import yaml

from .compiler import (
    BACKGROUND, COLUMN_CONFIG_KEYS, CONTRIBUTION_VALUES, FOOTER_PREFIX, HEADER_PREFIX, HEADER_SIZE,
    LEGEND_TYPES, ROW_UNITS_PER_SPEC_UNIT, SDC_BAR_MARKER, STACK_VALUES, VIZ_TYPE,
)
from .spec import (
    ADHOC_AGGREGATES, DEPENDENCY_PARENT_TYPES, FORMAT_COLOR_HEX, FORMAT_TEXT_HEX, FUNNEL_LABEL_TYPES,
    HEATMAP_DEFAULT_SCHEME, HEX_COLOUR_RE, PIVOT_ORDER, TREND_DEFAULT_HEX, FilterOp, LabelType,
    PivotAggregate, SequentialScheme, metric_label, row_items,
)

REVERSE_VIZ = {v: k for k, v in VIZ_TYPE.items() if k != "bar"}  # echarts_timeseries_bar -> timeseries_bar
_FILTER_OPS = set(get_args(FilterOp))

# Cosmetic / behavioral params we knowingly discard without a loss entry.
_IGNORABLE = {
    "datasource", "viz_type", "adhoc_filters", "extra_form_data", "dashboards",
    "color_scheme", "legendType", "legendOrientation", "legendMargin", "show_legend",
    "rich_tooltip", "annotation_layers", "comparison_type", "forecastEnabled",
    "forecastInterval", "forecastPeriods", "forecastSeasonalityDaily",
    "forecastSeasonalityWeekly", "forecastSeasonalityYearly", "truncateXAxis",
    "truncate_metric", "only_total", "order_desc", "show_empty_columns",
    "sort_series_type", "markerSize", "tooltipTimeFormat", "x_axis_sort_asc",
    "x_axis_sort_series", "x_axis_sort_series_ascending", "x_axis_time_format",
    "x_axis_title_margin", "y_axis_bounds", "y_axis_format", "y_axis_title_margin",
    "y_axis_title_position", "sort_by_metric", "show_labels_threshold",
    "server_page_length", "query_mode", "time_range", "time_grain_sqla", "x_axis",
    "granularity_sqla", "x_axis_sort", "orientation",
    "metric", "metrics", "groupby", "row_limit", "all_columns", "subheader",
    "label_colors", "date_format", "outerRadius", "innerRadius", "donut",
    "show_labels", "labels_outside", "label_type", "number_format", "cache_timeout",
    "order_by_cols", "table_timestamp_format", "show_cell_bars", "include_search",
    "currency_format", "opacity", "seriesType", "show_trend_line",
    "start_y_axis_at_zero", "rolling_type", "header_font_size", "subheader_font_size",
    "time_format", "color_picker", "groupbyRows", "groupbyColumns",
    "aggregateFunction", "metricsLayout", "rowOrder", "colOrder", "valueFormat",
    "temporal_columns_lookup", "normalize_across", "legend_type",
    "linear_color_scheme", "sort_x_axis", "sort_y_axis", "bottom_margin",
    "left_margin", "show_percentage", "show_values", "value_bounds",
    "xscale_interval", "yscale_interval", "column", "bins", "normalize",
    "show_value", "slice_id", "url_params", "percent_calculation_type",
    "show_tooltip_labels", "tooltip_label_type", SDC_BAR_MARKER,
}

# Of the keys above, those a user changes from the value an untouched chart stores:
# that value (any of these, across 4.1.4, 5.0.0 and 6.1.0) is no choice to lose, any
# other is, and decompile names it instead of dropping it silently (triage I). Each is
# the control's own default in the plugin source at all three tags
# (plugin-chart-echarts/src/controls.tsx, Timeseries/constants.ts DEFAULT_FORM_DATA,
# superset-ui-chart-controls sections/forecastInterval.tsx FORECAST_DEFAULT_DATA,
# sections/advancedAnalytics.tsx, utils/series.ts DEFAULT_SORT_SERIES_DATA and
# DEFAULT_XAXIS_SORT_SERIES_DATA, constants.ts TITLE_MARGIN_OPTIONS and
# TITLE_POSITION_OPTIONS). A key a chart type maps into the spec is never checked here.
_STORED_DEFAULTS: dict[str, tuple] = {
    "legendType": ("scroll",), "legendOrientation": ("top",), "legendMargin": (None, ""),
    "show_legend": (True,), "rich_tooltip": (True,), "tooltipTimeFormat": ("smart_date",),
    "truncateXAxis": (True,), "markerSize": (6,), "opacity": (0.2,),
    "sort_series_type": ("sum",), "y_axis_bounds": ([None, None], [], None),
    "x_axis_title_margin": (0, 15), "y_axis_title_margin": (15, 30),
    "y_axis_title_position": ("Left",), "only_total": (True,), "comparison_type": ("values",),
    "rolling_type": (None, "None", ""), "forecastEnabled": (False, None),
    "order_desc": (True,), "truncate_metric": (True,), "show_empty_columns": (True,),
    "x_axis_sort_asc": (True,), "x_axis_sort_series": ("name",),
    "x_axis_sort_series_ascending": (True,), "show_labels_threshold": (5,),
    "sort_by_metric": (True,), "start_y_axis_at_zero": (True,),
    "time_format": ("smart_date",), "date_format": ("smart_date",),
    "table_timestamp_format": ("smart_date",),
}
# Keys that matter only when another is set: a changed value with no effect is no loss.
_STORED_DEFAULT_WHEN = {
    "only_total": lambda p: bool(p.get("show_value")) and bool(p.get("stack")),
    "comparison_type": lambda p: bool(p.get("time_compare")),
    "x_axis_title_margin": lambda p: bool(p.get("x_axis_title")),
    "y_axis_title_margin": lambda p: bool(p.get("y_axis_title")),
    "y_axis_title_position": lambda p: bool(p.get("y_axis_title")),
}

# Spec chart types with a color_scheme field (spec._ColorSchemeMixin).
_COLOR_SCHEME_TYPES = ("timeseries_line", "timeseries_bar", "timeseries_area", "timeseries_scatter",
                       "bar", "pie", "histogram", "funnel", "treemap", "mixed")
_REVERSE_HEADER_SIZE = {v: k for k, v in HEADER_SIZE.items()}
_REVERSE_OPACITY = {"opacityLow": "low", "opacityMedium": "medium", "opacityHigh": "high"}

# Charts with an x axis (spec._AxisChart) and the params their x-label fields map to.
_AXIS_TYPES = ("timeseries_line", "timeseries_bar", "timeseries_area", "timeseries_scatter",
               "bar", "mixed")
_X_LABEL_KEYS = {"x_axis_time_format", "xAxisLabelRotation", "force_max_interval",
                 "xAxisLabelInterval"}


def _x_labels_to_spec(p: dict, out: dict, losses: list, name: str) -> None:
    # Superset's own defaults (adaptive format, 0 degrees, auto spacing) map to nothing.
    if p.get("x_axis_time_format") not in (None, "", "smart_date"):
        out["x_label_format"] = p["x_axis_time_format"]
    rot = p.get("xAxisLabelRotation")
    if rot not in (None, "", 0, "0"):
        try:
            deg = int(rot)
        except (TypeError, ValueError):
            deg = None
        if deg is not None and -90 <= deg <= 90:
            out["x_label_rotation"] = deg
        else:
            losses.append(Loss(name, f"x-axis label rotation {rot!r} not representable; dropped"))
    # Each control works on one axis kind only, as the compiler emits them.
    if "time_grain_sqla" in p:
        every = bool(p.get("force_max_interval"))
    else:
        every = str(p.get("xAxisLabelInterval")) == "0"
    if every:
        out["x_label_every"] = True


_SERIES_DISPLAY_TYPES = ("timeseries_line", "timeseries_bar", "timeseries_area",
                         "timeseries_scatter", "bar")
_STACK_SPEC = {v: k for k, v in STACK_VALUES.items()}           # "Stack" -> True, ...
_STACK_ALLOWED = {"timeseries_bar": (True,), "bar": (True,),
                  "timeseries_area": (True, "stream", "expand")}  # others: True, "stream"
_CONTRIBUTION_SPEC = {v: k for k, v in CONTRIBUTION_VALUES.items()}
_PIVOT_ORDER_SPEC = {v: k for k, v in PIVOT_ORDER.items()}
_LABEL_TYPES = set(get_args(LabelType))
_PIVOT_AGGREGATES = set(get_args(PivotAggregate))
_SEQUENTIAL_SCHEMES = set(get_args(SequentialScheme))
_COLUMN_CONFIG_SPEC = {v: k for k, v in COLUMN_CONFIG_KEYS.items()}  # d3NumberFormat -> number_formats


def _number(v) -> float | int | None:
    """A stored bound or size as a number; None for null, "" or junk."""
    if isinstance(v, bool) or v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _set_bounds(p: dict, key: str, out: dict, suffix: str, log: bool, losses: list, name: str) -> None:
    bounds = p.get(key)
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        return
    lo, hi = _number(bounds[0]), _number(bounds[1])
    if lo is not None and hi is not None and lo >= hi:
        losses.append(Loss(name, f"{key} {list(bounds)}: the minimum is not below the maximum; dropped"))
        return
    if log and lo is not None and lo <= 0:
        lo = None  # a log axis ignores a floor at or below zero
    if lo is not None:
        out[f"y_axis_min{suffix}"] = lo
    if hi is not None:
        out[f"y_axis_max{suffix}"] = hi


def _axis_to_spec(p: dict, out: dict, losses: list, name: str, spec_type: str) -> set[str]:
    """Axis titles, bounds, truncation and log scale (all with an x axis), plus the mixed
    chart's secondary axis. Superset's defaults ('' titles, [null, null], false) map to
    nothing. Returns the params it read."""
    for key in ("x_axis_title", "y_axis_title"):
        if isinstance(p.get(key), str) and p[key].strip():
            out[key] = p[key]
    if p.get("truncateYAxis") is True:
        out["y_axis_truncate"] = True
    if p.get("logAxis") is True:
        out["y_axis_log"] = True
    _set_bounds(p, "y_axis_bounds", out, "", bool(p.get("logAxis")), losses, name)
    read = {"x_axis_title", "y_axis_title", "truncateYAxis", "logAxis", "y_axis_bounds"}
    if spec_type == "mixed":
        if isinstance(p.get("yAxisTitleSecondary"), str) and p["yAxisTitleSecondary"].strip():
            out["y_axis_title_secondary"] = p["yAxisTitleSecondary"]
        if p.get("logAxisSecondary") is True:
            out["y_axis_log_secondary"] = True
        _set_bounds(p, "y_axis_bounds_secondary", out, "_secondary",
                    bool(p.get("logAxisSecondary")), losses, name)
        read |= {"yAxisTitleSecondary", "logAxisSecondary", "y_axis_bounds_secondary"}
    return read


def _legend_to_spec(p: dict, out: dict, spec_type: str) -> set[str]:
    """show_legend false, and where a shown legend sits; the defaults (shown, top,
    scroll) map to nothing, and a hidden legend's placement does nothing."""
    if p.get("show_legend") is False:
        out["show_legend"] = False
        return {"show_legend", "legendOrientation", "legendType"}
    if p.get("legendOrientation") in ("bottom", "left", "right"):
        out["legend_position"] = p["legendOrientation"]
    if p.get("legendType") == "plain" and spec_type != "funnel":
        out["legend_type"] = "plain"
    return {"show_legend", "legendOrientation", "legendType"}


def _series_limit_to_spec(p: dict, sfx: str, target: dict, losses: list, name: str) -> None:
    """limit / timeseries_limit_metric / order_desc (query B: '_b'). A limit only
    does something with a groupby, so without one it is dropped like Superset ignores it."""
    limit = _number(p.get(f"limit{sfx}"))
    if not target.get("groupby") or not isinstance(limit, int) or limit < 1:
        return
    target["series_limit"] = limit
    ranked = p.get(f"timeseries_limit_metric{sfx}")
    if ranked:
        m = _metric_to_spec(ranked, losses, name)
        if m:
            target["series_limit_metric"] = m
    if p.get(f"order_desc{sfx}") is False:
        target["series_limit_ascending"] = True


def _series_display_to_spec(p: dict, out: dict, losses: list, name: str, spec_type: str) -> set[str]:
    if p.get("show_value") is True:
        out["show_value"] = True
    stack = p.get("stack")
    if stack:
        value = _STACK_SPEC.get(stack)
        if value is not None and value in _STACK_ALLOWED.get(spec_type, (True, "stream")):
            out["stack"] = value
        else:
            losses.append(Loss(name, f"stack {stack!r} not preserved (not available on {spec_type})"))
    if p.get("only_total") is False and out.get("show_value") and out.get("stack"):
        out["only_total"] = False
    if p.get("contributionMode") in _CONTRIBUTION_SPEC:
        out["contribution"] = _CONTRIBUTION_SPEC[p["contributionMode"]]
    _series_limit_to_spec(p, "", out, losses, name)
    return {"show_value", "stack", "only_total", "contributionMode", "limit",
            "timeseries_limit_metric", "order_desc"}


def _line_style_to_spec(p: dict, out: dict, spec_type: str) -> set[str]:
    """Markers, marker size, the area under a line and its opacity. Superset's own
    defaults (no markers, size 6, opacity 0.2) map to nothing."""
    markers = spec_type in ("timeseries_line", "timeseries_area") and p.get("markerEnabled") is True
    if markers:
        out["markers"] = True
    size = _number(p.get("markerSize"))
    if (markers or spec_type == "timeseries_scatter") and isinstance(size, int) \
            and 0 <= size <= 20 and size != 6:
        out["marker_size"] = size
    filled = spec_type == "timeseries_area" or (spec_type == "timeseries_line" and p.get("area") is True)
    if spec_type == "timeseries_line" and filled:
        out["area"] = True
    opacity = _number(p.get("opacity"))
    if filled and opacity is not None and 0 <= opacity <= 1 and opacity != 0.2:
        out["opacity"] = opacity
    return {"markerEnabled", "markerSize", "area", "opacity"}


def _rgb_to_spec(colour) -> str | None:
    """A color_picker {r, g, b} as the spec writes it: a named shade, a hex, or None
    for Superset's own default teal."""
    if not isinstance(colour, dict):
        return None
    try:
        hexed = "#{:02X}{:02X}{:02X}".format(*(int(colour[k]) for k in "rgb"))
    except (KeyError, TypeError, ValueError):
        return None
    if hexed == TREND_DEFAULT_HEX:
        return None
    return {v.upper(): k for k, v in FORMAT_TEXT_HEX.items()}.get(hexed, hexed)


def _annotations_to_spec(p: dict, out: dict, losses: list, name: str) -> None:
    """FORMULA annotation layers -> spec annotations; any other layer (events,
    intervals, other charts' series) is named as a loss."""
    found = []
    for layer in p.get("annotation_layers") or []:
        if not isinstance(layer, dict):
            continue
        label = layer.get("name") or "?"
        if layer.get("annotationType") != "FORMULA":
            losses.append(Loss(name, f"annotation layer {label!r} ({layer.get('annotationType')}) "
                                     "not preserved: only FORMULA layers are in the spec"))
            continue
        if layer.get("show") is False:
            losses.append(Loss(name, f"hidden annotation layer {label!r} not preserved"))
            continue
        raw = str(layer.get("value") if layer.get("value") is not None else "").strip()
        a: dict = {"name": label}
        try:
            number = float(raw)
            if not math.isfinite(number):
                losses.append(Loss(name, f"annotation layer {label!r} value {raw!r} not preserved"))
                continue
            a["value"] = _number(number)
        except ValueError:
            if not raw:
                losses.append(Loss(name, f"annotation layer {label!r} has no formula; dropped"))
                continue
            a["formula"] = raw
        color = layer.get("color")
        if isinstance(color, str) and HEX_COLOUR_RE.fullmatch(color):
            a["color"] = color.upper()
        style = layer.get("style") or "solid"
        if style in ("dashed", "dotted"):
            a["style"] = style
        elif style != "solid":
            losses.append(Loss(name, f"annotation {label!r} line style {style!r} not preserved (solid)"))
        width = layer.get("width")
        if isinstance(width, (int, float)) and 0 < width <= 20 and width != 1:
            a["width"] = _number(width)
        if layer.get("opacity") in _REVERSE_OPACITY:
            a["opacity"] = _REVERSE_OPACITY[layer["opacity"]]
        found.append(a)
    unique, seen = [], set()
    for a in found:
        if a["name"] in seen:
            losses.append(Loss(name, f"second annotation named {a['name']!r} not preserved"))
            continue
        seen.add(a["name"])
        unique.append(a)
    if unique:
        out["annotations"] = unique



def _echart_options_to_spec(p: dict, out: dict, losses: list, name: str, spec_type: str) -> None:
    # The compiler writes JSON; an xAxis part is regenerated from x_label_every.
    raw = p.get("echart_options")
    if not raw:
        return
    try:
        opts = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        losses.append(Loss(name, "echart_options not preserved (not JSON)"))
        return
    if out.get("x_label_every"):
        opts.pop("xAxis", None)
    y = opts.get("yAxis")
    if spec_type == "timeseries_line" and isinstance(y, dict) and set(y) == {"max"}:
        # How the tool wrote y_axis_max before it moved to y_axis_bounds.
        out.setdefault("y_axis_max", y["max"])
        opts.pop("yAxis")
    if opts:
        losses.append(Loss(name, f"echart_options not preserved: {sorted(opts)}"))


@dataclass
class Loss:
    where: str
    what: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DecompileResult:
    spec: dict
    losses: list[Loss] = field(default_factory=list)
    dataset_uuids: dict[str, str] = field(default_factory=dict)  # chart name -> dataset uuid
    # The live dashboard's owner ids (decompile_live only: exports carry no owners).
    owner_ids: list[int] | None = None
    chart_uuids: dict[str, str] = field(default_factory=dict)  # chart name -> chart uuid
    skipped_charts: list[str] = field(default_factory=list)  # charts the spec can't represent
    dashboard_uuid: str | None = None
    source_slug: str | None = None  # the live dashboard's own slug (None when it has none)
    chart_titles: list[str] = field(default_factory=list)  # every chart's title, skipped ones too

    def losses_json(self) -> list[dict]:
        return [loss.as_dict() for loss in self.losses]


DatasetLookup = Callable[[str], dict | None]
"""dataset_uuid -> {'database','schema','table'} or None."""


def _format_to_spec(cf: dict) -> dict | None:
    """Superset conditional_formatting entry -> FormatRule dict, None if outside surface."""
    text = cf.get("objectFormatting") == "TEXT_COLOR"
    palette = FORMAT_TEXT_HEX if text else FORMAT_COLOR_HEX
    scheme = cf.get("colorScheme") if isinstance(cf.get("colorScheme"), str) else ""
    # A named shade for this paint reads back as its name; any other #RRGGBB as itself.
    color = {v.upper(): k for k, v in palette.items()}.get(scheme.upper())
    if color is None and HEX_COLOUR_RE.fullmatch(scheme):
        color = scheme.upper()
    # '< x <' is Superset's range comparator; the bare "between" older
    # chartwright builds wrote (and Superset never matched) reads back the same.
    op = {"< x <": "between"}.get(cf.get("operator"), cf.get("operator"))
    if not color or not cf.get("column") or op not in ("<", ">", "=", "between"):
        return None
    if cf.get("toTextColor") or cf.get("objectFormatting") not in (None, "BACKGROUND_COLOR", "TEXT_COLOR"):
        return None  # legacy text flag / cell bars: outside the surface
    rule: dict = {"metric": cf["column"], "operator": op, "color": color}
    if text:
        rule["paint"] = "text"
    target_col = cf.get("columnFormatting") or ("ENTIRE_ROW" if cf.get("toAllRow") else None)
    if target_col:
        rule["apply_to"] = "row" if target_col == "ENTIRE_ROW" else target_col
    if op == "between":
        rule["target_left"] = cf.get("targetValueLeft")
        rule["target_right"] = cf.get("targetValueRight")
        if rule["target_left"] is None or rule["target_right"] is None:
            return None
    else:
        rule["target"] = cf.get("targetValue")
        if rule["target"] is None:
            return None
    return rule


def _rules_to_spec(p: dict, losses: list, name: str, labels: set[str],
                   table: bool) -> list[dict]:
    """A table's or pivot's conditional_formatting as FormatRule dicts. Superset
    keeps a rule after its column leaves the query; the spec rejects a rule on
    a label the chart doesn't have, so those are dropped and recorded."""
    rules = []
    for cf in p.get("conditional_formatting") or []:
        rule = _format_to_spec(cf if isinstance(cf, dict) else {})
        if rule is None:
            losses.append(Loss(name, f"conditional format not representable, dropped: {cf}"))
            continue
        target = rule.get("apply_to")
        if rule["metric"] not in labels:
            losses.append(Loss(name, f"conditional format on {rule['metric']!r}, which the chart "
                                     "no longer queries, dropped"))
        elif target is not None and not table:
            losses.append(Loss(name, f"pivot conditional format painting {target!r} not preserved, dropped"))
        elif target not in (None, "row") and target not in labels:
            losses.append(Loss(name, f"conditional format painting {target!r}, which the chart "
                                     "no longer queries, dropped"))
        else:
            rules.append(rule)
    return rules


def _order_by_col(order_by_cols) -> tuple[str | None, bool]:
    """Raw-table sort: order_by_cols holds ["column", ascending] pairs, stored
    JSON-encoded by the control but seen as plain lists in some exports.
    Returns (first column, ascending); (None, False) when there is no sort."""
    for entry in order_by_cols or []:
        if isinstance(entry, str):
            try:
                entry = json.loads(entry)
            except ValueError:
                continue
        if isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], str):
            return entry[0], bool(entry[1])
    return None, False


def _metric_to_spec(m, losses: list[Loss], chart: str) -> str | None:
    if isinstance(m, str):
        return m
    if isinstance(m, dict):
        if m.get("expressionType") == "SIMPLE":
            agg = m.get("aggregate")
            col = (m.get("column") or {}).get("column_name")
            if agg in ADHOC_AGGREGATES and col:
                if m.get("hasCustomLabel") and m.get("label"):
                    return f"{agg}({col}) AS {m['label']}"
                return f"{agg}({col})"
        if m.get("expressionType") == "SQL":
            sql = (m.get("sqlExpression") or "").strip()
            match = re.fullmatch(r"(SUM|AVG|COUNT|COUNT_DISTINCT|MIN|MAX)\(\s*(\*|\w+)\s*\)", sql, re.I)
            if match:
                base = f"{match.group(1).upper()}({match.group(2)})"
                if m.get("hasCustomLabel") and m.get("label"):
                    return f"{base} AS {m['label']}"
                return base
            if sql:
                # Any other custom SQL keeps its expression and the label Superset
                # shows (a default label is the SQL itself, sometimes shortened).
                label = (m.get("label") or "").strip() or sql
                return f"SQL({sql}) AS {label}"
        losses.append(Loss(chart, f"metric not representable, dropped: {m}"))
        return None
    losses.append(Loss(chart, f"unrecognized metric shape, dropped: {m!r}"))
    return None


def _filters_to_spec(params: dict, losses: list[Loss], chart: str) -> list[dict]:
    out = []
    for f in params.get("adhoc_filters") or []:
        if not isinstance(f, dict):
            continue
        if f.get("operator") == "TEMPORAL_RANGE":
            continue  # the time-range mechanism, not a data filter
        if f.get("expressionType") == "SIMPLE" and f.get("clause", "WHERE") == "WHERE":
            op = f.get("operator")
            if op in _FILTER_OPS:
                out.append({"column": f.get("subject"), "op": op, "comparator": f.get("comparator")})
                continue
        if (f.get("expressionType") == "SQL" and f.get("clause", "WHERE") == "WHERE"
                and (f.get("sqlExpression") or "").strip()):
            out.append({"sql": f["sqlExpression"].strip()})
            continue
        losses.append(Loss(chart, f"filter not representable, dropped: {f}"))
    return [
        f if "sql" in f else
        {"column": f["column"], "op": f["op"], **({} if f["op"] in ("IS NULL", "IS NOT NULL") else {"value": f["comparator"]})}
        for f in out
    ]


def _groupby_one(p: dict, losses: list[Loss], chart: str) -> str | None:
    gb = p.get("groupby") or []
    if isinstance(gb, str):
        return gb
    if len(gb) > 1:
        losses.append(Loss(chart, f"multiple groupby {gb}; kept first only"))
    return gb[0] if gb and isinstance(gb[0], str) else None


def _chart_to_spec(chart_yaml: dict, lookup: DatasetLookup, losses: list[Loss],
                   changed_defaults: dict | None = None) -> dict | None:
    """With `changed_defaults`, keys a user changed from Superset's stored default go
    there ({key: value}) for the caller to check against what apply writes back;
    without it, each becomes a loss here."""
    name = chart_yaml.get("slice_name") or "Unnamed"
    viz = chart_yaml.get("viz_type")
    p = chart_yaml.get("params") or {}
    spec_type = REVERSE_VIZ.get(viz)
    if viz == "echarts_timeseries_bar" and p.get(SDC_BAR_MARKER):
        spec_type = "bar"
    if viz == "histogram":  # pre-6.x name
        spec_type = "histogram"
    if spec_type is None:
        losses.append(Loss(name, f"viz_type {viz!r} outside spec surface; chart skipped"))
        return None
    ds = lookup(str(chart_yaml.get("dataset_uuid")))
    if ds is None:
        losses.append(Loss(name, f"dataset uuid {chart_yaml.get('dataset_uuid')} not resolvable; chart skipped"))
        return None
    out: dict = {"name": name, "type": spec_type, "dataset": ds}
    flt = _filters_to_spec(p, losses, name)
    if flt:
        out["filters"] = flt
    for key in ("description", "certified_by", "certification_details"):
        text = chart_yaml.get(key)
        if isinstance(text, str) and text.strip():
            out[key] = text
    if chart_yaml.get("certification_details") and not chart_yaml.get("certified_by"):
        out.pop("certification_details", None)
        losses.append(Loss(name, "certification_details without certified_by not preserved"))
    timeout = chart_yaml.get("cache_timeout")
    if isinstance(timeout, int) and timeout >= 1:
        out["cache_timeout"] = timeout
    elif timeout not in (None, 0):
        losses.append(Loss(name, f"cache_timeout {timeout!r} not preserved (Superset's default on re-apply)"))
    tags = _tags_to_spec(chart_yaml.get("tags"), losses, name)
    if tags:
        out["tags"] = tags
    if spec_type in _COLOR_SCHEME_TYPES and isinstance(p.get("color_scheme"), str) and p["color_scheme"]:
        out["color_scheme"] = p["color_scheme"]

    def metric_one(value) -> str | None:
        return _metric_to_spec(value, losses, name)

    def keep_row_limit() -> None:
        if p.get("row_limit"):
            out["row_limit"] = p["row_limit"]

    if spec_type == "big_number_total":
        m = metric_one(p.get("metric"))
        if m is None:
            return None
        out["metric"] = m
        # 6.0+ stores the subtitle as `subtitle` (BigNumberTotal controlPanel at 6.1.0);
        # 4.1.4 and 5.0.0 as `subheader`, which 6.1.0 still reads.
        text = p.get("subtitle") if isinstance(p.get("subtitle"), str) and p["subtitle"].strip() \
            else p.get("subheader")
        if text:
            out["subtitle"] = text
        if p.get("y_axis_format"):
            out["number_format"] = p["y_axis_format"]
    elif spec_type == "big_number_trend":
        m = metric_one(p.get("metric"))
        x = p.get("x_axis")
        if m is None or not x:
            losses.append(Loss(name, "big_number trend needs metric + x_axis; chart skipped"))
            return None
        out["metric"] = m
        out["time_column"] = x
        if p.get("time_grain_sqla"):
            out["time_grain"] = p["time_grain_sqla"]
        if p.get("y_axis_format"):
            out["number_format"] = p["y_axis_format"]
        lag = _number(p.get("compare_lag"))
        if isinstance(lag, int) and lag >= 1:
            out["compare_lag"] = lag
            if isinstance(p.get("compare_suffix"), str) and p["compare_suffix"].strip():
                out["compare_suffix"] = p["compare_suffix"]
        if isinstance(p.get("subtitle"), str) and p["subtitle"].strip():
            out["subtitle"] = p["subtitle"]
        colour = _rgb_to_spec(p.get("color_picker"))
        if colour:
            out["trend_color"] = colour
    elif spec_type in ("timeseries_line", "timeseries_bar", "timeseries_area", "timeseries_scatter"):
        ms = [metric_one(m) for m in (p.get("metrics") or [])]
        ms = [m for m in ms if m]
        if not ms:
            losses.append(Loss(name, "no representable metrics; chart skipped"))
            return None
        out["metrics"] = ms
        x = p.get("x_axis")
        if isinstance(x, dict):
            x = x.get("sqlExpression") or x.get("label")
        if not x:
            losses.append(Loss(name, "no x_axis; chart skipped"))
            return None
        out["time_column"] = x
        if p.get("time_grain_sqla"):
            out["time_grain"] = p["time_grain_sqla"]
        if p.get("y_axis_format") not in (None, "SMART_NUMBER"):
            out["number_format"] = p["y_axis_format"]
        gb = _groupby_one(p, losses, name)
        if gb:
            out["groupby"] = gb
        keep_row_limit()
    elif spec_type == "bar":
        ms = [m for m in (metric_one(m) for m in (p.get("metrics") or [])) if m]
        x = p.get("x_axis")
        if not ms or not x:
            losses.append(Loss(name, "bar needs metrics + x_axis; chart skipped"))
            return None
        out["metrics"] = ms
        out["x_column"] = x
        if p.get("orientation") == "horizontal":
            out["orientation"] = "horizontal"
        # Sorted by the category: on the x column (one series), or by name (several
        # series: x_axis_sort from 6.0.0, x_axis_sort_series at 4.1.4 and 5.0.0).
        several = bool(p.get("groupby")) or len(ms) > 1
        sort, stored_asc = p.get("x_axis_sort"), p.get("x_axis_sort_asc")
        if several and sort in (None, "") and p.get("x_axis_sort_series") == "name":
            sort, stored_asc = "name", p.get("x_axis_sort_series_ascending", True)
        if sort == x or (several and sort == "name"):
            # In reading order (the horizontal axis runs bottom-up).
            ascending = bool(stored_asc) != (p.get("orientation") == "horizontal")
            out["category_sort"] = "asc" if ascending else "desc"
        if p.get("y_axis_format") not in (None, "SMART_NUMBER"):
            out["number_format"] = p["y_axis_format"]
        gb = _groupby_one(p, losses, name)
        if gb:
            out["groupby"] = gb
        keep_row_limit()
    elif spec_type == "pie":
        m = metric_one(p.get("metric"))
        gb = _groupby_one(p, losses, name)
        if m is None or not gb:
            losses.append(Loss(name, "pie needs metric+groupby; chart skipped"))
            return None
        out["metric"] = m
        out["groupby"] = gb
        if p.get("donut"):
            out["donut"] = True
        label_type = p.get("label_type")
        if label_type in _LABEL_TYPES and label_type != "key_percent":
            out["label_type"] = label_type
        elif label_type not in (None, "key_percent"):
            losses.append(Loss(name, f"pie label_type {label_type!r} not preserved (key_percent on re-apply)"))
        if p.get("number_format") not in (None, "", "SMART_NUMBER"):
            out["number_format"] = p["number_format"]
        if p.get("show_total") is True:
            out["show_total"] = True
        if p.get("labels_outside") is False:
            out["labels_outside"] = False
        keep_row_limit()
    elif spec_type == "table":
        if p.get("query_mode") == "raw" or p.get("all_columns"):
            out["columns"] = p.get("all_columns") or []
            if not out["columns"]:
                losses.append(Loss(name, "raw table with no columns; chart skipped"))
                return None
            col, asc = _order_by_col(p.get("order_by_cols"))
            if col:
                out["sort_by"] = col
        else:
            ms = [m for m in (metric_one(m) for m in (p.get("metrics") or [])) if m]
            out["metrics"] = ms or None
            out["groupby"] = [g for g in (p.get("groupby") or []) if isinstance(g, str)] or None
            if not out["metrics"] and not out["groupby"]:
                losses.append(Loss(name, "aggregate table with no metrics/groupby; chart skipped"))
                return None
            sort = p.get("timeseries_limit_metric") or p.get("series_limit_metric")
            if sort:
                s = metric_one(sort)
                if s:
                    out["sort_by"] = s
            asc = p.get("order_desc") is False
        if out.get("sort_by") and asc:
            out["sort_ascending"] = True
        labels = {metric_label(m) for m in out.get("metrics") or []}
        labels |= set(out.get("groupby") or []) | set(out.get("columns") or [])
        rules = _rules_to_spec(p, losses, name, labels, table=True)
        if rules:
            out["conditional_formatting"] = rules
        hidden: list = []
        per_label: dict[str, dict] = {spec_key: {} for spec_key in COLUMN_CONFIG_KEYS}
        for label, cfg in (p.get("column_config") or {}).items():
            cfg = cfg if isinstance(cfg, dict) else {}
            if label not in labels:
                # Superset keeps column_config entries after the column leaves
                # the query; they no longer do anything.
                if cfg:
                    losses.append(Loss(name, f"column_config for {label!r}, which the table "
                                             "no longer queries, dropped"))
                continue
            if cfg.get("visible") is False:
                hidden.append(label)
            if cfg.get("d3NumberFormat"):
                per_label["number_formats"][label] = cfg["d3NumberFormat"]
            if cfg.get("horizontalAlign") in ("left", "center", "right"):
                per_label["column_align"][label] = cfg["horizontalAlign"]
            width = _number(cfg.get("columnWidth"))
            if isinstance(width, int) and width >= 1:
                per_label["column_widths"][label] = width
            if isinstance(cfg.get("customColumnName"), str) and cfg["customColumnName"].strip():
                per_label["column_headers"][label] = cfg["customColumnName"]
            extra = sorted(k for k in cfg if k not in ("visible", *_COLUMN_CONFIG_SPEC))
            if extra:
                losses.append(Loss(name, f"column_config {label!r} settings not preserved: {extra}"))
        if hidden:
            out["hidden"] = hidden
        for spec_field, values in per_label.items():
            if values:
                out[spec_field] = values
        page = _number(p.get("page_length"))
        if isinstance(page, int) and page >= 0:
            out["page_length"] = page
        if p.get("show_totals") is True and out.get("metrics"):
            out["show_totals"] = True
        if p.get("include_search") is True:
            out["search_box"] = True
        if "show_cell_bars" in p:
            out["cell_bars"] = bool(p["show_cell_bars"])
        if p.get("table_timestamp_format") not in (None, "", "smart_date"):
            out["date_format"] = p["table_timestamp_format"]
        keep_row_limit()
    elif spec_type == "pivot_table":
        ms = [m for m in (metric_one(m) for m in (p.get("metrics") or [])) if m]
        rows = [c for c in (p.get("groupbyRows") or []) if isinstance(c, str)]
        cols = [c for c in (p.get("groupbyColumns") or []) if isinstance(c, str)]
        if not ms or not (rows or cols):
            losses.append(Loss(name, "pivot needs metrics + rows/columns; chart skipped"))
            return None
        out["metrics"] = ms
        if rows:
            out["rows"] = rows
        if cols:
            out["columns"] = cols
        aggregate = p.get("aggregateFunction") or "Sum"
        if aggregate in _PIVOT_AGGREGATES:
            if aggregate != "Sum":
                out["aggregate_function"] = aggregate
        else:
            losses.append(Loss(name, f"pivot aggregateFunction {aggregate!r} not preserved (Sum on re-apply)"))
        for key, spec_field in (("rowOrder", "row_order"), ("colOrder", "column_order")):
            order = _PIVOT_ORDER_SPEC.get(p.get(key) or "key_a_to_z")
            if order is None:
                losses.append(Loss(name, f"pivot {key} {p[key]!r} not preserved (a to z on re-apply)"))
            elif order != "a_to_z":
                out[spec_field] = order
        if p.get("metricsLayout") == "ROWS":
            out["metrics_layout"] = "rows"
        if p.get("rowSubTotals"):
            out["row_subtotals"] = True
        if p.get("transposePivot"):
            out["transpose"] = True
        if p.get("combineMetric"):
            out["combine_metric"] = True
        if p.get("rowTotals"):
            out["row_totals"] = True
        if p.get("colTotals"):
            out["column_totals"] = True
        if p.get("colSubTotals"):
            out["measure_totals"] = True
        if p.get("date_format"):
            out["date_format"] = p["date_format"]
        if p.get("valueFormat") not in (None, "", "SMART_NUMBER"):
            out["number_format"] = p["valueFormat"]
        rules = _rules_to_spec(p, losses, name, {metric_label(m) for m in ms}, table=False)
        if rules:
            out["conditional_formatting"] = rules
        keep_row_limit()
    elif spec_type == "heatmap":
        m = metric_one(p.get("metric"))
        x = p.get("x_axis")
        y = p.get("groupby") if isinstance(p.get("groupby"), str) else None
        if m is None or not x or not y:
            losses.append(Loss(name, "heatmap needs metric + x_axis + groupby; chart skipped"))
            return None
        out["metric"] = m
        out["x_column"] = x
        out["y_column"] = y
        if p.get("show_values") is True:
            out["show_values"] = True
        if p.get("show_percentage") is False:
            out["show_percentage"] = False
        if p.get("normalize_across") in ("x", "y"):
            out["normalize_across"] = p["normalize_across"]
        scheme = p.get("linear_color_scheme")
        if scheme in _SEQUENTIAL_SCHEMES and scheme != HEATMAP_DEFAULT_SCHEME:
            out["color_scheme"] = scheme
        elif scheme not in (None, "", HEATMAP_DEFAULT_SCHEME):
            losses.append(Loss(name, f"heatmap colour scheme {scheme!r} not preserved "
                                     f"({HEATMAP_DEFAULT_SCHEME} on re-apply)"))
        if p.get("y_axis_format") not in (None, "", "SMART_NUMBER"):
            out["number_format"] = p["y_axis_format"]
        if p.get("show_legend") is False:
            out["show_legend"] = False
        keep_row_limit()
    elif spec_type == "histogram":
        col = p.get("column")
        if not col:
            losses.append(Loss(name, "histogram without column; chart skipped"))
            return None
        out["column"] = col
        if p.get("bins"):
            out["bins"] = p["bins"]
        for key in ("x_axis_title", "y_axis_title"):
            if isinstance(p.get(key), str) and p[key].strip():
                out[key] = p[key]
        gb = _groupby_one(p, losses, name)
        if gb:
            out["groupby"] = gb
        keep_row_limit()
    elif spec_type == "funnel":
        m = metric_one(p.get("metric"))
        gb = _groupby_one(p, losses, name)
        if m is None or not gb:
            losses.append(Loss(name, "funnel needs metric + groupby; chart skipped"))
            return None
        out["metric"] = m
        out["groupby"] = gb
        index = _number(p.get("label_type"))
        if isinstance(index, int) and 0 <= index < len(FUNNEL_LABEL_TYPES):
            if index:
                out["label_type"] = FUNNEL_LABEL_TYPES[index]
        elif p.get("label_type") not in (None, ""):
            losses.append(Loss(name, f"funnel label_type {p['label_type']!r} not preserved (key on re-apply)"))
        if p.get("number_format") not in (None, "", "SMART_NUMBER"):
            out["number_format"] = p["number_format"]
        keep_row_limit()
    elif spec_type == "treemap":
        m = metric_one(p.get("metric"))
        gb = [g for g in (p.get("groupby") or []) if isinstance(g, str)]
        if m is None or not gb:
            losses.append(Loss(name, "treemap needs metric + groupby; chart skipped"))
            return None
        out["metric"] = m
        out["groupby"] = gb
        if p.get("label_type") in ("key", "value"):
            out["label_type"] = p["label_type"]
        if p.get("number_format") not in (None, "", "SMART_NUMBER"):
            out["number_format"] = p["number_format"]
        keep_row_limit()
    elif spec_type == "mixed":
        x = p.get("x_axis")
        if isinstance(x, dict):
            x = x.get("sqlExpression") or x.get("label")
        if not x:
            losses.append(Loss(name, "mixed chart without x_axis; chart skipped"))
            return None
        out["x_column"] = x
        for key, sfx, kind_key, axis_key in (("a", "", "seriesType", "yAxisIndex"),
                                             ("b", "_b", "seriesTypeB", "yAxisIndexB")):
            ms = [m for m in (metric_one(m) for m in (p.get(f"metrics{sfx}") or [])) if m]
            if not ms:
                losses.append(Loss(name, f"mixed chart query {key.upper()} has no representable metrics; chart skipped"))
                return None
            series: dict = {"metrics": ms}
            kind = p.get(kind_key) or "line"  # the plugin's own default series type
            if kind not in ("bar", "line"):
                losses.append(Loss(name, f"query {key.upper()} series type {kind!r} not preserved (line on re-apply)"))
                kind = "line"
            if kind != "bar":
                series["kind"] = kind
            if p.get(axis_key) == 1:
                series["axis"] = "secondary"
            gb = [g for g in (p.get(f"groupby{sfx}") or []) if isinstance(g, str)]
            if len(gb) > 1:
                losses.append(Loss(name, f"query {key.upper()}: multiple groupby {gb}; kept first only"))
            if gb:
                series["groupby"] = gb[0]
            disp = kind_key.replace("seriesType", "")  # "" for query A, "B" for query B
            if p.get(f"markerEnabled{disp}"):
                series["markers"] = True
            if p.get(f"show_value{disp}") is True:
                series["show_value"] = True
            if p.get(f"stack{disp}"):
                series["stack"] = True
            if p.get(f"only_total{disp}") is False and series.get("show_value") and series.get("stack"):
                series["only_total"] = False
            _series_limit_to_spec(p, sfx, series, losses, name)
            out[key] = series
        if _filters_to_spec({"adhoc_filters": p.get("adhoc_filters_b")}, [], name) != (out.get("filters") or []):
            losses.append(Loss(name, "query B filters differ from query A's; not preserved (both take the chart's filters)"))
        if p.get("time_grain_sqla"):
            out["time_grain"] = p["time_grain_sqla"]
        if p.get("y_axis_format") not in (None, "SMART_NUMBER"):
            out["number_format"] = p["y_axis_format"]
        if p.get("y_axis_format_secondary") not in (None, "SMART_NUMBER"):
            out["number_format_secondary"] = p["y_axis_format_secondary"]
        keep_row_limit()

    mapped_here = {"combineMetric", "conditional_formatting", "rowTotals", "colTotals", "colSubTotals"} if spec_type == "pivot_table" else (
        {"order_by_cols", "timeseries_limit_metric", "series_limit_metric",
         "conditional_formatting", "column_config"}
        if spec_type == "table" else set())
    if spec_type == "mixed":
        mapped_here = {"metrics_b", "groupby_b", "adhoc_filters_b", "row_limit_b", "seriesTypeB",
                       "yAxisIndex", "yAxisIndexB", "y_axis_format_secondary",
                       "markerEnabled", "markerEnabledB"}
        mapped_here |= {f"{k}{s}" for k in ("show_value", "stack", "only_total") for s in ("", "B")}
        mapped_here |= {f"{k}{s}" for k in ("limit", "timeseries_limit_metric", "order_desc")
                        for s in ("", "_b")}
    if p.get("time_range") not in (None, "", "No filter"):
        out["time_range"] = p["time_range"]
    if spec_type in _SERIES_DISPLAY_TYPES:
        mapped_here = mapped_here | _series_display_to_spec(p, out, losses, name, spec_type)
    if spec_type in ("timeseries_line", "timeseries_area", "timeseries_scatter"):
        mapped_here = mapped_here | _line_style_to_spec(p, out, spec_type)
    if spec_type in LEGEND_TYPES:
        mapped_here = mapped_here | _legend_to_spec(p, out, spec_type)
    if spec_type == "big_number_trend":
        mapped_here = mapped_here | {"compare_lag", "compare_suffix", "subtitle", "color_picker"}
    if spec_type == "big_number_total":
        mapped_here = mapped_here | {"subtitle"}
    if spec_type == "table":
        mapped_here = mapped_here | {"page_length", "show_totals", "include_search"}
    if spec_type == "pivot_table":
        mapped_here = mapped_here | {"rowSubTotals", "transposePivot"}
    if spec_type == "histogram":
        mapped_here = mapped_here | {"x_axis_title", "y_axis_title"}
    if spec_type == "pie":
        mapped_here = mapped_here | {"show_total"}
    if spec_type in _AXIS_TYPES:
        _x_labels_to_spec(p, out, losses, name)
        mapped_here = mapped_here | _axis_to_spec(p, out, losses, name, spec_type)
        _echart_options_to_spec(p, out, losses, name, spec_type)
        _annotations_to_spec(p, out, losses, name)
        mapped_here = mapped_here | _X_LABEL_KEYS | {"echart_options"}
    unmapped = sorted(k for k in p if k not in _IGNORABLE and k not in mapped_here)
    if unmapped:
        losses.append(Loss(name, f"params not preserved: {unmapped}"))
    changed = {k: p[k] for k in sorted(p)
               if k in _STORED_DEFAULTS and k not in mapped_here
               and p[k] not in _STORED_DEFAULTS[k]
               and _STORED_DEFAULT_WHEN.get(k, lambda _: True)(p)}
    if changed:
        if changed_defaults is None:
            losses.append(_defaults_loss(name, changed))
        else:
            changed_defaults["__last__"] = changed
    return out


def _defaults_loss(name: str, changed: dict) -> Loss:
    return Loss(name, f"settings not preserved (apply puts Superset's default back): "
                      f"{[f'{k}={v!r}' for k, v in changed.items()]}")


def _check_changed_defaults(spec: dict, pending: dict[str, dict], losses: list[Loss]) -> None:
    """Report each changed default apply would NOT write back. Compiling the spec just
    read (offline, stub ids) shows which ones the spec carries under its own fields,
    without a hand list of the keys each chart type maps; if it can't be compiled,
    every one is reported."""
    from .compiler import compile_bundle
    from .spec import load_spec as _load
    from .testing import stub_resolution

    compiled: dict[str, dict] = {}
    try:
        built = _load(spec)
        zf = zipfile.ZipFile(io.BytesIO(compile_bundle(built, stub_resolution(built))))
        for n in zf.namelist():
            if "/charts/" in n and n.endswith(".yaml"):
                cy = yaml.safe_load(zf.read(n)) or {}
                compiled[cy.get("slice_name")] = cy.get("params") or {}
    except Exception:  # noqa: BLE001 - an unloadable spec reports every change
        compiled = {}
    for name, changed in pending.items():
        params = compiled.get(name)
        lost = {k: v for k, v in changed.items() if params is None or params.get(k) != v}
        if lost:
            losses.append(_defaults_loss(name, lost))


def _tags_to_spec(tags, losses: list, where: str) -> list[str]:
    """Superset 6.1 exports a custom-tag name list (TAGGING_SYSTEM on)."""
    if not isinstance(tags, list):
        return []
    keep = []
    for t in tags:
        if isinstance(t, str) and t.strip() and ":" not in t and t.strip() not in keep:
            keep.append(t.strip())
        else:
            losses.append(Loss(where, f"tag {t!r} not preserved"))
    return keep


def _pre_filter_to_spec(nf: dict, f: dict, losses: list, where: str) -> None:
    pre = _filters_to_spec({"adhoc_filters": nf.get("adhoc_filters")}, losses, where)
    if pre:
        f["pre_filter"] = pre
    time_range = nf.get("time_range")
    if isinstance(time_range, str) and time_range and time_range != "No filter":
        if nf.get("granularity_sqla"):
            f["time_range"] = time_range
            f["time_column"] = nf["granularity_sqla"]
        else:
            losses.append(Loss(where, f"pre-filter time range {time_range!r} without a time column "
                                      "not preserved"))


def _native_filters_to_spec(
    metadata: dict, lookup: DatasetLookup, losses: list[Loss],
    filter_uuids: dict[str, str] | None = None,
) -> list[dict]:
    out = []
    configs = [nf for nf in metadata.get("native_filter_configuration") or [] if isinstance(nf, dict)]
    names_by_id = {nf.get("id"): nf.get("name") or nf.get("id") or "filter" for nf in configs}
    parent_ids: dict[str, list] = {}
    for nf in configs:
        name = nf.get("name") or nf.get("id") or "filter"
        ftype = nf.get("filterType")
        if nf.get("cascadeParentIds"):
            parent_ids[name] = list(nf["cascadeParentIds"])
        if ftype == "filter_select":
            targets = nf.get("targets") or []
            col = ((targets[0].get("column") or {}).get("name")) if targets else None
            ds_uuid = targets[0].get("datasetUuid") if targets else None
            ds = lookup(str(ds_uuid)) if ds_uuid else None
            if not col or ds is None:
                losses.append(Loss(f"filter:{name}", "select filter target not resolvable; dropped"))
                continue
            if filter_uuids is not None:
                filter_uuids[f"filter:{name}"] = str(ds_uuid)
            f: dict = {"type": "select", "name": name, "dataset": ds, "column": col}
            cv = nf.get("controlValues") or {}
            multi = cv.get("multiSelect", True)
            if multi is False:
                f["multi"] = False
            if cv.get("defaultToFirstItem"):
                f["default_to_first"] = True
            if cv.get("sortAscending") is False:
                f["sort_descending"] = True
            if cv.get("enableEmptyFilter"):
                f["required"] = True
            if cv.get("searchAllOptions"):
                f["search_all_options"] = True
            if cv.get("inverseSelection"):
                f["inverse_selection"] = True
            # 6.1.0 keeps it in controlValues, 4.1.4/5.0.0 at the top level.
            sort_metric = cv.get("sortMetric") or nf.get("sortMetric")
            if isinstance(sort_metric, str) and sort_metric:
                f["sort_metric"] = sort_metric
            _pre_filter_to_spec(nf, f, losses, f"filter:{name}")
            value = ((nf.get("defaultDataMask") or {}).get("filterState") or {}).get("value")
            # With "select first value" the stored value is just the first item
            # when the filter was saved; Superset picks it again on load, and
            # the spec takes one of default / default_to_first.
            if isinstance(value, list) and value and not f.get("default_to_first"):
                f["default"] = list(value)
            scoped = nf.get("sdc_scope_charts")
            if scoped:
                f["charts"] = list(scoped)
            elif (nf.get("scope") or {}).get("excluded"):
                losses.append(Loss(
                    f"filter:{name}",
                    "chart scope not preserved (live scopes are numeric slice ids; "
                    "re-declare `charts` by name in the spec)",
                ))
            out.append(f)
            if "default" in f or f.get("default_to_first"):
                continue  # default preserved; skip the default-loss check
        elif ftype == "filter_range":
            targets = nf.get("targets") or []
            col = ((targets[0].get("column") or {}).get("name")) if targets else None
            ds_uuid = targets[0].get("datasetUuid") if targets else None
            ds = lookup(str(ds_uuid)) if ds_uuid else None
            if not col or ds is None:
                losses.append(Loss(f"filter:{name}", "range filter target not resolvable; dropped"))
                continue
            if filter_uuids is not None:
                filter_uuids[f"filter:{name}"] = str(ds_uuid)
            f = {"type": "range", "name": name, "dataset": ds, "column": col}
            _pre_filter_to_spec(nf, f, losses, f"filter:{name}")
            value = ((nf.get("defaultDataMask") or {}).get("filterState") or {}).get("value")
            if isinstance(value, list) and len(value) == 2:
                if value[0] is not None:
                    f["ge"] = value[0]
                if value[1] is not None:
                    f["le"] = value[1]
            scoped = nf.get("sdc_scope_charts")
            if scoped:
                # Tool-born filters carry their name-based scope; the numeric
                # live scope is derived from it by apply's scope stage.
                f["charts"] = list(scoped)
            elif (nf.get("scope") or {}).get("excluded"):
                losses.append(Loss(
                    f"filter:{name}",
                    "chart scope not preserved (live scopes are numeric slice ids; "
                    "re-declare `charts` by name in the spec)",
                ))
            out.append(f)
            continue  # range preserves its default; skip the default-loss check
        elif ftype == "filter_time":
            f = {"type": "time_range", "name": name}
            _scope_to_spec(nf, f, losses, name)
            value = ((nf.get("defaultDataMask") or {}).get("filterState") or {}).get("value")
            if isinstance(value, str) and value:
                f["default"] = value
                out.append(f)
                continue  # default preserved; skip the default-loss check
            out.append(f)
        elif ftype in ("filter_timegrain", "filter_timecolumn"):
            kind = "time_grain" if ftype == "filter_timegrain" else "time_column"
            targets = nf.get("targets") or []
            ds_uuid = targets[0].get("datasetUuid") if targets and isinstance(targets[0], dict) else None
            ds = lookup(str(ds_uuid)) if ds_uuid else None
            if ds is None:
                losses.append(Loss(f"filter:{name}", f"{kind} filter dataset not resolvable; dropped"))
                continue
            if filter_uuids is not None:
                filter_uuids[f"filter:{name}"] = str(ds_uuid)
            f = {"type": kind, "name": name, "dataset": ds}
            if (nf.get("controlValues") or {}).get("enableEmptyFilter"):
                f["required"] = True
            _scope_to_spec(nf, f, losses, name)
            value = ((nf.get("defaultDataMask") or {}).get("filterState") or {}).get("value")
            if isinstance(value, list) and value and isinstance(value[0], str):
                f["default"] = value[0]
                out.append(f)
                continue  # default preserved; skip the default-loss check
            out.append(f)
        else:
            losses.append(Loss(f"filter:{name}", f"filterType {ftype!r} outside spec surface; dropped"))
            continue
        dm = nf.get("defaultDataMask") or {}
        if dm.get("filterState") or dm.get("extraFormData"):
            losses.append(Loss(f"filter:{name}", "default value not preserved"))
    # A filter scoped to some tabs (scope.rootPath other than the whole dashboard): the
    # spec scopes by chart name or not at all, so apply widens it (triage I).
    for nf in configs:
        root = (nf.get("scope") or {}).get("rootPath") or ["ROOT_ID"]
        nm = nf.get("name") or nf.get("id") or "filter"
        if root != ["ROOT_ID"] and any(f["name"] == nm for f in out):
            losses.append(Loss(f"filter:{nm}", f"scoped to tabs {root}; not preserved, apply "
                                               f"scopes it to the whole dashboard"))
    kept = {f["name"]: f["type"] for f in out}
    for f in out:
        nf = next((c for c in configs if (c.get("name") or c.get("id") or "filter") == f["name"]), {})
        text = nf.get("description")
        if isinstance(text, str) and text.strip():
            f["description"] = text
        deps = []
        for pid in parent_ids.get(f["name"], []):
            parent = names_by_id.get(pid)
            if (parent in kept and parent != f["name"] and f["type"] in ("select", "range")
                    and kept[parent] in DEPENDENCY_PARENT_TYPES):
                deps.append(parent)
            else:
                losses.append(Loss(f"filter:{f['name']}",
                                   f"dependency on {parent or pid!r} not preserved"))
        if deps:
            f["dependencies"] = deps
    # A dependency on a parent of a type that cannot be one, or a cycle, would
    # make the spec invalid; the spec validator names it, so leave it visible.
    return out


def _scope_to_spec(nf: dict, f: dict, losses: list, name: str) -> None:
    scoped = nf.get("sdc_scope_charts")
    if scoped:
        # Tool-born filters carry their name-based scope; the numeric
        # live scope is derived from it by apply's scope stage.
        f["charts"] = list(scoped)
    elif (nf.get("scope") or {}).get("excluded"):
        losses.append(Loss(
            f"filter:{name}",
            "chart scope not preserved (live scopes are numeric slice ids; "
            "re-declare `charts` by name in the spec)",
        ))


def _geo(meta: dict) -> dict:
    geo = {"width": meta.get("width"), "height": meta.get("height")}
    override = meta.get("sliceNameOverride")
    if isinstance(override, str) and override.strip() and override != meta.get("sliceName"):
        geo["display_name"] = override
    return geo


def _walk_rows(position: dict, children: list[str], kept_names: set[str],
               losses: list[Loss], geometry: dict[str, dict]) -> list:
    """Convert grid- or tab-level nodes into spec rows: ROWs (chart names +
    markdown blocks; a white background makes a {"row", "background"} entry),
    HEADERs and DIVIDERs."""
    rows: list = []

    def handle(children_ids: list[str], depth: int = 0) -> None:
        if depth > 10:
            return
        for cid in children_ids:
            node = position.get(cid)
            if not node:
                continue
            t = node.get("type")
            if t == "ROW":
                row: list = []
                for ch_id in node.get("children", []):
                    ch = position.get(ch_id) or {}
                    meta = ch.get("meta") or {}
                    if ch.get("type") == "CHART":
                        nm = meta.get("sliceName")
                        if nm and nm in kept_names:
                            row.append(nm)
                            geometry[nm] = _geo(meta)
                        elif nm:
                            losses.append(Loss("layout", f"chart {nm!r} in layout but not decompilable; removed from row"))
                    elif ch.get("type") == "MARKDOWN":
                        block: dict = {"markdown": meta.get("code") or ""}
                        if meta.get("width"):
                            block["width"] = max(1, min(12, int(meta["width"])))
                        if meta.get("height"):
                            h = int(meta["height"]) / ROW_UNITS_PER_SPEC_UNIT  # exact: text blocks take fifths
                            block["height"] = int(h) if h.is_integer() else round(h, 1)
                        if block["markdown"]:
                            row.append(block)
                        else:
                            losses.append(Loss("layout", "empty MARKDOWN dropped"))
                    elif ch.get("type") == "COLUMN":
                        losses.append(Loss("layout", "COLUMN (vertical stacking) flattened: children pulled up"))
                        handle(ch.get("children", []), depth + 1)
                    else:
                        losses.append(Loss("layout", f"{ch.get('type')} element dropped from a row"))
                if row:
                    if (node.get("meta") or {}).get("background") == BACKGROUND["white"]:
                        rows.append({"row": row, "background": "white"})
                    else:
                        rows.append(row)
            elif t == "CHART":
                meta = node.get("meta") or {}
                nm = meta.get("sliceName")
                if nm and nm in kept_names:
                    rows.append([nm])
                    geometry[nm] = _geo(meta)
            elif t == "HEADER" and depth == 0:
                meta = node.get("meta") or {}
                text = meta.get("text")
                if not isinstance(text, str) or not text.strip():
                    losses.append(Loss("layout", "empty HEADER dropped"))
                    continue
                header: dict = {"header": text}
                # Superset draws a header with no stored size as small (Header.tsx).
                size = _REVERSE_HEADER_SIZE.get(meta.get("headerSize") or "SMALL_HEADER", "small")
                if size != "medium":
                    header["size"] = size
                if meta.get("background") == BACKGROUND["white"]:
                    header["background"] = "white"
                rows.append(header)
            elif t == "DIVIDER" and depth == 0:
                rows.append({"divider": True})
            elif t in ("TABS", "TAB"):
                # mixed/nested tabs at this level are handled by the caller;
                # reaching here means nested tabs inside a tab -> flatten
                losses.append(Loss("layout", f"nested {t} flattened"))
                handle(node.get("children", []), depth + 1)
            else:
                losses.append(Loss("layout", f"{t or cid} element dropped"))

    handle(children)
    return rows


def _dashboard_settings_to_spec(dash: dict, losses: list[Loss],
                                themes: dict[str, str] | None = None) -> dict:
    """The dashboard's own settings, each emitted only when it differs from
    what an omitted spec field compiles to, so neither side drifts in plan."""
    meta = dash.get("metadata") or {}
    out: dict = {}
    scheme = meta.get("color_scheme")
    if isinstance(scheme, str) and scheme.strip():
        out["color_scheme"] = scheme
    for key in ("description", "certified_by", "certification_details"):
        text = dash.get(key)
        if isinstance(text, str) and text.strip():
            out[key] = text
    if out.get("certification_details") and not out.get("certified_by"):
        out.pop("certification_details")
        losses.append(Loss("dashboard", "certification_details without certified_by not preserved"))
    if dash.get("published") is False:
        out["published"] = False
    refresh = meta.get("refresh_frequency")
    if isinstance(refresh, int) and refresh > 0:
        out["refresh_frequency"] = refresh
    orientation = meta.get("filter_bar_orientation")
    if isinstance(orientation, str) and orientation.upper() == "HORIZONTAL":
        out["filter_bar_orientation"] = "horizontal"  # vertical is the default
    if meta.get("show_chart_timestamps") is True:
        out["show_chart_timestamps"] = True
    tags = _tags_to_spec(dash.get("tags"), losses, "dashboard")
    if tags:
        out["tags"] = tags
    theme = _theme_to_spec(dash, themes or {}, losses)
    if theme:
        out["theme"] = theme
    return out


def _theme_to_spec(dash: dict, themes: dict[str, str], losses: list[Loss]) -> str | None:
    """dashboard.theme, by name: a 6.0+ export names the dashboard's theme by
    theme_uuid and ships the theme itself under themes/ (commands/dashboard/export.py
    :164-165 and :199-203 at 6.0.0 and 6.1.0), whose theme_name the spec uses."""
    u = dash.get("theme_uuid")
    if u:
        name = themes.get(str(u))
        if name:
            return name
        losses.append(Loss("dashboard", f"theme {u} has no themes/ file in the bundle; "
                                        f"theme not preserved"))
    elif dash.get("theme_id") is not None:
        # A compiled bundle: the id is the target's own, with no name beside it.
        losses.append(Loss("dashboard", f"theme_id {dash['theme_id']} can't be named "
                                        f"offline; theme not preserved"))
    return None


def _leaf_tabs(layout: dict) -> list[dict]:
    """The spec-layout tabs that hold rows: each top tab, or its sub-tabs."""
    return [leaf for tab in layout.get("tabs") or [] for leaf in (tab.get("tabs") or [tab])]


def decompile_bundle(zip_bytes: bytes, lookup: DatasetLookup) -> DecompileResult:
    losses: list[Loss] = []
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    dash_files = [n for n in zf.namelist() if "/dashboards/" in n and n.endswith(".yaml")]
    if not dash_files:
        raise ValueError("no dashboards/*.yaml in bundle")
    if len(dash_files) > 1:
        losses.append(Loss("dashboard", f"bundle has {len(dash_files)} dashboards; decompiling the first only"))
    dash = yaml.safe_load(zf.read(dash_files[0]))

    themes: dict[str, str] = {}   # theme uuid -> theme_name, from an export's themes/
    for n in zf.namelist():
        if "/themes/" in n and n.endswith(".yaml"):
            ty = yaml.safe_load(zf.read(n)) or {}
            if ty.get("uuid") and isinstance(ty.get("theme_name"), str) and ty["theme_name"]:
                themes[str(ty["uuid"])] = ty["theme_name"]

    charts_by_name: dict[str, dict] = {}
    dataset_uuids: dict[str, str] = {}
    chart_uuids: dict[str, str] = {}
    skipped: list[str] = []
    titles: list[str] = []
    changed_defaults_pending: dict[str, dict] = {}
    for n in zf.namelist():
        if "/charts/" in n and n.endswith(".yaml"):
            cy = yaml.safe_load(zf.read(n))
            titles.append(cy.get("slice_name") or "Unnamed")
            changed: dict = {}
            spec_chart = _chart_to_spec(cy, lookup, losses, changed)
            if spec_chart and changed.get("__last__"):
                changed_defaults_pending[spec_chart["name"]] = changed["__last__"]
            if spec_chart:
                if spec_chart["name"] in charts_by_name:
                    losses.append(Loss(spec_chart["name"], "duplicate slice_name in bundle; suffixed to keep uuid seeds unique"))
                    k = 2
                    while f"{spec_chart['name']} ({k})" in charts_by_name:
                        k += 1
                    old = spec_chart["name"]
                    spec_chart["name"] = f"{spec_chart['name']} ({k})"
                    if changed.get("__last__"):
                        changed_defaults_pending[spec_chart["name"]] = changed["__last__"]
                        if changed_defaults_pending.get(old) is changed["__last__"]:
                            changed_defaults_pending.pop(old)
                charts_by_name[spec_chart["name"]] = spec_chart
                dataset_uuids[spec_chart["name"]] = str(cy.get("dataset_uuid"))
                if cy.get("uuid"):
                    chart_uuids[spec_chart["name"]] = str(cy["uuid"])
            else:
                skipped.append(cy.get("slice_name") or "Unnamed")

    title = dash.get("dashboard_title") or "Untitled"
    slug = dash.get("slug")
    if not slug:
        slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", title.lower())).strip("-") or "untitled"
        losses.append(Loss("dashboard", f"no slug on source dashboard; derived {slug!r} (re-apply will NOT overwrite the original)"))

    position = dash.get("position") or {}
    geometry: dict[str, dict] = {}
    kept = set(charts_by_name)
    grid = position.get("GRID_ID") or {}
    grid_children = grid.get("children", [])
    # Header = grid-level content before the first TABS and footer = after the
    # last (Superset draws them above and under every tab), or, with no tabs, the
    # compiler's marked leading and trailing rows.
    types = [(position.get(c) or {}).get("type") for c in grid_children]
    if "TABS" in types:
        head = types.index("TABS")
        cut = len(types) - types[::-1].index("TABS")
    else:
        def marked(i: int, prefix: str) -> bool:
            return grid_children[i].startswith(
                tuple(f"{kind}-{prefix}" for kind in ("ROW", "HEADER", "DIVIDER")))

        cut = len(grid_children)
        while cut and marked(cut - 1, FOOTER_PREFIX):
            cut -= 1
        head = 0
        while head < cut and marked(head, HEADER_PREFIX):
            head += 1
    header_ids, grid_children, footer_ids = (
        grid_children[:head], grid_children[head:cut], grid_children[cut:])
    header_rows = _walk_rows(position, header_ids, kept, losses, geometry) if header_ids else []
    footer_rows = _walk_rows(position, footer_ids, kept, losses, geometry) if footer_ids else []
    top_types = {(position.get(c) or {}).get("type") for c in grid_children}

    layout: dict
    if top_types and top_types <= {"TABS"}:
        tabs = []
        for tabs_id in grid_children:
            for tab_id in (position.get(tabs_id) or {}).get("children", []):
                tab_node = position.get(tab_id) or {}
                tab_title = (tab_node.get("meta") or {}).get("text") or "Tab"
                kids = tab_node.get("children", [])
                if kids and all((position.get(c) or {}).get("type") == "TABS" for c in kids):
                    subs = []
                    for sub_tabs_id in kids:
                        for sub_id in (position.get(sub_tabs_id) or {}).get("children", []):
                            sub_node = position.get(sub_id) or {}
                            sub_title = (sub_node.get("meta") or {}).get("text") or "Tab"
                            sub_rows = _walk_rows(position, sub_node.get("children", []), kept, losses, geometry)
                            if sub_rows:
                                subs.append({"title": sub_title, "rows": sub_rows})
                            else:
                                losses.append(Loss("layout", f"tab {tab_title!r} > {sub_title!r} had no representable content; dropped"))
                    if subs:
                        tabs.append({"title": tab_title, "tabs": subs})
                    else:
                        losses.append(Loss("layout", f"tab {tab_title!r} had no representable content; dropped"))
                    continue
                tab_rows = _walk_rows(position, kids, kept, losses, geometry)
                if tab_rows:
                    tabs.append({"title": tab_title, "rows": tab_rows})
                else:
                    losses.append(Loss("layout", f"tab {tab_title!r} had no representable content; dropped"))
        layout = {"tabs": tabs} if tabs else {"rows": []}
    else:
        if "TABS" in top_types:
            losses.append(Loss("layout", "mixed rows + tabs at top level; tabs flattened into rows"))
        rows = _walk_rows(position, grid_children, kept, losses, geometry)
        layout = {"rows": rows}
    if footer_rows:
        layout["footer"] = footer_rows
    if header_rows:
        layout = {"header": header_rows, **layout}

    def body_and_footer() -> list:
        """The rows of charts and markdown (headers and dividers skipped), header
        and footer included."""
        body = layout.get("rows") if "rows" in layout else [r for leaf in _leaf_tabs(layout) for r in leaf["rows"]]
        rows = [*layout.get("header", []), *(body or []), *layout.get("footer", [])]
        return [items for items in (row_items(r) for r in rows) if items is not None]

    all_rows = body_and_footer()
    placed = {x for row in (all_rows or []) for x in row if isinstance(x, str)}
    unplaced_target = layout.get("rows") if "rows" in layout else (_leaf_tabs(layout)[0]["rows"] if layout.get("tabs") else None)
    for name in sorted(kept - placed):
        losses.append(Loss("layout", f"chart {name!r} not found in layout; appended as its own row"))
        if unplaced_target is None:
            layout = {"rows": [[name]]}
            unplaced_target = layout["rows"]
        else:
            unplaced_target.append([name])

    for name, geo in geometry.items():
        c = charts_by_name.get(name)
        if not c:
            continue
        if geo.get("width"):
            c["width"] = max(1, min(12, int(geo["width"])))
        if geo.get("height"):
            h = max(1, round(int(geo["height"]) / ROW_UNITS_PER_SPEC_UNIT))
            c["height"] = h
            if int(geo["height"]) != h * ROW_UNITS_PER_SPEC_UNIT:
                losses.append(Loss(name, f"height {geo['height']} rounded to {h * ROW_UNITS_PER_SPEC_UNIT} row units"))
        if geo.get("display_name"):
            c["display_name"] = geo["display_name"]

    # Row overflow guard: source rows can exceed 12 units after flattening.
    def width_of(item) -> int:
        if isinstance(item, str):
            return charts_by_name.get(item, {}).get("width") or 0
        return item.get("width") or 0

    all_rows = body_and_footer()
    for i, row in enumerate(all_rows or []):
        total = sum(width_of(x) for x in row)
        if total > 12:
            losses.append(Loss("layout", f"row {i} widths sum to {total} > 12; widths cleared, will auto-split"))
            for x in row:
                if isinstance(x, str):
                    charts_by_name.get(x, {}).pop("width", None)
                else:
                    x.pop("width", None)

    ordered_names: list[str] = []
    for row in (all_rows or []):
        for x in row:
            if isinstance(x, str) and x in charts_by_name:
                ordered_names.append(x)
    ordered = [charts_by_name[n] for n in ordered_names]

    label_colors = (dash.get("metadata") or {}).get("label_colors") or {}
    css = dash.get("css") if isinstance(dash.get("css"), str) else ""
    spec = {
        "spec_version": "1",
        "dashboard": {
            "title": title,
            "slug": slug,
            # Superset force-stamps True when the key is absent from a PUT
            # (docs/CONTRACTS.md), so read it back explicitly and always emit it.
            "cross_filters": bool((dash.get("metadata") or {}).get("cross_filters_enabled", False)),
            **({"label_colors": dict(label_colors)} if label_colors else {}),
            # "Edit CSS"; blank or absent (Superset stores null) reads as omitted.
            **({"css": css} if css.strip() else {}),
            **_dashboard_settings_to_spec(dash, losses, themes),
        },
        "charts": ordered,
        "layout": layout,
    }
    # Filter dataset identities ride along under "filter:<name>" keys so plan
    # can compare filters by resolved uuid, exactly as it does for charts.
    filters = _native_filters_to_spec(dash.get("metadata") or {}, lookup, losses,
                                      filter_uuids=dataset_uuids)
    if filters:
        spec["filters"] = filters
    if not ordered:
        losses.append(Loss("dashboard", "no representable charts; spec is not valid for apply"))
    truncated = getattr(lookup, "truncated", 0)
    if truncated:
        losses.append(Loss(
            "dashboard",
            f"the dataset index stopped at {truncated} datasets (page cap); any "
            f"'dataset uuid not resolvable' loss above may be a dataset past the cap "
            f"rather than a missing one -- re-check those charts before trusting this spec"))
    if changed_defaults_pending:
        _check_changed_defaults(spec, changed_defaults_pending, losses)
    kept_names = {c["name"] for c in spec.get("charts", [])}
    return DecompileResult(spec=spec, losses=losses, dataset_uuids=dataset_uuids,
                           chart_uuids={n: u for n, u in chart_uuids.items() if n in kept_names},
                           skipped_charts=skipped,
                           dashboard_uuid=str(dash["uuid"]) if dash.get("uuid") else None,
                           source_slug=dash.get("slug") or None,
                           chart_titles=titles)


PAGE_CAP = 200  # 20,000 datasets; a runaway guard, not an expected ceiling


def live_dataset_lookup(client) -> DatasetLookup:
    """uuid -> triple, resolved lazily against the live instance.

    Sets `lookup.truncated` when the runaway guard trips, so decompile can SAY
    the index is incomplete. Without that, a dataset past the cap silently
    became "uuid not resolvable" and its chart was dropped -- a wrong answer
    dressed as an honest loss, which is the one failure this decompiler must
    never produce."""
    cache: dict[str, dict] | None = None

    def lookup(u: str) -> dict | None:
        nonlocal cache
        if cache is None:
            cache = {}
            page = 0
            while True:
                out = client.get(
                    "/api/v1/dataset/",
                    q={"columns": ["uuid", "table_name", "schema", "database.database_name"],
                       "page": page, "page_size": 100},
                )["result"]
                if not out:
                    break
                for d in out:
                    cache[str(d["uuid"])] = {
                        "database": (d.get("database") or {}).get("database_name"),
                        "schema": d.get("schema") or None,
                        "table": d["table_name"],
                    }
                page += 1
                if page > PAGE_CAP:
                    lookup.truncated = len(cache)
                    break
        return cache.get(u)

    lookup.truncated = 0
    return lookup


def decompile_live(slug_or_id: str, client) -> DecompileResult:
    if slug_or_id.isdigit():
        did = int(slug_or_id)
    else:
        dash = client.find_dashboard_by_slug(slug_or_id)
        if dash is None:
            raise ValueError(f"no dashboard with slug {slug_or_id!r}")
        did = dash["id"]
    blob = client.export_dashboard(did)
    result = decompile_bundle(blob, live_dataset_lookup(client))
    _read_owners(result, client, did)
    return result


def _read_owners(result: DecompileResult, client, dashboard_id: int) -> None:
    """dashboard.owners from the live dashboard: exports carry no owners
    (ImportV1DashboardSchema has none), so they come from the REST API, named by
    username where the instance returns usernames and by email elsewhere
    (chartwright.owners). An owner that can't be named leaves `owners` out, with a
    loss: a partial list would drop that owner on the next apply."""
    from .client import SupersetAPIError
    from .owners import live_owner_ids, owner_names

    result.owner_ids = live_owner_ids(client, dashboard_id)
    try:
        names, why = owner_names(client, result.owner_ids)
    except SupersetAPIError as e:  # e.g. an account the owner list is closed to
        names, why = None, f"owners not read back ({e}); omitted, so apply leaves them alone"
    if names is None:
        result.losses.append(Loss("dashboard", why))
    elif names:
        result.spec["dashboard"]["owners"] = names
