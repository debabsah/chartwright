"""A chart added to a chartwright dashboard in Superset (triage H). 4.1.4 and 5.0.0 merge
a dashboard's chart links on import, so the chart stayed linked and apply failed at
linkage after the new version was live; 6.1.0 unlinks it without a word. apply now
takes it off the dashboard on every release, never deletes it, and says so. Live
coverage: tools/ci_live_ui_chart.py on all three releases."""

import json

import pytest

import chartwright.apply as ap
from chartwright.resolver import Resolution
from chartwright.spec import load_spec

SPEC = {
    "spec_version": "1",
    "dashboard": {"title": "UI chart", "slug": "ui-chart"},
    "charts": [{"name": "Orders", "type": "big_number_total", "metric": "COUNT(*)",
                "dataset": {"database": "warehouse", "table": "orders"}}],
    "layout": {"rows": [["Orders"]]},
}
OURS = {"id": 11, "slice_name": "Orders"}
UI = {"id": 99, "slice_name": "Added in UI"}
METADATA = {"color_scheme": "bnbColors", "native_filter_configuration": [], "label_colors": {"a": "#111"}}
POSITIONS = {"DASHBOARD_VERSION_KEY": "v2", "CHART-1": {"type": "CHART", "meta": {"chartId": 11}}}


class Client:
    """A dashboard whose links follow the release: `merge` keeps the UI chart linked
    through the import (4.1.4, 5.0.0), otherwise the import drops it (6.1.0)."""

    base_url = "http://superset.test"

    def __init__(self, merge: bool, put_status: int = 200):
        self.merge, self.put_status = merge, put_status
        self.linked = [OURS, UI]
        self.puts: list[tuple[str, dict]] = []
        self.deleted: list[int] = []

    def find_dashboard_by_slug(self, slug):
        return {"id": 7}

    def export_dashboard(self, did):
        return b"backup-zip"

    def dashboard_charts(self, did):
        return list(self.linked)

    def charts_by_uuids(self, mapping):
        return {u: OURS for u, n in mapping.items() if n == "Orders"}

    def delete_chart(self, cid):
        self.deleted.append(cid)

    def import_dashboard_bundle(self, bundle, overwrite=True):
        if not self.merge:
            self.linked = [OURS]

        class R:
            status_code = 200
            text = ""
        return R()

    def get(self, path, **params):
        return {"result": {"json_metadata": json.dumps(METADATA),
                           "position_json": json.dumps(POSITIONS)}}

    def put_json(self, path, payload):
        self.puts.append((path, payload))
        if path.startswith("/api/v1/chart/"):
            status = 200
        else:
            status = self.put_status
        if status == 200 and "json_metadata" in payload:
            chart_ids = {v["meta"]["chartId"] for v in
                         json.loads(payload["json_metadata"])["positions"].values()
                         if isinstance(v, dict)}
            self.linked = [c for c in self.linked if c["id"] in chart_ids]

        class R:
            status_code = status
            text = "boom"
        return R()


@pytest.fixture(autouse=True)
def flow(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(ap, "resolve", lambda spec, client, *_: Resolution())
    monkeypatch.setattr(ap, "_ownership_guard", lambda spec, client: None)
    monkeypatch.setattr(ap, "_roundtrip_dataset_files", lambda res, client: {})
    monkeypatch.setattr(ap, "compile_bundle", lambda spec, res, extra_files=None: b"bundle")
    monkeypatch.setattr(ap, "_update_owned_charts_in_place", lambda *a, **k: ([], []))
    monkeypatch.setattr(ap, "_apply_filter_scopes", lambda spec, client, did: [])
    monkeypatch.setattr(ap, "smoke", lambda spec, res, client: [])


def _warning(rep):
    return [w for w in rep.warnings if "off the dashboard" in w]


def test_a_merging_release_takes_the_chart_off_with_the_layout():
    c = Client(merge=True)
    rep = ap.apply(load_spec(SPEC), c, "dev")
    assert rep.ok and rep.stage == "done", rep.import_detail
    assert c.linked == [OURS] and c.deleted == []          # unlinked, never deleted
    [(path, payload)] = c.puts
    sent = json.loads(payload["json_metadata"])
    assert path == "/api/v1/dashboard/7"
    assert sent == {**METADATA, "positions": POSITIONS}    # the whole metadata goes along
    [w] = _warning(rep)
    assert "'Added in UI'" in w and "not deleted" in w and "add it to the spec" in w


def test_an_unlinking_release_needs_no_put_and_still_says_so():
    c = Client(merge=False)
    rep = ap.apply(load_spec(SPEC), c, "dev")
    assert rep.ok and c.puts == []
    [w] = _warning(rep)
    assert "'Added in UI'" in w


def test_a_refused_unlink_fails_at_linkage_and_names_the_cause():
    c = Client(merge=True, put_status=403)
    rep = ap.apply(load_spec(SPEC), c, "dev")
    assert not rep.ok and rep.stage == "linkage"
    assert "HTTP 403" in rep.import_detail and "added in Superset" in rep.import_detail
    assert rep.backup


def test_a_dashboard_with_only_its_own_charts_is_left_alone():
    c = Client(merge=True)
    c.linked = [OURS]
    rep = ap.apply(load_spec(SPEC), c, "dev")
    assert rep.ok and c.puts == [] and _warning(rep) == []


def test_restore_matches_the_backups_chart_links(monkeypatch):
    """A failed apply's new chart, or a UI chart, stays linked through a restore on
    4.1.4 and 5.0.0 unless the restore saves the backup's layout too."""
    c = Client(merge=True)
    monkeypatch.setattr(ap, "bundle_dataset_ids", lambda data, client: {})
    monkeypatch.setattr(ap, "chart_payloads_from_bundle",
                        lambda data, ids: {"u1": {"slice_name": "Orders", "params": "{}"}})
    import chartwright.decompile as dec
    monkeypatch.setattr(dec, "decompile_bundle", lambda data, lookup: type("D", (), {"spec": SPEC})())
    monkeypatch.setattr(dec, "live_dataset_lookup", lambda client: None)
    rep = ap.restore_bundle(b"backup-zip", "ui-chart", c)
    assert rep.ok and c.linked == [OURS] and c.deleted == []
    assert any("backup doesn't have" in w and "'Added in UI'" in w for w in rep.warnings)
