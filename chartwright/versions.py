"""Spec fields that only some Superset releases take, and the check that holds a
spec to the release it is going to.

The compiler writes one bundle for every release, so it never knows the target.
`check`, `apply` and `plan` do: they ask the instance its version
(SupersetClient.superset_version) and refuse, at the resolve stage and before
anything is written, a spec that uses a field the release cannot take safely.
Fields an older release merely ignores are warnings. `compile --superset-version`
runs the same check offline.

GATED_FIELDS is the one registry. Each entry names the first release that takes
the field, what an older release does with it, and the Superset source that
shows it (docs/CONTRACTS.md has the citations in full).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

from .spec import DashboardSpec

Release = tuple[int, int, int]

_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text) -> Release | None:
    """(major, minor, patch) from a Superset version string ("5.0.0", "4.1.4rc1",
    "6.1"), or None when it names no release. A development build reports
    0.0.0 (superset-frontend/package.json on master), which is no release."""
    m = _VERSION_RE.match(str(text)) if text is not None else None
    if not m:
        return None
    release = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    return None if release[0] == 0 else release


def format_version(release: Release) -> str:
    return ".".join(str(n) for n in release)


def stated_release(text) -> str | None:
    """A release someone stated (--superset-version, the MCP tools' superset_version)
    in canonical form, "v5.0" -> "5.0.0", or None when the text names no release."""
    release = parse_version(text)
    return format_version(release) if release else None


def not_a_release(text) -> str:
    return f"{text!r} is not a Superset release, e.g. 5.0.0"


@dataclass(frozen=True)
class GatedField:
    field: str                              # the spec field, as a spec writes it
    since: str                              # the first release that takes it safely
    severity: Literal["error", "warn"]      # error: older releases break; warn: they ignore it
    before: str                             # what a release before `since` does with it
    source: str                             # where Superset's source shows it
    used_by: Callable[[DashboardSpec], list[str | None]]  # chart names using it; None = the dashboard
    # A warning's own sentence, for a field whose effect is not "taken or ignored":
    # str.format with where, since, runs (who runs what) and that (the release).
    warning: str | None = None


def _tags(spec: DashboardSpec) -> list[str | None]:
    # [] counts: the compiler writes `tags: []` to clear them, and the key alone fails.
    return ([None] if spec.dashboard.tags is not None else []) + [
        c.name for c in spec.charts if c.tags is not None]


def _charts(predicate) -> Callable[[DashboardSpec], list[str | None]]:
    return lambda spec: [c.name for c in spec.charts if predicate(c)]


GATED_FIELDS: tuple[GatedField, ...] = (
    GatedField(
        "tags", "6.0.0", "error",
        "rejects the import bundle, so the import fails: the dashboard and chart import "
        "schemas have no tags field before 6.0.0",
        "ImportV1DashboardSchema / ImportV1ChartSchema: tags at 6.0.0 dashboards/schemas.py:502, "
        "charts/schemas.py:1589 and 6.1.0 :519, :1627; absent at 4.1.4 and 5.0.0",
        _tags,
    ),
    GatedField(
        "theme", "6.0.0", "error",
        "rejects the import bundle, so the import fails: the dashboard import schema has no "
        "theme field and the instance has no themes before 6.0.0",
        "ImportV1DashboardSchema theme_uuid/theme_id at 6.0.0 dashboards/schemas.py:503-504 "
        "and 6.1.0 :520-521; Dashboard.theme_id at 6.0.0 models/dashboard.py:139 and 6.1.0 "
        ":140; absent at 4.1.4 and 5.0.0",
        lambda spec: [None] if spec.dashboard.theme is not None else [],
    ),
    GatedField(
        "show_chart_timestamps", "6.1.0", "error",
        "imports it, then refuses every later save of the dashboard's settings (its "
        "metadata schema rejects the key), apply's filter-scope step included",
        "DashboardJSONMetadataSchema declares show_chart_timestamps at 6.1.0 "
        "dashboards/schemas.py:167, not at 4.1.4, 5.0.0 or 6.0.0; validate_json_metadata "
        ":107-116 at 4.1.4",
        lambda spec: [None] if spec.dashboard.show_chart_timestamps else [],
    ),
    GatedField(
        # A time axis (every timeseries chart, a mixed chart over a time column) needs
        # force_max_interval, new in 6.1.0; a mixed chart's category axis needs only
        # xAxisLabelInterval, which 6.0.0 reads. The gate takes the later release.
        "x_label_every", "6.1.0", "warn",
        "ignores it on a time axis: the axis keeps Superset's automatic label spacing "
        "(a mixed chart's category axis takes it from 6.0.0)",
        "force_max_interval is a 6.1.0 control (controls.tsx:389, Timeseries and "
        "MixedTimeseries controlPanel.tsx); xAxisLabelInterval is at 6.0.0 controls.tsx:305; "
        "neither at 4.1.4 or 5.0.0",
        # A heatmap's x_label_every is its own xscale_interval, which every release reads
        # (Heatmap/controlPanel.tsx 4.1.4 :130, 5.0.0 :128, 6.1.0 :155).
        _charts(lambda c: c.type != "heatmap" and getattr(c, "x_label_every", False)),
    ),
    GatedField(
        "subtitle", "6.0.0", "warn",
        "ignores a trendline KPI's subtitle: nothing shows under the number",
        "BigNumberWithTrendline controlPanel.tsx subtitleControl at 6.0.0 :33,144 "
        "(read at transformProps.ts:96); absent at 4.1.4 and 5.0.0",
        _charts(lambda c: c.type == "big_number_trend" and c.subtitle),
    ),
    GatedField(
        "column_headers", "6.0.0", "warn",
        "ignores it: the table's headers show the labels",
        "customColumnName read at 6.0.0 plugin-chart-table/src/TableChart.tsx:806 "
        "(6.1.0 :859); absent at 4.1.4 and 5.0.0",
        _charts(lambda c: c.type == "table" and c.column_headers),
    ),
    GatedField(
        # Keyed on what the author writes: show_value on a stacked query. only_total
        # defaults to true, so naming it would point at a field the spec never set;
        # written false, it asks for every segment, which every release draws.
        "show_value", "6.0.0", "warn",
        "labels every segment of a stacked mixed chart",
        "onlyTotal / onlyTotalB read at 6.0.0 MixedTimeseries/transformProps.ts:178-179 "
        "(6.1.0 :186-187); absent at 4.1.4 and 5.0.0",
        _charts(lambda c: c.type == "mixed" and any(
            s.show_value and s.stack and s.only_total for s in (c.a, c.b))),
        warning="show_value on a stacked query of {where} labels each stack's total from "
                "Superset {since}; {runs}, and {that} labels every segment of a stacked "
                "mixed chart. Set only_total: false for the same labels on every release.",
    ),
)


@dataclass
class VersionCheck:
    version: str | None                         # the release checked against; None = unknown
    errors: list[dict]                          # ResolutionError fields: code, chart, ref, detail
    warnings: list[dict]

    @property
    def ok(self) -> bool:
        return not self.errors


def gated_fields_used(spec: DashboardSpec) -> list[tuple[GatedField, list[str | None]]]:
    return [(g, users) for g in GATED_FIELDS if (users := g.used_by(spec))]


def check_spec_version(spec: DashboardSpec, version: str | None,
                       uses: list[tuple[GatedField, list[str | None]]] | None = None) -> VersionCheck:
    """Hold a spec to a Superset release. An unknown version (None, or a string that
    names no release) cannot vouch for an error-level field, so each one is refused
    with a pointer to --superset-version; warn-level fields still warn."""
    uses = gated_fields_used(spec) if uses is None else uses
    release = parse_version(version)
    shown = format_version(release) if release else None
    out = VersionCheck(shown, [], [])
    for g, users in uses:
        since = parse_version(g.since)
        if release is not None and release >= since:
            continue
        for chart in users:
            where = "the dashboard" if chart is None else f"chart {chart!r}"
            if g.severity == "error":
                if release is None:
                    out.errors.append({
                        "code": "superset_version_unknown", "chart": chart, "ref": g.field,
                        "detail": f"{g.field} on {where} needs Superset {g.since} or later, and the "
                                  f"instance did not report its version. A release before "
                                  f"{g.since} {g.before}. Pass --superset-version (or the MCP "
                                  "tool's superset_version) with the instance's release, or "
                                  "remove the field."})
                else:
                    out.errors.append({
                        "code": "superset_version_too_old", "chart": chart, "ref": g.field,
                        "detail": f"{g.field} on {where} needs Superset {g.since} or later; this "
                                  f"instance runs {shown}, which {g.before}. Remove the field "
                                  f"for this instance."})
            else:
                runs = f"this instance runs {shown}" if release else "the instance did not report its version"
                if g.warning:
                    detail = g.warning.format(
                        where=where, since=g.since, runs=runs,
                        that="this release" if release else f"a release before {g.since}")
                else:
                    detail = (f"{g.field} on {where} takes effect on Superset {g.since} or "
                              f"later; {runs}, and a release before {g.since} {g.before}.")
                out.warnings.append({
                    "code": "field_ignored_before_version", "chart": chart, "field": g.field,
                    "since": g.since, "detail": detail})
    return out
