"""Adopt in place: a spec that takes over an existing, hand-built dashboard and
updates that same dashboard (same uuid, so the same id and address) instead of
building a copy (offline)."""

import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chartwright import cli, ids
from chartwright.adopt import adopted_spec
from chartwright.apply import _ownership_guard, _unlink_charts
from chartwright.compiler import compile_bundle
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

from test_decompile import _stub_lookup_for

FIXTURES = Path(__file__).parent / "fixtures"
HAND_DASH = "11111111-2222-3333-4444-555555555555"


def _fixture() -> dict:
    return json.loads((FIXTURES / "sales_overview.json").read_text())


def _hand_built(extra_chart: bool = False) -> tuple[bytes, dict, object]:
    """A compiled bundle made to look built in the UI: every uuid random
    (deterministic here), optionally plus a chart type the spec can't hold."""
    spec = load_spec(_fixture())
    bundle = compile_bundle(spec, stub_resolution(spec))
    chart_uuids: dict[str, str] = {}

    def rewrite(path, doc):
        if "/dashboards/" in path:
            doc["uuid"] = HAND_DASH
            for node in doc["position"].values():
                if isinstance(node, dict) and node.get("type") == "CHART":
                    node["meta"]["uuid"] = chart_uuids.setdefault(
                        node["meta"]["sliceName"], str(uuid.uuid5(uuid.NAMESPACE_DNS, node["meta"]["sliceName"])))
        elif "/charts/" in path:
            doc["uuid"] = chart_uuids.setdefault(
                doc["slice_name"], str(uuid.uuid5(uuid.NAMESPACE_DNS, doc["slice_name"])))

    add = {}
    if extra_chart:
        add["sdc_bundle/charts/Sunburst.yaml"] = {
            "slice_name": "Sunburst", "viz_type": "sunburst_v2", "params": {},
            "uuid": "99999999-0000-0000-0000-000000000001", "dataset_uuid": "x"}
    hand = edit_bundle(bundle, rewrite, add=add)
    return hand, chart_uuids, spec


def test_decompile_reports_identity_and_skipped_charts():
    hand, chart_uuids, spec = _hand_built(extra_chart=True)
    result = decompile_bundle(hand, _stub_lookup_for(spec))
    assert result.dashboard_uuid == HAND_DASH
    assert result.chart_uuids == chart_uuids
    assert result.skipped_charts == ["Sunburst"]


def test_adopt_refuses_when_charts_would_leave_the_dashboard_unless_forced():
    hand, _, spec = _hand_built(extra_chart=True)
    result = decompile_bundle(hand, _stub_lookup_for(spec))
    refused = adopted_spec(result)
    assert not refused.ok and refused.skipped_charts == ["Sunburst"]
    assert "not be deleted" in refused.detail
    forced = adopted_spec(result, force=True)
    assert forced.ok and forced.skipped_charts == ["Sunburst"]


def test_adopted_spec_compiles_to_the_same_dashboard_and_charts():
    hand, chart_uuids, spec = _hand_built()
    adopted = adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec)))
    assert adopted.ok
    assert adopted.spec["dashboard"]["adopted"] == {"dashboard_uuid": HAND_DASH, "slug": "sdc-sales-overview",
                                                    "charts": chart_uuids}
    live = load_spec(adopted.spec)
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(live, stub_resolution(live))))
    docs = {n: yaml.safe_load(zf.read(n)) for n in zf.namelist() if n.endswith(".yaml")}
    dash = next(d for n, d in docs.items() if "/dashboards/" in n)
    assert dash["uuid"] == HAND_DASH  # the importer overwrites THIS dashboard, in place
    assert {d["slice_name"]: d["uuid"] for n, d in docs.items() if "/charts/" in n} == chart_uuids
    placed = {n["meta"]["uuid"] for n in dash["position"].values()
              if isinstance(n, dict) and n.get("type") == "CHART"}
    assert placed == set(chart_uuids.values())


def test_a_chart_added_after_adoption_gets_a_derived_id():
    data = _fixture()
    data["dashboard"]["adopted"] = {"dashboard_uuid": HAND_DASH, "slug": "sdc-sales-overview",
                                   "charts": {"Total Orders": "aaaaaaaa-0000-0000-0000-000000000001"}}
    spec = load_spec(data)
    assert str(spec.chart_uuid("Total Orders")) == "aaaaaaaa-0000-0000-0000-000000000001"
    assert spec.chart_uuid("Total Sales") == ids.chart_uuid("sdc-sales-overview", "Total Sales")
    assert str(spec.dashboard_uuid()) == HAND_DASH


@pytest.mark.parametrize("adopted, message", [
    ({"dashboard_uuid": "not-a-uuid", "slug": "sdc-sales-overview"}, "not a uuid"),
    ({"dashboard_uuid": HAND_DASH, "slug": "sdc-sales-overview", "charts": {"Gone": HAND_DASH}}, "not in the spec"),
    ({"dashboard_uuid": HAND_DASH, "slug": "sdc-sales-overview",
      "charts": {"Total Orders": HAND_DASH, "Total Sales": HAND_DASH}}, "several names"),
    ({"dashboard_uuid": HAND_DASH, "slug": "the-original"}, "Remove dashboard.adopted"),
])
def test_adopted_block_is_validated(adopted, message):
    data = _fixture()
    data["dashboard"]["adopted"] = adopted
    with pytest.raises(ValidationError, match=message):
        load_spec(data)


class _Client:
    def __init__(self, live_uuid):
        self.live_uuid = live_uuid

    def find_dashboard_by_slug(self, slug):
        return {"id": 7}

    def get(self, path):
        return {"result": {"uuid": self.live_uuid}}


def test_ownership_guard_accepts_only_the_adopted_dashboard():
    plain = load_spec(_fixture())
    assert "chartwright adopt" in _ownership_guard(plain, _Client(HAND_DASH))
    data = _fixture()
    data["dashboard"]["adopted"] = {"dashboard_uuid": HAND_DASH, "slug": "sdc-sales-overview"}
    adopted = load_spec(data)
    assert _ownership_guard(adopted, _Client(HAND_DASH)) is None
    other = _ownership_guard(adopted, _Client("22222222-2222-2222-2222-222222222222"))
    assert "different dashboard" in other


def test_unlink_takes_a_chart_off_one_dashboard_only():
    sent = []

    class Client:
        def get(self, path):
            return {"result": {"dashboards": [{"id": 7}, {"id": 9}]}}

        def put_json(self, path, payload):
            sent.append((path, payload))
            return type("R", (), {"status_code": 200})()

    assert _unlink_charts(Client(), 7, [{"id": 3, "slice_name": "Old"}]) == ["Old"]
    assert sent == [("/api/v1/chart/3", {"dashboards": [9]})]


def test_restore_accepts_the_tools_own_backup_of_an_adopted_dashboard(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path / "backups"))
    hand, _, _ = _hand_built()  # uuid is random: not tool-owned by derivation
    outside = tmp_path / "elsewhere.zip"
    outside.write_bytes(hand)
    with pytest.raises(SystemExit):
        cli.main(["restore", str(outside), "--profile", "prod"])
    assert "not_owned" in capsys.readouterr().out

    from chartwright.apply import backup_dir_for

    inside = backup_dir_for("prod", "sdc-sales-overview")
    inside.mkdir(parents=True)
    (inside / "20261002T120000.zip").write_bytes(hand)

    class Reached(Exception):
        pass

    def no_network(_profile):
        raise Reached

    monkeypatch.setattr(cli, "_client", no_network)
    with pytest.raises(SystemExit):  # passed the ownership check, went on to sign in
        cli.main(["restore", str(inside / "20261002T120000.zip"), "--profile", "prod"])
    out = capsys.readouterr().out
    assert "Reached" in out and "not_owned" not in out


def test_a_copy_of_an_adopted_spec_cannot_point_at_the_original():
    """Cloning = copy the spec, change the slug. Left alone, the copy would carry the
    original's uuid and overwrite it; validation stops that offline."""
    hand, _, spec = _hand_built()
    adopted = adopted_spec(decompile_bundle(hand, _stub_lookup_for(spec))).spec
    clone = json.loads(json.dumps(adopted))
    clone["dashboard"]["slug"] = "sales-overview-emea"
    with pytest.raises(ValidationError, match="Remove dashboard.adopted"):
        load_spec(clone)
    del clone["dashboard"]["adopted"]
    assert load_spec(clone).dashboard_uuid() == ids.dashboard_uuid("sales-overview-emea")


def test_dashboard_settings_the_spec_cannot_carry_are_reported():
    spec = load_spec(_fixture())

    def styled(path, doc):
        if "/dashboards/" in path:
            doc["css"] = ".header { color: red; }"
            doc["published"] = False
            doc["metadata"]["color_scheme"] = "supersetColors"
            doc["metadata"]["refresh_frequency"] = 300

    bundle = edit_bundle(compile_bundle(spec, stub_resolution(spec)), styled)
    whats = [l.what for l in decompile_bundle(bundle, _stub_lookup_for(spec)).losses if l.where == "dashboard"]
    assert any("CSS" in w for w in whats) and any("colour scheme" in w for w in whats)
    assert any("auto-refresh" in w for w in whats) and any("draft" in w for w in whats)
