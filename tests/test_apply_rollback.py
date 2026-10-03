"""When apply fails partway, the backup goes back on the same rule for every
error type: a failure in the prepare or import step (chart updates included)
restores it; a later failure leaves the new version live. Either way the
report carries the backup path. Live coverage of the same rule, on all three
Superset releases, is tools/faultline.py."""

import pytest

import chartwright.apply as ap
from chartwright.resolver import Resolution
from chartwright.spec import load_spec

SPEC = {
    "spec_version": "1",
    "dashboard": {"title": "Rollback", "slug": "rollback"},
    "charts": [{"name": "Orders", "type": "big_number_total", "metric": "COUNT(*)",
                "dataset": {"database": "warehouse", "table": "orders"}}],
    "layout": {"rows": [["Orders"]]},
}


class StubClient:
    """Just enough of SupersetClient for apply's flow, with an existing dashboard."""

    base_url = "http://superset.test"

    def __init__(self, crash_on: str | None = None):
        self.crash_on = crash_on

    def _maybe_crash(self, name):
        if self.crash_on == name:
            raise RuntimeError(f"bug in {name}")

    def find_dashboard_by_slug(self, slug):
        return {"id": 7}

    def export_dashboard(self, did):
        return b"backup-zip"

    def dashboard_charts(self, did):
        self._maybe_crash("dashboard_charts")
        return [{"slice_name": "Orders"}]

    def charts_by_uuids(self, mapping):
        return {u: {"id": 11, "slice_name": n} for u, n in mapping.items()}

    def import_dashboard_bundle(self, bundle, overwrite=True):
        self._maybe_crash("import_dashboard_bundle")

        class R:
            status_code = 200
            text = ""
        return R()


@pytest.fixture
def flow(monkeypatch, tmp_path):
    """Isolate apply's orchestration: resolution, compile and restore stubbed."""
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(ap, "resolve", lambda spec, client: Resolution())
    monkeypatch.setattr(ap, "_ownership_guard", lambda spec, client: None)
    monkeypatch.setattr(ap, "_roundtrip_dataset_files", lambda res, client: {})
    monkeypatch.setattr(ap, "compile_bundle", lambda spec, res, extra_files=None: b"bundle")
    monkeypatch.setattr(ap, "_apply_filter_scopes", lambda spec, client, did: [])
    monkeypatch.setattr(ap, "smoke", lambda spec, res, client: [])
    restores = []

    def fake_restore(data, slug, client):
        restores.append(data)
        return ap.ApplyReport(ok=True, stage="done")

    monkeypatch.setattr(ap, "restore_bundle", fake_restore)
    return restores


def test_a_rejected_chart_update_restores_the_backup(flow, monkeypatch):
    monkeypatch.setattr(ap, "_update_owned_charts_in_place",
                        lambda *a, **k: ([], ["chart 'Orders': update PUT HTTP 500 boom"]))
    rep = ap.apply(load_spec(SPEC), StubClient(), "dev")
    assert not rep.ok and rep.stage == "import"
    assert flow == [b"backup-zip"]
    assert any("AUTO-RESTORED" in w for w in rep.warnings)
    assert rep.backup


def test_an_unexpected_error_during_import_restores_and_reports(flow, monkeypatch):
    rep = ap.apply(load_spec(SPEC), StubClient(crash_on="import_dashboard_bundle"), "dev")
    assert not rep.ok and rep.stage == "import"
    assert "RuntimeError" in rep.import_detail
    assert flow == [b"backup-zip"]
    assert rep.backup


def test_an_unexpected_error_after_import_reports_without_restoring(flow, monkeypatch):
    monkeypatch.setattr(ap, "_update_owned_charts_in_place", lambda *a, **k: (["Orders"], []))
    calls = {"n": 0}
    client = StubClient()
    real = client.dashboard_charts

    def second_call_crashes(did):
        calls["n"] += 1
        if calls["n"] == 2:              # call 1 is the prepare-stage stale check
            raise RuntimeError("bug in linkage")
        return real(did)

    client.dashboard_charts = second_call_crashes
    rep = ap.apply(load_spec(SPEC), client, "dev")
    assert not rep.ok and rep.stage == "linkage"
    assert "RuntimeError" in rep.import_detail
    assert flow == []                    # later steps leave the new version live
    assert rep.backup


def test_backups_written_in_the_same_second_never_overwrite(tmp_path):
    a = ap.write_backup(tmp_path, b"first")
    b = ap.write_backup(tmp_path, b"second")
    assert a != b
    assert a.read_bytes() == b"first" and b.read_bytes() == b"second"
    assert sorted(p.name for p in tmp_path.iterdir()) == [a.name, b.name]
