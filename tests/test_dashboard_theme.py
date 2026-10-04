"""dashboard.theme: a Superset 6 theme, named in the spec (docs/CONTRACTS.md,
"Dashboard theme").

Superset 6.0.0 added themes: Dashboard.theme_id (models/dashboard.py:139 at 6.0.0,
:140 at 6.1.0), theme_uuid and theme_id in ImportV1DashboardSchema (dashboards/
schemas.py:503-504 at 6.0.0, :520-521 at 6.1.0), and an export that names the theme
by theme_uuid and ships it under themes/ (commands/dashboard/export.py:164-165,
:199-203). The importer maps a theme_uuid only through a themes/ file in the same
bundle and otherwise sets no theme (importers/v1/__init__.py:170-177 at 6.0.0); it
takes theme_id as written (Dashboard.extra_import_fields). So check resolves the name
on the instance, the bundle carries that theme_id, decompile reads the name back from
an export, plan compares names when the spec names one, and 4.1.4 and 5.0.0, which
have no themes, refuse it at resolve.
"""

import json

import pytest
from pydantic import ValidationError

from chartwright import dashdiff, ids, resolver
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import plan
from chartwright.decompile import decompile_bundle
from chartwright.resolver import resolve
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution
from chartwright.versions import check_spec_version
from test_dashboard_settings import dashboard_doc, edit_dashboard, lookup_for, spec_data
from test_superset_version import FakeClient

THEME_UUID = "77777777-7777-7777-7777-777777777777"


class Client(FakeClient):
    def __init__(self, version="6.1.0", themes=None, **kw):
        super().__init__(version, **kw)
        self._themes = themes if themes is not None else [
            {"id": 7, "uuid": THEME_UUID, "theme_name": "Acme Brand"},
            {"id": 8, "uuid": "88888888-8888-8888-8888-888888888888", "theme_name": "Dark"}]

    def themes(self):
        return self._themes


def themed(name="Acme Brand"):
    return load_spec(spec_data({"theme": name}))


# -- the spec field -------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "   ", " Acme Brand", "x" * 251])
def test_a_theme_is_a_name(name):
    assert themed("Acme Brand").dashboard.theme == "Acme Brand"
    assert load_spec(spec_data()).dashboard.theme is None      # optional
    with pytest.raises(ValidationError):
        themed(name)


# -- the version gate -----------------------------------------------------------------


@pytest.mark.parametrize("version, ok", [("4.1.4", False), ("5.0.0", False),
                                         ("6.0.0", True), ("6.1.0", True)])
def test_a_theme_needs_6_0(version, ok):
    checked = check_spec_version(themed(), version)
    assert checked.ok is ok
    if not ok:
        assert checked.errors[0]["code"] == "superset_version_too_old"
        assert checked.errors[0]["ref"] == "theme"


def test_an_older_instance_refuses_before_looking_up_any_theme():
    class NoThemes(Client):
        def themes(self):
            raise AssertionError("a 5.0 instance has no theme API")

    res = resolve(themed(), NoThemes("5.0.0"))
    assert [e.code for e in res.errors] == ["superset_version_too_old"]


# -- resolve ----------------------------------------------------------------------------


def test_resolve_finds_the_theme_by_its_exact_name():
    res = resolve(themed(), Client())
    assert res.ok and (res.theme_id, res.theme_uuid) == (7, THEME_UUID)


def test_an_unknown_theme_is_a_resolve_error_with_the_closest_names():
    res = resolve(themed("Acme Brnd"), Client())
    [e] = res.errors
    assert e.code == "theme_not_found" and e.ref == "theme"
    assert e.candidates == ["Acme Brand"] and "Themes: Acme Brand, Dark" in e.detail


def test_two_themes_of_one_name_are_an_error_not_a_guess():
    twins = [{"id": 1, "uuid": "a", "theme_name": "Acme Brand"},
             {"id": 2, "uuid": "b", "theme_name": "Acme Brand"}]
    [e] = resolve(themed(), Client(themes=twins)).errors
    assert e.code == "theme_ambiguous" and "[1, 2]" in e.detail


def test_a_theme_list_the_account_cannot_read_is_named():
    from chartwright.client import SupersetAPIError

    class Closed(Client):
        def themes(self):
            raise SupersetAPIError("GET /api/v1/theme/ failed", 403)

    [e] = resolve(themed(), Closed()).errors
    assert e.code == "theme_lookup_failed" and "can read on Theme" in e.detail


def test_the_client_reads_every_page_of_themes():
    from chartwright.client import SupersetClient

    pages = [[{"id": i, "uuid": str(i), "theme_name": f"t{i}"} for i in range(100)],
             [{"id": 100, "uuid": "100", "theme_name": "last"}]]
    asked = []

    class Paged(SupersetClient):
        def get(self, path, **params):
            asked.append(params["q"]["page"])
            return {"result": pages[params["q"]["page"]]}

    c = Paged.__new__(Paged)
    assert len(c.themes()) == 101 and asked == [0, 1]


# -- compile ------------------------------------------------------------------------------


def test_the_bundle_carries_the_resolved_theme_id():
    spec = themed()
    res = stub_resolution(spec)
    res.theme_id = 7
    doc = next(d for n, d in _docs(compile_bundle(spec, res)).items() if "/dashboards/" in n)
    assert doc["theme_id"] == 7 and "theme_uuid" not in doc
    plain = dashboard_doc(load_spec(spec_data()))              # no theme, no key
    assert "theme_id" not in plain and "theme_uuid" not in plain


def test_compile_refuses_an_unresolved_theme():
    spec = themed()
    res = stub_resolution(load_spec(spec_data()))
    with pytest.raises(ValueError, match="not resolved"):
        compile_bundle(spec, res)


def _docs(blob):
    import io
    import zipfile

    import yaml

    zf = zipfile.ZipFile(io.BytesIO(blob))
    return {n: yaml.safe_load(zf.read(n)) for n in zf.namelist() if n.endswith(".yaml")}


# -- decompile ---------------------------------------------------------------------------


def exported(spec, theme_name="Acme Brand", theme_uuid=THEME_UUID, ship_theme=True):
    """The bundle a 6.x export of this dashboard gives: theme_uuid on the dashboard, the
    theme itself under themes/."""
    res = stub_resolution(load_spec(spec_data()) if spec.dashboard.theme is None else spec)
    blob = compile_bundle(spec, res)

    def edit(path, doc):
        if "/dashboards/" in path:
            doc.pop("theme_id", None)
            doc["theme_uuid"] = theme_uuid

    root = next(iter(_docs(blob))).split("/")[0]
    add = ({f"{root}/themes/Acme_Brand.yaml": {
        "theme_name": theme_name, "json_data": {"token": {"colorPrimary": "#003366"}},
        "uuid": theme_uuid, "version": "1.0.0"}} if ship_theme else {})
    return edit_bundle(blob, edit, add), res


def test_decompile_reads_the_theme_name_from_an_export():
    blob, res = exported(themed())
    out = decompile_bundle(blob, lookup_for(res))
    assert out.spec["dashboard"]["theme"] == "Acme Brand" and out.losses == []
    plain = load_spec(spec_data())                             # no theme: as before
    res = stub_resolution(plain)
    out = decompile_bundle(compile_bundle(plain, res), lookup_for(res))
    assert "theme" not in out.spec["dashboard"] and out.losses == []


def test_decompile_names_a_theme_it_cannot_read():
    blob, res = exported(themed(), ship_theme=False)
    out = decompile_bundle(blob, lookup_for(res))
    assert "theme" not in out.spec["dashboard"]
    assert any("has no themes/ file" in l.what for l in out.losses)


def test_decompile_of_a_compiled_bundle_says_the_id_has_no_name():
    spec = themed()
    res = stub_resolution(spec)
    out = decompile_bundle(compile_bundle(spec, res), lookup_for(res))
    assert "theme" not in out.spec["dashboard"]
    assert any("theme_id 1000 can't be named offline" in l.what for l in out.losses)


# -- plan -----------------------------------------------------------------------------------


def plan_live(target, live_blob, live_res, monkeypatch) -> dict:
    live = decompile_bundle(live_blob, lookup_for(live_res))
    monkeypatch.setattr(resolver, "resolve", lambda s, c, *_: stub_resolution(s))
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: live)

    class Owned:
        def find_dashboard_by_slug(self, slug):
            return {"id": 3, "uuid": str(ids.dashboard_uuid(slug))}

    return json.loads(plan(target, Owned()).to_json())


def test_plan_sees_the_theme_when_the_spec_names_one(monkeypatch):
    blob, res = exported(themed())
    assert plan_live(themed(), blob, res, monkeypatch)["clean"] is True
    out = plan_live(themed("Dark"), blob, res, monkeypatch)
    assert out["clean"] is False and out["dashboard_settings_changed"] == ["theme"]


def test_plan_sees_a_theme_missing_live(monkeypatch):
    spec = load_spec(spec_data())
    res = stub_resolution(spec)
    out = plan_live(themed(), compile_bundle(spec, res), res, monkeypatch)
    assert out["dashboard_settings_changed"] == ["theme"]


def test_plan_leaves_a_ui_chosen_theme_alone_when_the_spec_names_none(monkeypatch):
    """The import keeps a theme chosen in the UI when the bundle names none, so it is
    no drift apply could repair."""
    blob, res = exported(themed())
    assert plan_live(load_spec(spec_data()), blob, res, monkeypatch)["clean"] is True
