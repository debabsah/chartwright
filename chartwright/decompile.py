"""Decompiler: Superset export bundle -> spec (+ explicit lossiness report).

Turns any existing dashboard into a diffable, editable spec. Decompilation is
honest about loss: everything outside the spec surface is NAMED in the loss
report, never silently dropped-and-forgotten.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from typing import Callable, get_args

import yaml

from .compiler import FOOTER_PREFIX, ROW_UNITS_PER_SPEC_UNIT, SDC_BAR_MARKER, VIZ_TYPE
from .spec import ADHOC_AGGREGATES, FORMAT_COLOR_HEX, FORMAT_TEXT_HEX, FilterOp, metric_label

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
        out["y_axis_max"] = y["max"]
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

    def losses_json(self) -> list[dict]:
        return [loss.as_dict() for loss in self.losses]


DatasetLookup = Callable[[str], dict | None]
"""dataset_uuid -> {'database','schema','table'} or None."""


def _format_to_spec(cf: dict) -> dict | None:
    """Superset conditional_formatting entry -> FormatRule dict, None if outside surface."""
    text = cf.get("objectFormatting") == "TEXT_COLOR"
    palette = FORMAT_TEXT_HEX if text else FORMAT_COLOR_HEX
    color = {v.upper(): k for k, v in palette.items()}.get((cf.get("colorScheme") or "").upper())
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
        losses.append(Loss(chart, f"filter not representable, dropped: {f}"))
    return [
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


def _chart_to_spec(chart_yaml: dict, lookup: DatasetLookup, losses: list[Loss]) -> dict | None:
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
        if p.get("subheader"):
            out["subtitle"] = p["subheader"]
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
        if p.get("time_range") and p["time_range"] != "No filter":
            out["time_range"] = p["time_range"]
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
        hidden, formats = [], {}
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
                formats[label] = cfg["d3NumberFormat"]
            extra = sorted(k for k in cfg if k not in ("visible", "d3NumberFormat"))
            if extra:
                losses.append(Loss(name, f"column_config {label!r} settings not preserved: {extra}"))
        if hidden:
            out["hidden"] = hidden
        if formats:
            out["number_formats"] = formats
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
        if (p.get("aggregateFunction") or "Sum") != "Sum":
            losses.append(Loss(name, f"pivot aggregateFunction {p['aggregateFunction']!r} not preserved (Sum on re-apply)"))
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
        keep_row_limit()
    elif spec_type == "histogram":
        col = p.get("column")
        if not col:
            losses.append(Loss(name, "histogram without column; chart skipped"))
            return None
        out["column"] = col
        if p.get("bins"):
            out["bins"] = p["bins"]
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
        keep_row_limit()
    elif spec_type == "treemap":
        m = metric_one(p.get("metric"))
        gb = [g for g in (p.get("groupby") or []) if isinstance(g, str)]
        if m is None or not gb:
            losses.append(Loss(name, "treemap needs metric + groupby; chart skipped"))
            return None
        out["metric"] = m
        out["groupby"] = gb
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
            if p.get(kind_key.replace("seriesType", "markerEnabled")):  # markerEnabled / markerEnabledB
                series["markers"] = True
            out[key] = series
        if _filters_to_spec({"adhoc_filters": p.get("adhoc_filters_b")}, [], name) != (out.get("filters") or []):
            losses.append(Loss(name, "query B filters differ from query A's; not preserved (both take the chart's filters)"))
        if p.get("time_grain_sqla"):
            out["time_grain"] = p["time_grain_sqla"]
        if p.get("time_range") and p["time_range"] != "No filter":
            out["time_range"] = p["time_range"]
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
    if spec_type in _AXIS_TYPES:
        _x_labels_to_spec(p, out, losses, name)
        _echart_options_to_spec(p, out, losses, name, spec_type)
        mapped_here = mapped_here | _X_LABEL_KEYS | {"echart_options"}
    unmapped = sorted(k for k in p if k not in _IGNORABLE and k not in mapped_here)
    if unmapped:
        losses.append(Loss(name, f"params not preserved: {unmapped}"))
    return out


def _native_filters_to_spec(
    metadata: dict, lookup: DatasetLookup, losses: list[Loss],
    filter_uuids: dict[str, str] | None = None,
) -> list[dict]:
    out = []
    for nf in metadata.get("native_filter_configuration") or []:
        name = nf.get("name") or nf.get("id") or "filter"
        ftype = nf.get("filterType")
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
            value = ((nf.get("defaultDataMask") or {}).get("filterState") or {}).get("value")
            if isinstance(value, str) and value:
                f["default"] = value
                out.append(f)
                continue  # default preserved; skip the default-loss check
            out.append(f)
        else:
            losses.append(Loss(f"filter:{name}", f"filterType {ftype!r} outside spec surface; dropped"))
            continue
        dm = nf.get("defaultDataMask") or {}
        if dm.get("filterState") or dm.get("extraFormData"):
            losses.append(Loss(f"filter:{name}", "default value not preserved"))
    return out


def _walk_rows(position: dict, children: list[str], kept_names: set[str],
               losses: list[Loss], geometry: dict[str, dict]) -> list[list]:
    """Convert ROW children into spec rows (chart names + markdown blocks)."""
    rows: list[list] = []

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
                            geometry[nm] = {"width": meta.get("width"), "height": meta.get("height")}
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
                    rows.append(row)
            elif t == "CHART":
                meta = node.get("meta") or {}
                nm = meta.get("sliceName")
                if nm and nm in kept_names:
                    rows.append([nm])
                    geometry[nm] = {"width": meta.get("width"), "height": meta.get("height")}
            elif t in ("TABS", "TAB"):
                # mixed/nested tabs at this level are handled by the caller;
                # reaching here means nested tabs inside a tab -> flatten
                losses.append(Loss("layout", f"nested {t} flattened"))
                handle(node.get("children", []), depth + 1)
            else:
                losses.append(Loss("layout", f"{t or cid} element dropped"))

    handle(children)
    return rows


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

    charts_by_name: dict[str, dict] = {}
    dataset_uuids: dict[str, str] = {}
    for n in zf.namelist():
        if "/charts/" in n and n.endswith(".yaml"):
            cy = yaml.safe_load(zf.read(n))
            spec_chart = _chart_to_spec(cy, lookup, losses)
            if spec_chart:
                if spec_chart["name"] in charts_by_name:
                    losses.append(Loss(spec_chart["name"], "duplicate slice_name in bundle; suffixed to keep uuid seeds unique"))
                    spec_chart["name"] = f"{spec_chart['name']} (2)"
                charts_by_name[spec_chart["name"]] = spec_chart
                dataset_uuids[spec_chart["name"]] = str(cy.get("dataset_uuid"))

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
    # Footer = grid-level content after the last TABS (Superset draws it under
    # every tab), or, with no tabs, the compiler's marked trailing rows.
    types = [(position.get(c) or {}).get("type") for c in grid_children]
    if "TABS" in types:
        cut = len(types) - types[::-1].index("TABS")
    else:
        cut = len(grid_children)
        while cut and grid_children[cut - 1].startswith(f"ROW-{FOOTER_PREFIX}"):
            cut -= 1
    grid_children, footer_ids = grid_children[:cut], grid_children[cut:]
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

    def body_and_footer() -> list:
        body = layout.get("rows") if "rows" in layout else [r for leaf in _leaf_tabs(layout) for r in leaf["rows"]]
        return [*(body or []), *layout.get("footer", [])]

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
    spec = {
        "spec_version": "1",
        "dashboard": {
            "title": title,
            "slug": slug,
            # Superset force-stamps True when the key is absent from a PUT
            # (docs/CONTRACTS.md), so read it back explicitly and always emit it.
            "cross_filters": bool((dash.get("metadata") or {}).get("cross_filters_enabled", False)),
            **({"label_colors": dict(label_colors)} if label_colors else {}),
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
    return DecompileResult(spec=spec, losses=losses, dataset_uuids=dataset_uuids)


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
    return decompile_bundle(blob, live_dataset_lookup(client))
