"""Backups (triage L, M, N): each backup records the instance it came from and restore
refuses another instance's; a restore deletes the charts the tool created after the
backup that are left on no dashboard; old backups are pruned to the newest 50 per
profile and dashboard (CHARTWRIGHT_BACKUP_KEEP; 0 keeps all)."""

import json
from pathlib import Path

import pytest

from chartwright import apply as apply_mod
from chartwright import cli, resolver
from chartwright.apply import (BACKUP_KEEP_DEFAULT, backup_dir_for, backup_keep, backup_record,
                               prune_backups, write_backup)
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

from fake_superset import FakeSuperset

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture() -> dict:
    return json.loads((FIXTURES / "sales_overview.json").read_text())


# -- N: pruning ------------------------------------------------------------------------


def test_a_backup_carries_a_record_of_its_instance(tmp_path):
    path = write_backup(tmp_path, b"zip", record={"base_url": "https://bi.example.com",
                                                  "profile": "prod", "slug": "s", "dashboard_id": 3})
    rec = backup_record(path)
    assert rec["base_url"] == "https://bi.example.com" and rec["profile"] == "prod"
    assert rec["created"] == path.stem
    assert backup_record(tmp_path / "missing.zip") is None


def test_pruning_keeps_the_newest_and_leaves_other_files(tmp_path):
    names = [f"2026100{d}T120000.000000.zip" for d in range(1, 6)]
    for n in names:
        (tmp_path / n).write_bytes(b"z")
        (tmp_path / n.replace(".zip", ".json")).write_text("{}")
    (tmp_path / "keep-me.zip").write_bytes(b"mine")
    removed = prune_backups(tmp_path, 2)
    assert [p.name for p in removed] == names[:3]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == sorted(["keep-me.zip", names[3], names[4],
                           names[3].replace(".zip", ".json"), names[4].replace(".zip", ".json")])
    assert prune_backups(tmp_path, 0) == []


@pytest.mark.parametrize("raw, keep, warned", [
    (None, BACKUP_KEEP_DEFAULT, False), ("", BACKUP_KEEP_DEFAULT, False), ("0", 0, False),
    ("7", 7, False), ("-1", BACKUP_KEEP_DEFAULT, True), ("lots", BACKUP_KEEP_DEFAULT, True),
])
def test_the_keep_setting(monkeypatch, raw, keep, warned):
    if raw is None:
        monkeypatch.delenv("CHARTWRIGHT_BACKUP_KEEP", raising=False)
    else:
        monkeypatch.setenv("CHARTWRIGHT_BACKUP_KEEP", raw)
    got, warning = backup_keep()
    assert got == keep and bool(warning) == warned


# -- apply and restore against the fake Superset -----------------------------------------


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(apply_mod, "resolve", lambda spec, client, *a, **k: stub_resolution(spec))
    monkeypatch.setattr(resolver, "resolve", lambda spec, client, *a, **k: stub_resolution(spec))
    monkeypatch.setattr(apply_mod, "_roundtrip_dataset_files", lambda res, client: {})
    monkeypatch.setattr(apply_mod, "smoke", lambda *a, **k: [])

    def setup(merge_links=False):
        spec = load_spec(_fixture())
        ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
        fake = FakeSuperset({ds.uuid: {"table_name": "cleaned_sales_data",
                                       "database_name": "examples"}}, merge_links=merge_links)
        assert apply_mod.apply(spec, fake, "prod").ok
        return fake
    return setup


def test_apply_writes_the_record_and_prunes(live, monkeypatch):
    fake = live()
    spec = load_spec(_fixture())
    folder = backup_dir_for("prod", spec.dashboard.slug)
    folder.mkdir(parents=True, exist_ok=True)
    for d in range(1, 4):
        (folder / f"2020010{d}T000000.000000.zip").write_bytes(b"old")
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_KEEP", "2")
    report = apply_mod.apply(spec, fake, "prod")
    assert report.ok
    assert len(list(folder.glob("*.zip"))) == 2
    assert backup_record(Path(report.backup))["base_url"] == fake.base_url
    assert any("removed 2 old backup(s)" in w for w in report.warnings)


@pytest.mark.parametrize("merge_links", [False, True])
def test_restore_deletes_the_tools_charts_made_after_the_backup(live, merge_links):
    """A chart a later apply added (the failed apply's, most often) is left on no
    dashboard by a restore of the earlier backup; it is the tool's, so it goes. A
    chart someone added in Superset is never deleted."""
    fake = live(merge_links=merge_links)
    data = _fixture()
    data["charts"].append(dict(next(c for c in data["charts"] if c["name"] == "Total Sales"),
                               name="Extra"))
    data["layout"]["rows"].append(["Extra"])
    report = apply_mod.apply(load_spec(data), fake, "prod")
    assert report.ok
    dash = report.dashboard_id
    extra_id = fake.linked(dash)["Extra"]
    fake.next_id += 1  # a chart added in the UI, linked to the dashboard
    fake.charts[fake.next_id] = {"uuid": "abababab-0000-0000-0000-000000000009",
                                 "slice_name": "Hand made", "params": {}, "viz_type": "pie",
                                 "dataset_uuid": None, "query_context": None, "dashboards": {dash}}
    hand_id = fake.next_id
    restored = apply_mod.restore_bundle(Path(report.backup).read_bytes(), "sdc-sales-overview", fake)
    assert restored.ok, restored.import_detail
    assert extra_id not in fake.charts
    assert hand_id in fake.charts and dash not in fake.charts[hand_id]["dashboards"]
    assert any("created after this backup" in w and "Extra" in w for w in restored.warnings)


# -- L: restore checks the instance --------------------------------------------------------


class _Client:
    base_url = "https://staging.example.com"


def _restore(capsys, monkeypatch, bundle, *extra) -> tuple[int, dict]:
    monkeypatch.setattr(cli, "_client", lambda profile: _Client())
    monkeypatch.setattr(apply_mod, "restore_bundle",
                        lambda blob, slug, client: apply_mod.ApplyReport(ok=True, stage="done"))
    try:
        cli.main(["restore", str(bundle), "--profile", "staging", *extra])
        code = 0
    except SystemExit as e:
        code = e.code or 0
    return code, json.loads(capsys.readouterr().out)


def _tool_backup(tmp_path, base_url: str | None) -> Path:
    from chartwright.compiler import compile_bundle

    spec = load_spec(_fixture())
    record = None if base_url is None else {"base_url": base_url, "profile": "prod",
                                            "slug": spec.dashboard.slug, "dashboard_id": 1}
    return write_backup(tmp_path, compile_bundle(spec, stub_resolution(spec)), record=record)


def test_restore_refuses_a_backup_from_another_instance(tmp_path, capsys, monkeypatch):
    bundle = _tool_backup(tmp_path, "https://prod.example.com/")
    code, out = _restore(capsys, monkeypatch, bundle)
    assert code == 1 and out["errors"][0]["code"] == "other_instance"
    assert "prod.example.com" in out["errors"][0]["detail"]
    assert "--to-other-instance" in out["errors"][0]["detail"]
    code, out = _restore(capsys, monkeypatch, bundle, "--to-other-instance")
    assert code == 0 and out["ok"]


def test_restore_of_the_same_instance_needs_no_flag(tmp_path, capsys, monkeypatch):
    bundle = _tool_backup(tmp_path, "https://STAGING.example.com/")
    code, out = _restore(capsys, monkeypatch, bundle)
    assert code == 0 and out["ok"] and not out["warnings"]


def test_a_backup_without_a_record_restores_with_a_warning(tmp_path, capsys, monkeypatch):
    bundle = _tool_backup(tmp_path, None)
    code, out = _restore(capsys, monkeypatch, bundle)
    assert code == 0 and out["ok"]
    assert any("records no instance" in w for w in out["warnings"])
