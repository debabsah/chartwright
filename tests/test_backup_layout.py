"""Backup zips are namespaced by PROFILE, not just slug: the same slug on two
instances shares a uuid5, so restore's ownership guard cannot tell their zips
apart; the directory layout must."""

from pathlib import Path

from chartwright.apply import backup_dir_for


def test_default_layout_is_profile_then_slug(monkeypatch):
    monkeypatch.delenv("CHARTWRIGHT_BACKUP_DIR", raising=False)
    d = backup_dir_for("work", "sales-overview")
    assert d.parts[-3:] == ("backups", "work", "sales-overview")
    assert d.is_absolute()


def test_override_dir_keeps_profile_namespace(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path))
    assert backup_dir_for("work", "s") == tmp_path / "work" / "s"


def test_distinct_profiles_never_share_a_dir(monkeypatch):
    monkeypatch.delenv("CHARTWRIGHT_BACKUP_DIR", raising=False)
    assert backup_dir_for("sandbox", "s") != backup_dir_for("work", "s")
