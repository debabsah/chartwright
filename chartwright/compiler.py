"""Deterministic compiler: spec + resolution -> Superset import bundle (ZIP).

Byte-stable by construction: fixed bundle timestamp, sorted YAML keys, sorted
ZIP entries, fixed ZIP entry timestamps, uuid5 identity. Golden tests assert
the bundle's exact bytes. Param shapes come from real 6.1.0 exports
(tests/fixtures/featured_charts_export.zip + live Sales/Slack exports).
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import yaml

from . import ids
from .resolver import Resolution
from .spec import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_TIME_GRAIN,
    FORMAT_COLOR_HEX,
    DashboardSpec,
    MarkdownBlock,
    parse_metric,
)

BUNDLE_ROOT = "sdc_bundle"
FIXED_TIMESTAMP = "2026-01-01T00:00:00+00:00"
ZIP_DATE_TIME = (2026, 1, 1, 0, 0, 0)

# ponytail: 1 spec grid unit -> 5 superset row units (1 row unit ~ 8px).
# Calibration knob; validated against rendered dashboards.
ROW_UNITS_PER_SPEC_UNIT = 5

VIZ_TYPE = {
    "big_number_total": "big_number_total",
    "big_number_trend": "big_number",
    "timeseries_line": "echarts_timeseries_line",
    "timeseries_bar": "echarts_timeseries_bar",
    "timeseries_area": "echarts_area",
    "timeseries_scatter": "echarts_timeseries_scatter",
    "bar": "echarts_timeseries_bar",
    "pie": "pie",
    "table": "table",
    "pivot_table": "pivot_table_v2",
    "heatmap": "heatmap_v2",
    "histogram": "histogram_v2",
    "funnel": "funnel",
    "treemap": "treemap_v2",
}
# 'bar' and 'timeseries_bar' share a viz_type; the compiler marks categorical
# bars in params so the decompiler can tell them apart (x column not temporal
# is not knowable offline).
SDC_BAR_MARKER = "sdc_categorical_bar"


def _yaml(data: dict) -> bytes:
    return yaml.safe_dump(data, sort_keys=True, default_flow_style=False, allow_unicode=True).encode()


def _metric_payload(metric: str, slug: str, chart_name: str) -> str | dict:
    adhoc = parse_metric(metric)
    if adhoc is None:
        return metric
    option = "metric_sdc_" + uuid.uuid5(ids.NAMESPACE, f"{slug}/chart/{chart_name}/metric/{metric}").hex[:12]
    label = adhoc["label"] or metric
    if adhoc["column"] == "*":
        return {
            "expressionType": "SQL",
            "sqlExpression": f"{adhoc['aggregate']}(*)",
            "label": label,
            "optionName": option,
            "hasCustomLabel": bool(adhoc["label"]),
        }
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": adhoc["column"]},
        "aggregate": adhoc["aggregate"],
        "label": label,
        "optionName": option,
        "hasCustomLabel": bool(adhoc["label"]),
    }


def _format_rule_payload(rule) -> dict:
    out = {
        "column": rule.metric,
        "colorScheme": FORMAT_COLOR_HEX[rule.color],
        "operator": rule.operator,
    }
    if rule.operator == "between":
        out["targetValueLeft"] = rule.target_left
        out["targetValueRight"] = rule.target_right
    else:
        out["targetValue"] = rule.target
    return out


def _adhoc_filters(chart) -> list[dict]:
    out = []
    for f in chart.filters:
        out.append({
            "clause": "WHERE",
            "expressionType": "SIMPLE",
            "subject": f.column,
            "operator": f.op,
            "comparator": f.value,
        })
    return out


def _pin_big_number_fonts(p: dict) -> None:
    """Pin the number and subtitle sizes to the plugin's own documented
    defaults (proportions of the card). Left unset, 6.1 renders a subtitle fed
    through the legacy `subheader` key at proportion 1 of the card
    (BigNumberTotal/transformProps.ts:77-80 at 6.1.0: `subheaderFontSize ?? 1`),
    so short subtitles blow up and crop. 4.1.4/5.0.0 declare both controls
    natively; 6.1 honors them through that same fallback."""
    p["header_font_size"] = 0.4
    p["subheader_font_size"] = 0.15


def _chart_params(chart, spec: DashboardSpec, resolution: Resolution) -> dict:
    ds = resolution.for_chart(chart.dataset)
    slug = spec.dashboard.slug
    t = chart.type
    p: dict = {
        "datasource": f"{ds.id}__table",
        "viz_type": VIZ_TYPE[t],
        # Always explicit: Superset's default time window can be narrow enough
        # to return empty data on correct charts (false-trips the smoke check).
        "time_range": "No filter",
        "adhoc_filters": _adhoc_filters(chart),
    }

    def metric(m: str):
        return _metric_payload(m, slug, chart.name)

    if t == "big_number_total":
        p["metric"] = metric(chart.metric)
        if chart.subtitle:
            p["subheader"] = chart.subtitle
        if chart.number_format:
            p["y_axis_format"] = chart.number_format
        _pin_big_number_fonts(p)
    elif t == "big_number_trend":
        p["metric"] = metric(chart.metric)
        p["x_axis"] = chart.time_column
        p["time_grain_sqla"] = chart.time_grain or DEFAULT_TIME_GRAIN
        p["show_trend_line"] = True
        p["start_y_axis_at_zero"] = True
        p["rolling_type"] = "None"
        if chart.number_format:
            p["y_axis_format"] = chart.number_format
        _pin_big_number_fonts(p)
    elif t in ("timeseries_line", "timeseries_bar", "timeseries_area", "timeseries_scatter"):
        p["metrics"] = [metric(m) for m in chart.metrics]
        p["x_axis"] = chart.time_column
        p["time_grain_sqla"] = chart.time_grain or DEFAULT_TIME_GRAIN
        if chart.time_range:
            p["time_range"] = chart.time_range
        p["groupby"] = [chart.groupby] if chart.groupby else []
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]
        p["y_axis_format"] = "SMART_NUMBER"
        p["rich_tooltip"] = True
        p["show_legend"] = True
        if t == "timeseries_area":
            p["opacity"] = 0.2
        if t == "timeseries_scatter":
            p["markerSize"] = 6
    elif t == "bar":
        p["metrics"] = [metric(m) for m in chart.metrics]
        p["x_axis"] = chart.x_column
        p["groupby"] = [chart.groupby] if chart.groupby else []
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]
        p["y_axis_format"] = "SMART_NUMBER"
        p["rich_tooltip"] = True
        p["show_legend"] = True
        # Rankings read sorted by their measure, not by label order. The
        # horizontal axis renders bottom-up, so ascending puts the largest
        # bar on top there; vertical wants descending left to right.
        first = p["metrics"][0]
        p["x_axis_sort"] = first["label"] if isinstance(first, dict) else first
        p["x_axis_sort_asc"] = chart.orientation == "horizontal"
        if chart.orientation == "horizontal":
            p["orientation"] = "horizontal"
        p[SDC_BAR_MARKER] = True
    elif t == "pie":
        p["metric"] = metric(chart.metric)
        p["groupby"] = [chart.groupby]
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]
        p["sort_by_metric"] = True
        p["show_labels_threshold"] = 5
        p["label_type"] = "key_percent"
        if chart.donut:
            p["donut"] = True
    elif t == "table":
        if chart.columns:
            p["query_mode"] = "raw"
            p["all_columns"] = chart.columns
        else:
            p["query_mode"] = "aggregate"
            p["groupby"] = chart.groupby or []
            p["metrics"] = [metric(m) for m in (chart.metrics or [])]
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]
        p["server_page_length"] = 10
        if chart.sort_by:
            p["order_desc"] = True
    elif t == "pivot_table":
        p["groupbyRows"] = chart.rows
        p["groupbyColumns"] = chart.columns
        p["metrics"] = [metric(m) for m in chart.metrics]
        p["aggregateFunction"] = "Sum"
        p["metricsLayout"] = "COLUMNS"
        p["rowOrder"] = "key_a_to_z"
        p["colOrder"] = "key_a_to_z"
        p["valueFormat"] = "SMART_NUMBER"
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]
        # Emitted only when set, so pre-feature bundles stay byte-identical.
        if chart.combine_metric:
            p["combineMetric"] = True
        if chart.date_format:
            p["date_format"] = chart.date_format
        if chart.conditional_formatting:
            p["conditional_formatting"] = [
                _format_rule_payload(r) for r in chart.conditional_formatting
            ]
    elif t == "heatmap":
        p["x_axis"] = chart.x_column
        p["groupby"] = chart.y_column
        p["metric"] = metric(chart.metric)
        p["normalize_across"] = "heatmap"
        p["legend_type"] = "continuous"
        p["linear_color_scheme"] = "superset_seq_1"
        p["sort_x_axis"] = "alpha_asc"
        p["sort_y_axis"] = "alpha_asc"
        p["show_legend"] = True
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]
    elif t == "histogram":
        p["column"] = chart.column
        p["bins"] = chart.bins
        p["groupby"] = [chart.groupby] if chart.groupby else []
        p["normalize"] = False
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]
    elif t == "funnel":
        p["metric"] = metric(chart.metric)
        p["groupby"] = [chart.groupby]
        p["sort_by_metric"] = True
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]
    elif t == "treemap":
        p["metric"] = metric(chart.metric)
        p["groupby"] = chart.groupby
        p["row_limit"] = chart.row_limit or DEFAULT_ROW_LIMIT[t]

    # Bind charts without their own TIME column to the dataset's main temporal
    # column. The dashboard time filter reaches a chart only through a time
    # binding; without one the chart silently ignores it while the filter bar
    # still counts it as filtered (verified live on 6.1.0: full-month total
    # under a one-week default). The categorical bar's x_axis is not a time
    # column, so it needs the binding too.
    timeseries = ("timeseries_line", "timeseries_bar", "timeseries_area",
                  "timeseries_scatter", "big_number_trend")
    if t not in timeseries and ds.main_dttm_col:
        p["granularity_sqla"] = ds.main_dttm_col
    return p


def _chart_yaml(chart, spec: DashboardSpec, resolution: Resolution) -> dict:
    ds = resolution.for_chart(chart.dataset)
    return {
        "slice_name": chart.name,
        "description": None,
        "certified_by": None,
        "certification_details": None,
        "viz_type": VIZ_TYPE[chart.type],
        "params": _chart_params(chart, spec, resolution),
        "query_context": None,
        "cache_timeout": None,
        "uuid": str(ids.chart_uuid(spec.dashboard.slug, chart.name)),
        "version": "1.0.0",
        "dataset_uuid": ds.uuid,
    }


def _rows_into(pos: dict, rows, spec: DashboardSpec, parents: list[str], prefix: str, counter: list[int]) -> list[str]:
    """Emit ROW/CHART/MARKDOWN nodes for a list of rows; returns row ids."""
    slug = spec.dashboard.slug
    row_ids: list[str] = []
    for i, row in enumerate(rows):
        row_id = f"ROW-{prefix}{i + 1}"
        child_ids: list[str] = []
        for j, item in enumerate(row):
            if isinstance(item, MarkdownBlock):
                md_id = f"MARKDOWN-{prefix}{i + 1}-{j + 1}"
                child_ids.append(md_id)
                pos[md_id] = {
                    "type": "MARKDOWN",
                    "id": md_id,
                    "children": [],
                    "parents": [*parents, row_id],
                    "meta": {
                        "code": item.markdown,
                        "width": spec.resolved_item_width(item),
                        "height": (item.height or 4) * ROW_UNITS_PER_SPEC_UNIT,
                    },
                }
                continue
            counter[0] += 1
            cuuid = ids.chart_uuid(slug, item)
            chart_id = f"CHART-sdc-{cuuid.hex[:10]}"
            child_ids.append(chart_id)
            pos[chart_id] = {
                "type": "CHART",
                "id": chart_id,
                "children": [],
                "parents": [*parents, row_id],
                "meta": {
                    "uuid": str(cuuid),
                    "sliceName": item,
                    "width": spec.resolved_item_width(item),
                    "height": int(round(spec.resolved_height(item) * ROW_UNITS_PER_SPEC_UNIT)),
                    # Placeholder the importer requires and remaps via uuid.
                    "chartId": 100000 + counter[0],
                },
            }
        pos[row_id] = {
            "type": "ROW",
            "id": row_id,
            "children": child_ids,
            "parents": parents,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        row_ids.append(row_id)
    return row_ids


def _sketch_chart_node(pos, spec, sc, width, parents, counter) -> str:
    """Emit one CHART node from a sketch cell; explicit chart height wins."""
    counter[0] += 1
    cuuid = ids.chart_uuid(spec.dashboard.slug, sc.name)
    chart_id = f"CHART-sdc-{cuuid.hex[:10]}"
    explicit = next(c.height for c in spec.charts if c.name == sc.name)
    pos[chart_id] = {
        "type": "CHART",
        "id": chart_id,
        "children": [],
        "parents": parents,
        "meta": {
            "uuid": str(cuuid),
            "sliceName": sc.name,
            "width": width,
            "height": int(round((explicit or sc.height) * ROW_UNITS_PER_SPEC_UNIT)),
            "chartId": 100000 + counter[0],
        },
    }
    return chart_id


def _sketch_into(pos, parsed_rows, spec, parents: list[str], prefix: str, counter: list[int]) -> list[str]:
    """Emit ROW / COLUMN / CHART nodes from a parsed sketch (chartwright/sketch.py)."""
    from .sketch import SketchColumn

    row_ids: list[str] = []
    for i, srow in enumerate(parsed_rows):
        row_id = f"ROW-{prefix}{i + 1}"
        child_ids: list[str] = []
        for j, child in enumerate(srow.children):
            if isinstance(child, SketchColumn):
                col_id = f"COLUMN-{prefix}{i + 1}-{j + 1}"
                col_children = [
                    _sketch_chart_node(pos, spec, sc, child.width, [*parents, row_id, col_id], counter)
                    for sc in child.children
                ]
                pos[col_id] = {
                    "type": "COLUMN",
                    "id": col_id,
                    "children": col_children,
                    "parents": [*parents, row_id],
                    "meta": {"background": "BACKGROUND_TRANSPARENT", "width": child.width},
                }
                child_ids.append(col_id)
            else:
                child_ids.append(
                    _sketch_chart_node(pos, spec, child, child.width, [*parents, row_id], counter)
                )
        pos[row_id] = {
            "type": "ROW",
            "id": row_id,
            "children": child_ids,
            "parents": parents,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        row_ids.append(row_id)
    return row_ids


def _position(spec: DashboardSpec) -> dict:
    """The layout tree Superset's UI builds by drag and drop, generated from
    the spec instead."""
    pos: dict = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": spec.dashboard.title}},
    }
    counter = [0]
    if spec.layout.rows:
        grid_children = _rows_into(pos, spec.layout.rows, spec, ["ROOT_ID", "GRID_ID"], "sdc-", counter)
    elif spec.layout.sketch:
        grid_children = _sketch_into(
            pos, spec.layout.parsed_sketch(), spec, ["ROOT_ID", "GRID_ID"], "sdc-", counter
        )
    else:
        tabs_id = "TABS-sdc-1"
        tab_ids: list[str] = []
        for k, tab in enumerate(spec.layout.tabs or []):
            tab_id = f"TAB-sdc-{k + 1}"
            into = _sketch_into if tab.sketch else _rows_into
            row_ids = into(
                pos, tab.parsed_sketch() if tab.sketch else tab.rows, spec,
                ["ROOT_ID", "GRID_ID", tabs_id, tab_id],
                f"sdc-t{k + 1}-", counter,
            )
            pos[tab_id] = {
                "type": "TAB",
                "id": tab_id,
                "children": row_ids,
                "parents": ["ROOT_ID", "GRID_ID", tabs_id],
                "meta": {"text": tab.title, "defaultText": "Tab title", "placeholder": "Tab title"},
            }
            tab_ids.append(tab_id)
        pos[tabs_id] = {
            "type": "TABS",
            "id": tabs_id,
            "children": tab_ids,
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {},
        }
        grid_children = [tabs_id]
    pos["GRID_ID"] = {"type": "GRID", "id": "GRID_ID", "children": grid_children, "parents": ["ROOT_ID"]}
    return pos


def _native_filters(spec: DashboardSpec, resolution: Resolution) -> list[dict]:
    out = []
    for f in spec.filters:
        fid = "NATIVE_FILTER-sdc-" + uuid.uuid5(
            ids.NAMESPACE, f"{spec.dashboard.slug}/filter/{f.name}"
        ).hex[:12]
        base = {
            "id": fid,
            "name": f.name,
            "description": "",
            "cascadeParentIds": [],
            "defaultDataMask": {"extraFormData": {}, "filterState": {}, "ownState": {}},
            "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
            "type": "NATIVE_FILTER",
        }
        if f.type == "select":
            ds = resolution.datasets[f.dataset.key()]
            base["filterType"] = "filter_select"
            base["targets"] = [{"column": {"name": f.column}, "datasetUuid": ds.uuid}]
            base["controlValues"] = {
                "multiSelect": f.multi,
                "defaultToFirstItem": False,
                "enableEmptyFilter": False,
                "inverseSelection": False,
                "searchAllOptions": False,
            }
        elif f.type == "range":
            ds = resolution.datasets[f.dataset.key()]
            base["filterType"] = "filter_range"
            base["targets"] = [{"column": {"name": f.column}, "datasetUuid": ds.uuid}]
            base["controlValues"] = _range_control_values(f)
            mask = _range_default_mask(f)
            if mask:
                base["defaultDataMask"] = mask
            if f.charts:
                # Tool-owned, name-based scope marker (filter entries are opaque
                # dicts to Superset). The bundle ships ROOT scope; slice ids
                # don't exist at compile time; apply's scope stage rewrites live
                # ids, so without this the scope is unrecoverable on decompile.
                base["sdc_scope_charts"] = list(f.charts)
        else:  # time_range
            base["filterType"] = "filter_time"
            base["targets"] = [{}]
            base["controlValues"] = {}
            if f.default:
                # Same contract as the range default: BOTH halves or it never
                # hydrates (TimeFilterPlugin reads filterState.value for the
                # pill; queries read extraFormData.time_range).
                base["defaultDataMask"] = {
                    "extraFormData": {"time_range": f.default},
                    "filterState": {"value": f.default},
                    "ownState": {},
                }
        out.append(base)
    return out


def _num(v: float) -> float | int:
    """8.0 -> 8: match how the Range plugin's own labels/values render integers."""
    return int(v) if v == int(v) else v


def _range_control_values(f) -> dict:
    """SingleValueType is a bare TS enum: the stored values are NUMERIC
    (Minimum=0, Exact=1, Maximum=2). A string here never hydrates."""
    cv: dict = {"enableEmptyFilter": False}
    if f.ge is not None and f.le is None:
        cv["enableSingleValue"] = 0
    elif f.ge is not None and f.le is not None and f.ge == f.le:
        cv["enableSingleValue"] = 1
    elif f.le is not None and f.ge is None:
        cv["enableSingleValue"] = 2
    return cv


def _range_default_mask(f) -> dict | None:
    """Mirror the plugin's getRangeExtraFormData + getLabel: the default needs
    BOTH the prebuilt query filters and the filterState, or it never applies."""
    if f.ge is None and f.le is None:
        return None
    ge = None if f.ge is None else _num(f.ge)
    le = None if f.le is None else _num(f.le)
    filters: list[dict] = []
    if ge is not None and ge != le:
        filters.append({"col": f.column, "op": ">=", "val": ge})
    if le is not None and le != ge:
        filters.append({"col": f.column, "op": "<=", "val": le})
    if ge is not None and le is not None and ge == le:
        filters.append({"col": f.column, "op": "==", "val": le})
    if ge is not None and le is not None:
        label = f"x = {le}" if ge == le else f"{ge} ≤ x ≤ {le}"
    elif le is not None:
        label = f"x ≤ {le}"
    else:
        label = f"x ≥ {ge}"
    return {
        "extraFormData": {"filters": filters},
        "filterState": {"value": [ge, le], "label": label},
        "ownState": {},
    }


def _dashboard_yaml(spec: DashboardSpec, resolution: Resolution) -> dict:
    metadata: dict = {
        "color_scheme": "",
        "cross_filters_enabled": False,
        "expanded_slices": {},
        "label_colors": {},
        "refresh_frequency": 0,
        "timed_refresh_immune_slices": [],
    }
    if spec.filters:
        metadata["native_filter_configuration"] = _native_filters(spec, resolution)
    return {
        "dashboard_title": spec.dashboard.title,
        "description": None,
        "css": "",
        "slug": spec.dashboard.slug,
        "certified_by": None,
        "certification_details": None,
        "published": True,
        "uuid": str(ids.dashboard_uuid(spec.dashboard.slug)),
        "position": _position(spec),
        "metadata": metadata,
        "version": "1.0.0",
    }


def compile_bundle(
    spec: DashboardSpec,
    resolution: Resolution,
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    """extra_files: round-tripped dataset/database YAMLs the importer requires,
    path -> bytes, relative to the bundle root."""
    files: dict[str, bytes] = {
        f"{BUNDLE_ROOT}/metadata.yaml": _yaml(
            {"version": "1.0.0", "type": "Dashboard", "timestamp": FIXED_TIMESTAMP}
        ),
        f"{BUNDLE_ROOT}/dashboards/{spec.dashboard.slug}.yaml": _yaml(_dashboard_yaml(spec, resolution)),
    }
    for chart in spec.charts:
        safe = "".join(ch if ch.isalnum() else "_" for ch in chart.name)
        files[f"{BUNDLE_ROOT}/charts/{safe}_{ids.chart_uuid(spec.dashboard.slug, chart.name).hex[:8]}.yaml"] = _yaml(
            _chart_yaml(chart, spec, resolution)
        )
    for rel, content in (extra_files or {}).items():
        files[f"{BUNDLE_ROOT}/{rel}"] = content

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.external_attr = 0o644 << 16
            info.create_system = 3  # pin to Unix: byte-stable across build platforms
            zf.writestr(info, files[name])
    return buf.getvalue()
