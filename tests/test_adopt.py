"""Adopt in place: a spec that takes over an existing, hand-built dashboard and
updates that same dashboard (same uuid, so the same id and address) instead of
building a copy (offline; apply and plan run against tests/fake_superset.py)."""

import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chartwright import apply as apply_mod
from chartwright import cli, dashdiff, ids, resolver
from chartwright.adopt import adopted_spec, first_apply_resets
from chartwright.apply import _ownership_guard, backup_dir_for
from chartwright.compiler import compile_bundle
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

from fake_superset import FakeSuperset
from test_decompile import _stub_lookup_for

FIXTURES = Path(__file__).parent / "fixtures"
SLUG = "sdc-sales-overview"
HAND_DASH = "11111111-2222-3333-4444-555555555555"


def _fixture() -> dict:
    return json.loads((FIXTURES / "sales_overview.json").read_text())


def _hand_built(extra_chart=False, slug=SLUG, edit=None, data=None) -> tuple[bytes, dict, object]:
    """A compiled bundle made to look built in the UI: every uuid random
    (deterministic here), optionally plus a chart type the spec can't hold."""
    spec = load_spec(data or _fixture())
    chart_uuids: dict[str, str] = {}

    def rewrite(path, doc):
        if "/dashboards/" in path:
            doc["uuid"] = HAND_DASH
            doc["slug"] = slug
            for node in doc["position"].values():
                if isinstance(node, dict) and node.get("type") == "CHART":
                    node["meta"]["uuid"] = chart_uuids.setdefault(
                        node["meta"]["sliceName"], str(uuid.uuid5(uuid.NAMESPACE_DNS, node["meta"]["sliceName"])))
        elif "/charts/" in path:
            doc["uuid"] = chart_uuids.setdefault(
                doc["slice_name"], str(uuid.uuid5(uuid.NAMESPACE_DNS, doc["slice_name"])))
        if edit:
            edit(path, doc)

    add = {}
    if extra_chart:
        add["sdc_bundle/charts/Sunburst.yaml"] = {
            "slice_name": "Sunburst", "viz_type": "sunburst_v2", "params": {},
            "uuid": "99999999-0000-0000-0000-000000000001", "dataset_uuid": "x"}
    return edit_bundle(compile_bundle(spec, stub_resolution(spec)), rewrite, add=add), chart_uuids, spec


# -- adopt (pure core) ----------------------------------------------------------


def test_decompile_reports_identity_skipped_charts_and_slug():
    hand, chart_uuids, spec = _hand_built(extra_chart=True)
    result = decompile_bundle(hand, _stub_lookup_for(spec))
    assert result.dashboard_uuid == HAND_DASH and result.source_slug == SLUG
    assert result.chart_uuids == chart_uuids
    assert result.skipped_charts == ["Sunburst"]


def test_adopt_refuses_unrepresentable_charts_unless_the_reset_is_accepted():
    hand, _, spec = _hand_built(extra_chart=True)
    result = decompile_bundle(hand, _stub_lookup_for(spec))
    refused = adopted_spec(result, hand)
    assert not refused.ok and "--accept-reset" in refused.detail
    assert any(r["where"] == "Sunburst" for r in refused.resets)
    assert refused.payload()["errors"][0]["code"] == "refused"
    assert adopted_spec(result, hand, accept_reset=True).ok


def test_adopt_refuses_shared_charts_unless_allowed_and_accept_reset_does_not_allow_them():
    hand, _, spec = _hand_built()
    result = decompile_bundle(hand, _stub_lookup_for(spec))
    refused = adopted_spec(result, shared_charts=["Total Sales"])
    assert not refused.ok and "--allow-shared" in refused.detail and refused.shared_charts == ["Total Sales"]
    assert not adopted_spec(result, accept_reset=True, shared_charts=["Total Sales"]).ok
    assert adopted_spec(result, shared_charts=["Total Sales"], allow_shared=True).ok


def test_adopt_refuses_a_skipped_chart_that_shares_a_title_even_when_resets_are_accepted():
    """apply tells charts apart by title, so a repeat that includes a chart the spec
    can't hold would make every apply refuse."""
    def twin(path, doc):
        if path.endswith("Sunburst.yaml"):
            doc["slice_name"] = "Total Orders"
    hand, _, spec = _hand_built(extra_chart=True)
    hand = edit_bundle(hand, twin)
    refused = adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec)), accept_reset=True)
    assert not refused.ok and "share a title" in refused.detail


@pytest.mark.parametrize("slug, message", [(None, "no URL name"), ("Sales_Overview", "isn't one a spec can hold")])
def test_adopt_refuses_a_dashboard_without_a_usable_address(slug, message):
    hand, _, spec = _hand_built(slug=slug)
    refused = adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec)))
    assert not refused.ok and message in refused.detail


def test_adopt_refuses_charts_that_share_a_title():
    def same_title(path, doc):
        if "/charts/" in path and doc["slice_name"] == "Total Sales":
            doc["slice_name"] = "Total Orders"
    hand, _, spec = _hand_built(edit=same_title)
    refused = adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec)))
    assert not refused.ok and "share a title" in refused.detail


def test_three_charts_with_one_title_get_distinct_names():
    def same_title(path, doc):
        if "/charts/" in path and doc["slice_name"] in ("Total Sales", "Average Order Value"):
            doc["slice_name"] = "Total Orders"
    hand, _, spec = _hand_built(edit=same_title)
    names = [c["name"] for c in decompile_bundle(hand, _stub_lookup_for(spec)).spec["charts"]]
    assert sorted(n for n in names if n.startswith("Total Orders")) == [
        "Total Orders", "Total Orders (2)", "Total Orders (3)"]


def test_adopted_spec_compiles_to_the_same_dashboard_and_charts():
    hand, chart_uuids, spec = _hand_built()
    adopted = adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec)))
    assert adopted.ok
    assert adopted.spec["dashboard"]["adopted"] == {"dashboard_uuid": HAND_DASH, "slug": SLUG,
                                                    "charts": chart_uuids}
    live = load_spec(adopted.spec)
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(live, stub_resolution(live))))
    docs = {n: yaml.safe_load(zf.read(n)) for n in zf.namelist() if n.endswith(".yaml")}
    dash = next(d for n, d in docs.items() if "/dashboards/" in n)
    assert dash["uuid"] == HAND_DASH
    assert {d["slice_name"]: d["uuid"] for n, d in docs.items() if "/charts/" in n} == chart_uuids


def test_a_chart_added_after_adoption_gets_a_derived_id():
    data = _fixture()
    data["dashboard"]["adopted"] = {"dashboard_uuid": HAND_DASH, "slug": SLUG,
                                   "charts": {"Total Orders": "aaaaaaaa-0000-0000-0000-000000000001"}}
    spec = load_spec(data)
    assert str(spec.chart_uuid("Total Orders")) == "aaaaaaaa-0000-0000-0000-000000000001"
    assert spec.chart_uuid("Total Sales") == ids.chart_uuid(SLUG, "Total Sales")
    assert str(spec.dashboard_uuid()) == HAND_DASH


@pytest.mark.parametrize("adopted, message", [
    ({"dashboard_uuid": "not-a-uuid", "slug": SLUG}, "not a uuid"),
    ({"dashboard_uuid": HAND_DASH, "slug": SLUG, "charts": {"Gone": HAND_DASH}}, "not in the spec"),
    ({"dashboard_uuid": HAND_DASH, "slug": SLUG,
      "charts": {"Total Orders": HAND_DASH, "Total Sales": HAND_DASH}}, "several names"),
    ({"dashboard_uuid": HAND_DASH, "slug": "the-original"}, "Remove dashboard.adopted"),
])
def test_adopted_block_is_validated(adopted, message):
    data = _fixture()
    data["dashboard"]["adopted"] = adopted
    with pytest.raises(ValidationError, match=message):
        load_spec(data)


def test_a_copy_of_an_adopted_spec_cannot_point_at_the_original():
    hand, _, spec = _hand_built()
    clone = json.loads(json.dumps(adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec))).spec))
    clone["dashboard"]["slug"] = "sales-overview-emea"
    with pytest.raises(ValidationError, match="Remove dashboard.adopted"):
        load_spec(clone)
    del clone["dashboard"]["adopted"]
    assert load_spec(clone).dashboard_uuid() == ids.dashboard_uuid("sales-overview-emea")


def test_resets_list_what_the_first_apply_resets_without_a_loss():
    """Decompile reads past these without a loss, so adopt checks the export itself."""
    def ui_state(path, doc):
        if "/dashboards/" in path:
            # a per-chart scope on a dashboard with cross-filtering off: decompile names it
            doc["metadata"]["chart_configuration"] = {"1": {"crossFilters": {
                "scope": {"rootPath": ["ROOT_ID"], "excluded": [2]}}}}
            doc["metadata"]["timed_refresh_immune_slices"] = [1]
            doc["metadata"]["native_filter_configuration"] = [
                {"id": "NATIVE_FILTER-ui", "name": "Region", "filterType": "filter_select",
                 "targets": [{}], "scope": {"rootPath": ["TAB-1"], "excluded": []}}]
            doc["position"]["TAB-1"] = {"type": "TAB", "id": "TAB-1", "children": [], "meta": {}}
        elif "/charts/" in path and doc["slice_name"] == "Total Sales":
            doc["query_context"] = '{"queries": []}'
    hand, _, spec = _hand_built(edit=ui_state)
    resets = first_apply_resets(decompile_bundle(hand, _stub_lookup_for(spec)), hand)
    whats = " | ".join(f"{r['where']}: {r['what']}" for r in resets)
    # Tab-scoped filters are decompile's own loss now (test_decompile.py).
    for needle in ("cross-filter scopes", "exempt from auto-refresh",
                   "saved queries of ['Total Sales']", "tab ids change", "native filters get"):
        assert needle in whats, needle


def test_a_dashboard_with_nothing_to_reset_adopts_without_accepting():
    hand, _, spec = _hand_built()
    result = decompile_bundle(hand, _stub_lookup_for(spec))
    adopted = adopted_spec(result, hand)
    assert adopted.ok and adopted.resets == [], adopted.resets


# -- apply / plan against a fake Superset ----------------------------------------


@pytest.fixture
def live(monkeypatch, tmp_path):
    """A fake Superset holding the hand-built dashboard, plus its adopted spec."""
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(apply_mod, "resolve", lambda spec, client, *a, **k: stub_resolution(spec))
    monkeypatch.setattr(resolver, "resolve", lambda spec, client, *a, **k: stub_resolution(spec))
    monkeypatch.setattr(apply_mod, "_roundtrip_dataset_files", lambda res, client: {})
    monkeypatch.setattr(apply_mod, "smoke", lambda *a, **k: [])

    def setup(edit=None, data=None, merge_links=False):
        hand, chart_uuids, spec = _hand_built(edit=edit, data=data)
        ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
        fake = FakeSuperset({ds.uuid: {"table_name": "cleaned_sales_data", "database_name": "examples"}},
                            merge_links=merge_links)
        fake.import_dashboard_bundle(hand)
        fake.log.clear()
        adopted = adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec)), accept_reset=True)
        assert adopted.ok, adopted.detail
        return fake, adopted.spec
    return setup


def _dash_id(fake):
    return fake.dashboards[HAND_DASH]["id"]


@pytest.mark.parametrize("merge_links", [False, True])
def test_apply_updates_the_same_dashboard_and_charts(live, merge_links):
    fake, data = live(merge_links=merge_links)
    before = fake.linked(_dash_id(fake))
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert report.ok, report.import_detail
    assert report.dashboard_id == _dash_id(fake) and report.backup
    assert fake.linked(_dash_id(fake)) == before
    assert fake.log == [f"overwrote dashboard {_dash_id(fake)}"]


def test_nothing_changes_when_the_adopted_dashboard_has_moved(live):
    fake, data = live()
    fake.dashboards[HAND_DASH]["slug"] = "moved-elsewhere"
    spec = load_spec(data)
    report = apply_mod.apply(spec, fake, "prod")
    assert not report.ok and "no longer at" in report.import_detail
    assert fake.log == [] and report.backup is None
    assert dashdiff.plan(spec, fake).dashboard == "blocked"


def test_a_moved_copy_cannot_reach_the_original(live):
    """A find-and-replace of the slug changes adopted.slug too, which passes
    validation; apply must still refuse, since nothing is at the new address."""
    fake, data = live()
    text = json.dumps(data).replace(f'"{SLUG}"', '"sales-v2"')
    report = apply_mod.apply(load_spec(json.loads(text)), fake, "prod")
    assert not report.ok and "no longer at" in report.import_detail and fake.log == []


def test_renaming_an_adopted_chart_keeps_the_chart(live):
    fake, data = live()
    old, new = "Total Orders", "Orders (all time)"
    chart_id = fake.linked(_dash_id(fake))[old]
    data = json.loads(json.dumps(data).replace(f'"{old}"', f'"{new}"'))
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert report.ok, report.import_detail
    assert fake.linked(_dash_id(fake))[new] == chart_id and old not in fake.linked(_dash_id(fake))
    assert not any("took charts" in w for w in report.warnings)


def _drop(data, name):
    data["charts"] = [c for c in data["charts"] if c["name"] != name]
    data["layout"]["rows"] = [r for r in ([x for x in row if x != name] for row in data["layout"]["rows"]) if r]
    data["dashboard"]["adopted"]["charts"].pop(name, None)
    return data


@pytest.mark.parametrize("merge_links", [False, True])
def test_a_dropped_chart_leaves_the_dashboard_but_not_other_dashboards(live, merge_links):
    fake, data = live(merge_links=merge_links)
    chart_id = fake.linked(_dash_id(fake))["Sales by Product Line"]
    fake.charts[chart_id]["dashboards"].add(999)  # also on another dashboard
    report = apply_mod.apply(load_spec(_drop(data, "Sales by Product Line")), fake, "prod")
    assert report.ok, report.import_detail
    assert chart_id in fake.charts and fake.charts[chart_id]["dashboards"] == {999}
    assert any("not deleted" in w for w in report.warnings)


def test_a_chart_added_after_adoption_is_deleted_when_dropped(live):
    fake, data = live()
    data["charts"].append(dict(next(c for c in data["charts"] if c["name"] == "Total Sales"), name="Extra"))
    data["layout"]["rows"].append(["Extra"])
    assert apply_mod.apply(load_spec(data), fake, "prod").ok
    extra_id = fake.linked(_dash_id(fake))["Extra"]
    report = apply_mod.apply(load_spec(_drop(data, "Extra")), fake, "prod")
    assert report.ok and extra_id not in fake.charts


def test_a_chart_uuid_from_elsewhere_is_refused(live):
    fake, data = live()
    fake.next_id += 1
    fake.charts[fake.next_id] = {"uuid": "abababab-0000-0000-0000-000000000001", "slice_name": "Elsewhere",
                                 "params": {}, "viz_type": "pie", "dataset_uuid": None, "dashboards": {999}}
    data["dashboard"]["adopted"]["charts"]["Total Sales"] = "abababab-0000-0000-0000-000000000001"
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert not report.ok and "not on the adopted dashboard" in report.import_detail
    assert fake.log == [] and fake.charts[fake.next_id]["dashboards"] == {999}


def test_a_refused_unlink_stops_at_linkage_with_the_backup(live):
    """A merging release keeps the dropped chart linked through the import; the layout
    PUT that takes it off is refused. As for any failure after the import, the new
    version stays live and the report carries the backup."""
    fake, data = live(merge_links=True)
    fake.refuse_dashboard_puts = True
    report = apply_mod.apply(load_spec(_drop(data, "Sales by Product Line")), fake, "prod")
    assert not report.ok and report.stage == "linkage"
    assert "HTTP 403" in report.import_detail and report.backup


def test_plan_of_a_freshly_adopted_dashboard_is_clean(live):
    fake, data = live()
    p = dashdiff.plan(load_spec(data), fake)
    assert p.clean, p.to_json()


def test_plan_lists_native_filters_that_get_new_ids(live):
    data = _fixture()
    data["filters"] = [{"type": "select", "name": "Deal", "column": "deal_size",
                        "dataset": data["charts"][0]["dataset"]}]

    def renumber(path, doc):
        if "/dashboards/" in path:
            doc["metadata"]["native_filter_configuration"][0]["id"] = "NATIVE_FILTER-made-in-the-ui"
    fake, spec_data = live(edit=renumber, data=data)
    p = dashdiff.plan(load_spec(spec_data), fake)
    assert "Deal" in p.filters_changed and not p.clean


def test_plan_names_charts_whose_options_the_first_apply_resets(live):
    def custom(path, doc):
        if "/charts/" in path and doc["slice_name"] == "Sales Over Time":
            doc["params"]["my_custom_option"] = 1
    fake, data = live(edit=custom)
    p = dashdiff.plan(load_spec(data), fake)
    assert "Sales Over Time" in p.charts_changed and not p.clean


def test_apply_warns_when_an_adopted_chart_sits_on_another_dashboard(live):
    fake, data = live()
    fake.charts[fake.linked(_dash_id(fake))["Total Sales"]]["dashboards"].add(999)
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert report.ok and any("other dashboards" in w and "Total Sales" in w for w in report.warnings)


def test_a_tool_chart_taken_off_in_the_ui_is_still_updated(live):
    fake, data = live()
    data["charts"].append(dict(next(c for c in data["charts"] if c["name"] == "Total Sales"), name="Extra"))
    data["layout"]["rows"].append(["Extra"])
    assert apply_mod.apply(load_spec(data), fake, "prod").ok
    extra_id = fake.linked(_dash_id(fake))["Extra"]
    fake.charts[extra_id]["dashboards"].discard(_dash_id(fake))  # removed from the dashboard in the UI
    next(c for c in data["charts"] if c["name"] == "Extra")["metric"] = "MAX(sales)"
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert report.ok, report.import_detail
    assert "MAX(sales)" in json.dumps(fake.charts[extra_id]["params"])


def test_restore_finds_a_chart_renamed_since_the_backup(live):
    fake, data = live()
    assert apply_mod.apply(load_spec(data), fake, "prod").ok
    chart_id = fake.linked(_dash_id(fake))["Total Orders"]
    renamed = json.loads(json.dumps(data).replace('"Total Orders"', '"Orders (all time)"'))
    report = apply_mod.apply(load_spec(renamed), fake, "prod")
    assert report.ok and fake.charts[chart_id]["slice_name"] == "Orders (all time)"
    restored = apply_mod.restore_bundle(Path(report.backup).read_bytes(), SLUG, fake)
    assert restored.ok
    assert fake.charts[chart_id]["slice_name"] == "Total Orders"


def test_adopt_live_spots_shared_charts(live):
    from chartwright.adopt import adopt_live

    fake, _ = live()
    fake.charts[fake.linked(_dash_id(fake))["Total Sales"]]["dashboards"].add(999)
    result = adopt_live(SLUG, fake)
    assert not result.ok and result.shared_charts == ["Total Sales"]
    assert adopt_live(SLUG, fake, allow_shared=True).ok


def test_adopt_refuses_an_all_digit_address():
    """Superset reads /dashboard/2024/ as dashboard id 2024, not the slug "2024"."""
    hand, _, spec = _hand_built(slug="2024")
    refused = adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec)))
    assert not refused.ok and "all digits" in refused.detail


def test_plan_sees_chart_options_decompile_ignores(live):
    """Rolling sums, forecasts and annotations change the numbers shown; apply would
    drop them, so plan must not call the dashboard clean."""
    def analytics(path, doc):
        if "/charts/" in path and doc["slice_name"] == "Sales Over Time":
            doc["params"].update(rolling_type="cumsum", forecastEnabled=True,
                                 annotation_layers=[{"name": "launch"}], show_legend=False)
    fake, data = live(edit=analytics)
    p = dashdiff.plan(load_spec(data), fake)
    assert "Sales Over Time" in p.charts_changed and not p.clean


def test_plan_counts_filters_apply_would_remove(live):
    def extras(path, doc):
        if "/dashboards/" in path:
            doc["metadata"]["native_filter_configuration"] = [
                {"id": "NATIVE_FILTER-ui", "name": "Grain", "filterType": "filter_timegrain", "targets": [{}]}]
            grid = doc["position"]["GRID_ID"]
            doc["position"]["HEADER-ui"] = {"type": "HEADER", "id": "HEADER-ui", "children": [],
                                           "parents": ["ROOT_ID", "GRID_ID"], "meta": {"text": "Sales"}}
            grid["children"].insert(0, "HEADER-ui")
    fake, data = live(edit=extras)
    p = dashdiff.plan(load_spec(data), fake)
    # The header is a spec header row now, so only the filter is lost.
    assert "Grain" in p.filters_removed and not p.layout_changed and not p.clean


def test_plan_reads_a_renamed_adopted_chart_as_a_change(live):
    fake, data = live()
    renamed = json.loads(json.dumps(data).replace('"Total Orders"', '"Orders (all time)"'))
    p = dashdiff.plan(load_spec(renamed), fake)
    assert p.charts_changed == ["Orders (all time)"] and not p.charts_added and not p.charts_removed


def test_plan_reads_a_scope_naming_a_renamed_chart_as_unchanged(live):
    """A cross-filter scope names its charts; after a rename in the adopted spec, the live
    scope names the same chart under its old title, and the scope itself hasn't changed."""
    data = _fixture()
    data["dashboard"]["cross_filters"] = True
    data["charts"][3]["cross_filter_scope"] = ["Sales by Deal Size"]  # Sales Over Time
    fake, adopted = live(data=data)
    # the scope under the live chart ids, as apply writes it (the fake imports ids as 4.1.4 does)
    assert apply_mod._apply_cross_filter_scopes(load_spec(adopted), fake, _dash_id(fake)) == []
    assert dashdiff.plan(load_spec(adopted), fake).clean
    renamed = json.loads(json.dumps(adopted).replace('"Sales by Deal Size"', '"Deal size mix"'))
    p = dashdiff.plan(load_spec(renamed), fake)
    assert p.charts_changed == ["Deal size mix"] and not p.charts_added and not p.charts_removed


def test_plan_blocks_where_apply_would_refuse(live):
    fake, data = live()
    fake.charts[fake.linked(_dash_id(fake))["Total Sales"]]["dashboards"].clear()  # taken off in the UI
    p = dashdiff.plan(load_spec(data), fake)
    assert p.dashboard == "blocked" and "not on the adopted dashboard" in p.detail


def test_a_derived_id_cannot_collide_with_an_adopted_one():
    data = _fixture()
    data["dashboard"]["adopted"] = {"dashboard_uuid": HAND_DASH, "slug": SLUG,
                                   "charts": {"Total Orders": str(ids.chart_uuid(SLUG, "Total Sales"))}}
    with pytest.raises(ValidationError, match="same id as an adopted chart"):
        load_spec(data)


def test_adopted_uuids_are_normalised():
    data = _fixture()
    data["dashboard"]["adopted"] = {"dashboard_uuid": HAND_DASH.upper().replace("-", ""), "slug": SLUG,
                                   "charts": {"Total Orders": "AAAAAAAA-0000-0000-0000-000000000001"}}
    adopted = load_spec(data).dashboard.adopted
    assert adopted.dashboard_uuid == HAND_DASH
    assert adopted.charts["Total Orders"] == "aaaaaaaa-0000-0000-0000-000000000001"


def test_a_failed_chart_update_after_the_import_restores(live):
    """The restore can't put chart params back either (the same PUTs are refused), so
    the report must say the auto-restore failed, never that it succeeded."""
    fake, data = live()
    fake.refuse_chart_puts = True
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert not report.ok and "HTTP 403" in report.import_detail
    assert any("auto-restore also failed" in w for w in report.warnings)
    assert not any("AUTO-RESTORED" in w for w in report.warnings)


def test_a_tool_chart_used_elsewhere_is_taken_off_not_deleted(live):
    fake, data = live()
    data["charts"].append(dict(next(c for c in data["charts"] if c["name"] == "Total Sales"), name="Extra"))
    data["layout"]["rows"].append(["Extra"])
    assert apply_mod.apply(load_spec(data), fake, "prod").ok
    extra_id = fake.linked(_dash_id(fake))["Extra"]
    fake.charts[extra_id]["dashboards"].add(999)
    report = apply_mod.apply(load_spec(_drop(data, "Extra")), fake, "prod")
    assert report.ok and extra_id in fake.charts and fake.charts[extra_id]["dashboards"] == {999}


def test_refusals_never_print_a_uuid(live):
    fake, data = live()
    plain = json.loads(json.dumps(data))
    del plain["dashboard"]["adopted"]
    message = _ownership_guard(load_spec(plain), fake)
    assert "chartwright adopt" in message and HAND_DASH not in message


def _query_context(fake, name):
    return fake.charts[fake.linked(_dash_id(fake))[name]]["query_context"]


def test_a_chart_apply_leaves_unchanged_keeps_its_saved_query(live):
    """CSV and text reports and the chart data API read a chart's saved query; an
    in-place update that writes the same options must not clear it."""
    def saved(path, doc):
        if "/charts/" in path:
            doc["query_context"] = '{"queries": [{"saved": true}]}'
    fake, data = live(edit=saved)
    next(c for c in data["charts"] if c["name"] == "Total Sales")["metric"] = "MAX(sales)"
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert report.ok, report.import_detail
    assert _query_context(fake, "Total Orders") == '{"queries": [{"saved": true}]}'
    assert _query_context(fake, "Total Sales") is None     # its query changed: stale


def test_restore_puts_the_saved_queries_back(live):
    def saved(path, doc):
        if "/charts/" in path:
            doc["query_context"] = '{"queries": [{"saved": true}]}'
    fake, data = live(edit=saved)
    next(c for c in data["charts"] if c["name"] == "Total Sales")["metric"] = "MAX(sales)"
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert _query_context(fake, "Total Sales") is None
    assert apply_mod.restore_bundle(Path(report.backup).read_bytes(), SLUG, fake).ok
    assert _query_context(fake, "Total Sales") == '{"queries": [{"saved": true}]}'


def test_plan_treats_a_missing_annotation_layers_as_empty(live):
    """Superset's import and export fill in annotation_layers: [] where the compiler
    leaves it out (the adopt CI diagnostic); that is no change."""
    def filled(path, doc):
        if "/charts/" in path:
            doc["params"].setdefault("annotation_layers", [])
    fake, data = live(edit=filled)
    p = dashdiff.plan(load_spec(data), fake)
    assert p.clean, p.to_json()


# -- restore --------------------------------------------------------------------


def _restore_dies_not_owned(capsys, path) -> bool:
    with pytest.raises(SystemExit):
        cli.main(["restore", str(path), "--profile", "prod"])
    return "not_owned" in capsys.readouterr().out


def test_restore_accepts_only_the_tools_own_backup_of_that_dashboard(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path / "backups"))
    hand, _, _ = _hand_built()  # random uuid: not tool-owned by derivation

    elsewhere = tmp_path / "elsewhere.zip"
    elsewhere.write_bytes(hand)
    assert _restore_dies_not_owned(capsys, elsewhere)
    for profile, slug in [("staging", SLUG), ("prod", "another-dashboard")]:
        folder = backup_dir_for(profile, slug)
        folder.mkdir(parents=True)
        (folder / "b.zip").write_bytes(hand)
        assert _restore_dies_not_owned(capsys, folder / "b.zip"), (profile, slug)

    def dotdot(path, doc):
        if "/dashboards/" in path:
            doc["slug"] = ".."
    profile_root = backup_dir_for("prod", "x").parent
    profile_root.mkdir(parents=True, exist_ok=True)
    (profile_root / "s.zip").write_bytes(edit_bundle(hand, dotdot))
    assert _restore_dies_not_owned(capsys, profile_root / "s.zip")

    own = backup_dir_for("prod", SLUG)
    own.mkdir(parents=True)
    (own / "20261002T120000.zip").write_bytes(hand)

    class Reached(Exception):
        pass

    def no_network(_profile):
        raise Reached

    monkeypatch.setattr(cli, "_client", no_network)
    with pytest.raises(SystemExit):  # passed the ownership check, went on to sign in
        cli.main(["restore", str(own / "20261002T120000.zip"), "--profile", "prod"])
    out = capsys.readouterr().out
    assert "Reached" in out and "not_owned" not in out


# -- review fixes: a real UI export, metadata, partial restores ------------------------


def test_a_real_ui_export_lists_the_options_decompile_reads_past():
    """Decompile drops some options with no loss (rolling, forecasts, sorts...). The
    first apply rewrites them, so adopt must list them; a chartwright-compiled
    "hand-built" bundle can't show this, a genuine UI export does."""
    from chartwright.adopt import option_changes_for
    from chartwright.decompile import _IGNORABLE

    def slugged(path, doc):
        if "/dashboards/" in path:
            doc["slug"] = "featured-charts"
    blob = edit_bundle((FIXTURES / "featured_charts_export.zip").read_bytes(), slugged)
    result = decompile_bundle(blob, lambda u: {"database": "examples", "schema": None, "table": "t"})
    draft = adopted_spec(result, blob, accept_reset=True, allow_shared=True)
    assert draft.ok, draft.detail
    spec = load_spec(draft.spec)
    changes = option_changes_for(draft.spec, stub_resolution(spec), blob)
    silent = {k for keys in changes.values() for k in keys} & set(_IGNORABLE)
    assert silent, changes
    refused = adopted_spec(result, blob, option_changes=changes)
    assert not refused.ok and "--accept-reset" in refused.detail
    rewritten = {r["where"] for r in refused.resets if "rewritten" in r["what"]}
    assert rewritten == set(changes)


def test_plan_lists_dashboard_metadata_the_first_apply_replaces(live):
    def ui_meta(path, doc):
        if "/dashboards/" in path:
            doc["metadata"].update(
                expanded_slices={"1": True}, stagger_refresh=False,
                global_chart_configuration={"scope": {"rootPath": ["ROOT_ID"], "excluded": [5]}},
                chart_configuration={"1": {}}, timed_refresh_immune_slices=[1],
                color_namespace="ns")
    fake, data = live(edit=ui_meta)
    p = dashdiff.plan(load_spec(data), fake)
    assert not p.clean
    assert {"expanded_slices", "stagger_refresh", "global_chart_configuration",
            "timed_refresh_immune_slices", "color_namespace"} <= set(p.dashboard_settings_changed)
    # per-chart scopes are each chart's cross_filter_scope now, and this one sets none
    assert "chart_configuration" not in p.dashboard_settings_changed
    hand, _, spec = _hand_built(edit=ui_meta)
    whats = " ".join(r["what"] for r in first_apply_resets(
        decompile_bundle(hand, _stub_lookup_for(spec)), hand))
    for needle in ("expanded", "staggered", "dashboard-wide cross-filter", "colour namespace"):
        assert needle in whats, needle


def test_a_restore_that_cannot_put_chart_params_back_is_not_ok(live):
    fake, data = live()
    next(c for c in data["charts"] if c["name"] == "Total Sales")["metric"] = "MAX(sales)"
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert report.ok
    fake.refuse_chart_puts = True
    restored = apply_mod.restore_bundle(Path(report.backup).read_bytes(), SLUG, fake)
    assert not restored.ok and "Total Sales" in restored.import_detail
