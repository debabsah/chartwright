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

from .compiler import ROW_UNITS_PER_SPEC_UNIT, SDC_BAR_MARKER, VIZ_TYPE
from .spec import ADHOC_AGGREGATES, FORMAT_COLOR_HEX, FilterOp

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
    color = {v.upper(): k for k, v in FORMAT_COLOR_HEX.items()}.get((cf.get("colorScheme") or "").upper())
    op = cf.get("operator")
    if not color or not cf.get("column") or op not in ("<", ">", "between"):
        return None
    rule: dict = {"metric": cf["column"], "operator": op, "color": color}
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
            # The spec sorts descending (a ranking); an ascending live sort is
            # named rather than silently flipped on the next apply.
            losses.append(Loss(name, "ascending sort not preserved; the spec sorts "
                                     "sort_by descending, so re-apply will sort descending"))
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
        if p.get("date_format"):
            out["date_format"] = p["date_format"]
        rules = []
        for cf in p.get("conditional_formatting") or []:
            rule = _format_to_spec(cf if isinstance(cf, dict) else {})
            if rule is None:
                losses.append(Loss(name, f"conditional format not representable, dropped: {cf}"))
            else:
                rules.append(rule)
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

    mapped_here = {"combineMetric", "conditional_formatting"} if spec_type == "pivot_table" else (
        {"order_by_cols", "timeseries_limit_metric", "series_limit_metric"}
        if spec_type == "table" else set())
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
            multi = (nf.get("controlValues") or {}).get("multiSelect", True)
            if multi is False:
                f["multi"] = False
            out.append(f)
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
                            block["height"] = max(1, round(int(meta["height"]) / ROW_UNITS_PER_SPEC_UNIT))
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
    top_types = {(position.get(c) or {}).get("type") for c in grid_children}

    layout: dict
    if top_types and top_types <= {"TABS"}:
        tabs = []
        for tabs_id in grid_children:
            for tab_id in (position.get(tabs_id) or {}).get("children", []):
                tab_node = position.get(tab_id) or {}
                tab_rows = _walk_rows(position, tab_node.get("children", []), kept, losses, geometry)
                if tab_rows:
                    tabs.append({"title": (tab_node.get("meta") or {}).get("text") or "Tab", "rows": tab_rows})
                else:
                    losses.append(Loss("layout", f"tab {(tab_node.get('meta') or {}).get('text')!r} had no representable content; dropped"))
        layout = {"tabs": tabs} if tabs else {"rows": []}
    else:
        if "TABS" in top_types:
            losses.append(Loss("layout", "mixed rows + tabs at top level; tabs flattened into rows"))
        rows = _walk_rows(position, grid_children, kept, losses, geometry)
        layout = {"rows": rows}

    all_rows = layout.get("rows") if "rows" in layout else [r for t in layout["tabs"] for r in t["rows"]]
    placed = {x for row in (all_rows or []) for x in row if isinstance(x, str)}
    unplaced_target = layout.get("rows") if "rows" in layout else (layout["tabs"][0]["rows"] if layout.get("tabs") else None)
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

    all_rows = layout.get("rows") if "rows" in layout else [r for t in layout["tabs"] for r in t["rows"]]
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

    spec = {
        "spec_version": "1",
        "dashboard": {"title": title, "slug": slug},
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
    return DecompileResult(spec=spec, losses=losses, dataset_uuids=dataset_uuids)


def live_dataset_lookup(client) -> DatasetLookup:
    """uuid -> triple, resolved lazily against the live instance."""
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
                if page > 200:
                    break
        return cache.get(u)

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
