"""dashboard.owners: Superset accounts named in the spec.

The import bundle can't carry owners and the importer makes the importing account an
owner, so a CI apply used to leave the CI account as the only owner. Owners named in
the spec are resolved to user ids before anything is written, PUT after the import,
read back by decompile and compared by `plan`. Omitted, nothing changes: the bundle,
apply and plan are what they were.
"""

import base64
import io
import json
import zipfile

import pytest
import yaml
from pydantic import ValidationError

import chartwright.apply as ap
import chartwright.dashdiff as dashdiff
import chartwright.resolver as resolver
from chartwright import ids
from chartwright.client import SupersetAPIError
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import plan
from chartwright.decompile import DecompileResult, decompile_bundle
from chartwright.owners import resolve_owners, signed_in_account_id
from chartwright.resolver import Resolution, resolve
from chartwright.spec import json_schema, load_spec
from chartwright.testing import stub_resolution

DS = {"database": "warehouse", "table": "orders"}


def spec_data(owners=None) -> dict:
    data = {
        "spec_version": "1",
        "dashboard": {"title": "Orders", "slug": "orders"},
        "charts": [{"name": "Orders", "type": "big_number_total", "metric": "COUNT(*)",
                    "dataset": DS}],
        "layout": {"rows": [["Orders"]]},
    }
    if owners is not None:
        data["dashboard"]["owners"] = owners
    return data


def token(sub) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'sub': sub, 'type': 'access'})}.sig"


ACCOUNTS = [  # id, username, first, last, email
    (1, "admin", "Admin", "User", "admin@localhost"),
    (2, "jdoe", "Jane", "Doe", "jane.doe@example.com"),
    (3, "jdoe2", "John", "Doe", "john@example.com"),
    (4, "ci-bot", "CI", "Bot", "ci@example.com"),
]


class Session:
    def __init__(self, sub):
        self.headers = {"Authorization": f"Bearer {token(sub)}"} if sub is not None else {}


class Response:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text = status_code, text


class Client:
    """The account APIs of one Superset instance. `usernames` is whether the
    security API answers (6.1.0's default); without it, as on 4.1.4 and 5.0.0,
    only the related-owners list (id, full name, email) is there."""

    def __init__(self, usernames=True, signed_in=4, me=False, live_owners=(4,)):
        self.usernames = usernames
        self.session = Session(signed_in)
        self.me = me
        self.live_owners = list(live_owners)
        self.puts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    def _row(self, a):
        return {"value": a[0], "text": f"{a[2]} {a[3]}", "extra": {"email": a[4], "active": True}}

    def get(self, path, q=None, **_):
        self.gets.append(path)
        q = q or {}
        if path == "/api/v1/me/":
            if not self.me:
                raise SupersetAPIError("GET /api/v1/me/ failed: HTTP 401", 401)
            return {"result": {"id": self.me}}
        if path == "/api/v1/security/users/":
            if not self.usernames:
                raise SupersetAPIError("GET /api/v1/security/users/ failed: HTTP 404", 404)
            f = q["filters"][0]
            rows = [a for a in ACCOUNTS if (a[1] if f["col"] == "username" else a[0]) == f["value"]]
            return {"result": [{"id": a[0], "username": a[1], "first_name": a[2],
                                "last_name": a[3], "email": a[4]} for a in rows]}
        if path == "/api/v1/dashboard/related/owners":
            text = (q.get("filter") or "").lower()
            rows = [a for a in ACCOUNTS
                    if text in a[1].lower() or text in f"{a[2]} {a[3]}".lower()]
            page, size = q.get("page", 0), q.get("page_size", 20)
            out = [self._row(a) for a in rows[page * size:(page + 1) * size]]
            for i in q.get("include_ids") or []:
                if i not in [r["value"] for r in out]:
                    out += [self._row(a) for a in ACCOUNTS if a[0] == i]
            return {"count": len(out), "result": out}
        if path.startswith("/api/v1/dashboard/"):
            return {"result": {"owners": [{"id": i, "first_name": "x", "last_name": "y"}
                                          for i in self.live_owners]}}
        raise AssertionError(path)

    def put_json(self, path, payload):
        self.puts.append((path, payload))
        if "owners" in payload:
            self.live_owners = list(payload["owners"])
        return Response()


# -- the spec field -----------------------------------------------------------


def test_owners_are_optional_and_validated():
    assert load_spec(spec_data()).dashboard.owners is None
    assert load_spec(spec_data(["jdoe", "ana@example.com"])).dashboard.owners == [
        "jdoe", "ana@example.com"]
    assert load_spec(spec_data([])).dashboard.owners == []
    for bad in (["jdoe", "JDoe"], [" jdoe"], [""]):
        with pytest.raises(ValidationError):
            load_spec(spec_data(bad))
    assert "owners" in json_schema()["$defs"]["DashboardMeta"]["properties"]


def test_owners_never_reach_the_bundle():
    plain, owned = load_spec(spec_data()), load_spec(spec_data(["jdoe"]))
    assert compile_bundle(plain, stub_resolution(plain)) == compile_bundle(
        owned, stub_resolution(owned))


# -- resolving names to ids, before any write --------------------------------------


def test_usernames_resolve_where_the_instance_returns_them():
    ids_, errors = resolve_owners(["jdoe", "admin"], Client(usernames=True))
    assert errors == [] and ids_ == [1, 2, 4]  # the signed-in account (4) stays an owner


def test_emails_resolve_on_every_release():
    for usernames in (True, False):
        ids_, errors = resolve_owners(["jane.doe@example.com", "ADMIN@localhost"],
                                      Client(usernames=usernames))
        assert errors == [] and ids_ == [1, 2, 4]


def test_an_email_the_local_part_search_misses_is_found_by_the_full_list():
    class Counting(Client):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.filters = []

        def get(self, path, q=None, **kw):
            if path.endswith("related/owners"):
                self.filters.append((q or {}).get("filter"))
            return super().get(path, q=q, **kw)

    # "ci" is in the username ci-bot: one search finds it.
    client = Counting(usernames=False, signed_in=1)
    assert resolve_owners(["ci@example.com"], client) == ([1, 4], [])
    assert client.filters == ["ci"]
    # "jane.doe" is in no username or full name ("Jane Doe"): the whole list is read.
    client = Counting(usernames=False, signed_in=1)
    assert resolve_owners(["jane.doe@example.com"], client) == ([1, 2], [])
    assert client.filters == ["jane.doe", None]


def test_an_unknown_username_is_an_error_with_did_you_mean():
    ids_, errors = resolve_owners(["jdeo", "jdoe"], Client(usernames=True))
    assert ids_ is None
    [e] = errors
    assert e.code == "owner_not_found" and e.ref == "jdeo"
    assert e.candidates[:2] == ["jdoe", "jdoe2"]


def test_a_username_without_the_security_api_asks_for_an_email():
    ids_, errors = resolve_owners(["jdoe"], Client(usernames=False))
    [e] = errors
    assert ids_ is None and e.code == "owner_not_found"
    assert "email" in e.detail and "4.1.4" in e.detail
    assert set(e.candidates) == {"jane.doe@example.com", "john@example.com"}


def test_a_name_two_accounts_answer_to_is_ambiguous():
    global ACCOUNTS
    saved = ACCOUNTS
    ACCOUNTS = [*saved, (5, "ops@example.com", "Ops", "Team", "team@example.com"),
                (6, "ops", "Ops", "Lead", "ops@example.com")]
    try:
        _, errors = resolve_owners(["ops@example.com"], Client(usernames=True))
    finally:
        ACCOUNTS = saved
    assert [e.code for e in errors] == ["owner_ambiguous"]
    assert sorted(errors[0].candidates) == ["ops", "ops@example.com"]


def test_the_signed_in_account_comes_from_the_token_or_me():
    assert signed_in_account_id(Client(signed_in=4)) == 4
    assert signed_in_account_id(Client(signed_in="7")) == 7       # 5.0.0 and 6.1.0 send a string
    assert signed_in_account_id(Client(signed_in=None, me=9)) == 9
    assert signed_in_account_id(Client(signed_in=None)) is None
    _, errors = resolve_owners(["jdoe"], Client(signed_in=None))
    assert [e.code for e in errors] == ["owner_account_unknown"]


def test_resolve_reports_owner_errors_with_the_rest(monkeypatch):
    class WithDatasets(Client):
        def find_datasets(self, table):
            return []

    res = resolve(load_spec(spec_data(["nobody"])), WithDatasets())
    codes = sorted(e.code for e in res.errors)
    assert codes == ["dataset_not_found", "owner_not_found"]
    assert res.owner_ids is None
    owner = next(e for e in res.errors if e.code == "owner_not_found")
    assert owner.as_dict()["ref"] == "nobody" and owner.chart is None


def test_resolve_leaves_owners_alone_when_the_spec_omits_them():
    class WithDatasets(Client):
        def find_datasets(self, table):
            return []

    client = WithDatasets()
    res = resolve(load_spec(spec_data()), client)
    assert res.owner_ids is None
    assert not [p for p in client.gets if "owners" in p or "security" in p]


# -- apply ----------------------------------------------------------------------


class ApplyClient(Client):
    base_url = "http://superset.test"

    def find_dashboard_by_slug(self, slug):
        return {"id": 7}

    def export_dashboard(self, did):
        return b"backup-zip"

    def dashboard_charts(self, did):
        return [{"slice_name": "Orders"}]

    def charts_by_uuids(self, mapping):
        return {}

    def import_dashboard_bundle(self, bundle, overwrite=True):
        return Response()


@pytest.fixture
def flow(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(ap, "_ownership_guard", lambda spec, client: None)
    monkeypatch.setattr(ap, "_roundtrip_dataset_files", lambda res, client: {})
    monkeypatch.setattr(ap, "compile_bundle", lambda spec, res, extra_files=None: b"bundle")
    monkeypatch.setattr(ap, "_apply_filter_scopes", lambda spec, client, did: [])
    monkeypatch.setattr(ap, "smoke", lambda spec, res, client: [])

    def run(owner_ids, client=None):
        monkeypatch.setattr(ap, "resolve",
                            lambda spec, client, *_: Resolution(owner_ids=owner_ids))
        client = client or ApplyClient()
        return ap.apply(load_spec(spec_data()), client, "dev"), client
    return run


def test_apply_puts_the_resolved_owners_after_the_import(flow):
    rep, client = flow([2, 4])
    assert rep.ok
    assert client.puts == [("/api/v1/dashboard/7", {"owners": [2, 4]})]


def test_apply_without_owners_sends_none(flow):
    rep, client = flow(None)
    assert rep.ok and client.puts == []


def test_a_rejected_owners_put_fails_the_apply_at_its_own_stage(flow):
    class Refusing(ApplyClient):
        def put_json(self, path, payload):
            return Response(403, '{"message": "Forbidden"}')

    rep, _ = flow([2, 4], Refusing())
    assert not rep.ok and rep.stage == "owners"
    assert "owners PUT failed: HTTP 403" in rep.import_detail


def test_an_unknown_owner_stops_apply_before_any_write(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_BACKUP_DIR", str(tmp_path))

    class WithDatasets(ApplyClient):
        def find_datasets(self, table):
            return [{"id": 1, "table_name": "orders", "schema": None, "uuid": "u",
                     "database": {"database_name": "warehouse", "id": 1}}]

        def dataset_detail(self, did):
            return {"columns": [], "metrics": [], "main_dttm_col": None}

        def import_dashboard_bundle(self, bundle, overwrite=True):
            raise AssertionError("nothing may be written")

    client = WithDatasets()
    rep = ap.apply(load_spec(spec_data(["nobody@example.com"])), client, "dev")
    assert not rep.ok and rep.stage == "resolve"
    assert [e["code"] for e in rep.resolution_errors] == ["owner_not_found"]
    assert client.puts == []


# -- decompile and plan -----------------------------------------------------------


def test_decompile_reads_owners_back(monkeypatch):
    from chartwright import decompile as dec

    spec = load_spec(spec_data())
    res = stub_resolution(spec)
    blob = compile_bundle(spec, res)

    class Live(Client):
        def find_dashboard_by_slug(self, slug):
            return {"id": 7}

        def export_dashboard(self, did):
            return blob

    monkeypatch.setattr(dec, "live_dataset_lookup", lambda client: (
        lambda u: {"database": "warehouse", "schema": None, "table": "orders"}))
    out = dec.decompile_live("orders", Live(usernames=True, live_owners=[2, 4]))
    assert out.spec["dashboard"]["owners"] == ["ci-bot", "jdoe"]
    assert out.owner_ids == [2, 4] and out.losses == []
    out = dec.decompile_live("orders", Live(usernames=False, live_owners=[2, 4]))
    assert out.spec["dashboard"]["owners"] == ["ci@example.com", "jane.doe@example.com"]
    # The decompiled spec loads, and names what apply would PUT again.
    assert load_spec(out.spec).dashboard.owners == ["ci@example.com", "jane.doe@example.com"]


def test_decompile_leaves_owners_out_when_one_cannot_be_named(monkeypatch):
    from chartwright import decompile as dec

    spec = load_spec(spec_data())
    blob = compile_bundle(spec, stub_resolution(spec))

    class Live(Client):
        def find_dashboard_by_slug(self, slug):
            return {"id": 7}

        def export_dashboard(self, did):
            return blob

    monkeypatch.setattr(dec, "live_dataset_lookup", lambda client: (lambda u: None))
    out = dec.decompile_live("orders", Live(usernames=False, live_owners=[2, 99]))
    assert "owners" not in out.spec["dashboard"]
    assert any("owners not read back" in loss.what for loss in out.losses)


def test_decompile_survives_an_owner_list_it_may_not_read(monkeypatch):
    from chartwright import decompile as dec

    spec = load_spec(spec_data())
    blob = compile_bundle(spec, stub_resolution(spec))

    class Closed(Client):
        def find_dashboard_by_slug(self, slug):
            return {"id": 7}

        def export_dashboard(self, did):
            return blob

        def get(self, path, q=None, **kw):
            if path.endswith("related/owners"):
                raise SupersetAPIError("GET related/owners failed: HTTP 403", 403)
            return super().get(path, q=q, **kw)

    monkeypatch.setattr(dec, "live_dataset_lookup", lambda client: (lambda u: None))
    out = dec.decompile_live("orders", Closed(usernames=False, live_owners=[2]))
    assert "owners" not in out.spec["dashboard"] and out.owner_ids == [2]
    assert any("HTTP 403" in loss.what for loss in out.losses)


def plan_with(spec, live_owner_ids, owner_ids, monkeypatch) -> dict:
    res = stub_resolution(spec)
    res.owner_ids = owner_ids
    live = decompile_bundle(compile_bundle(spec, res), lambda u: {
        "database": "warehouse", "schema": None, "table": "orders"})
    live.owner_ids = live_owner_ids
    monkeypatch.setattr(resolver, "resolve", lambda s, c, *_: res)
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: live)

    class C:
        def find_dashboard_by_slug(self, slug):
            return {"id": 3, "uuid": str(ids.dashboard_uuid(slug))}

    return json.loads(plan(spec, C()).to_json())


def test_plan_compares_owners_as_ids(monkeypatch):
    spec = load_spec(spec_data(["jdoe"]))
    assert plan_with(spec, [2, 4], [2, 4], monkeypatch)["clean"] is True
    out = plan_with(spec, [4], [2, 4], monkeypatch)
    assert out["dashboard_settings_changed"] == ["owners"] and out["clean"] is False


def test_plan_leaves_owners_alone_when_the_spec_omits_them(monkeypatch):
    spec = load_spec(spec_data())
    assert plan_with(spec, [1, 2, 3], None, monkeypatch)["clean"] is True


def test_decompiled_owners_do_not_drift_against_a_spec_that_names_them(monkeypatch):
    """A spec carrying owners, and its live build read back with owners, compare
    equal apart from the id check: owners never leak into the layout or settings."""
    spec = load_spec(spec_data(["jdoe"]))
    res = stub_resolution(spec)
    live = decompile_bundle(compile_bundle(spec, res), lambda u: {
        "database": "warehouse", "schema": None, "table": "orders"})
    live.spec["dashboard"]["owners"] = ["ci-bot", "jdoe"]
    live.owner_ids = [2, 4]
    res.owner_ids = [2, 4]
    monkeypatch.setattr(resolver, "resolve", lambda s, c, *_: res)
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: live)

    class C:
        def find_dashboard_by_slug(self, slug):
            return {"id": 3, "uuid": str(ids.dashboard_uuid(slug))}

    assert json.loads(plan(spec, C()).to_json())["clean"] is True


def test_the_bundle_has_no_owners_key():
    spec = load_spec(spec_data(["jdoe"]))
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    dash = next(yaml.safe_load(zf.read(n)) for n in zf.namelist() if "/dashboards/" in n)
    assert "owners" not in dash
    assert isinstance(DecompileResult(spec={}).owner_ids, type(None))
