"""A small in-memory Superset for apply/plan tests (offline).

Only what apply, plan, adopt and restore touch, with the behaviours that matter
for ownership: the importer matches dashboards by uuid and overwrites them, never
overwrites an existing chart, and (6.1 behaviour) links exactly the charts in the
dashboard's position; `merge_links=True` gives the pre-6.1 importer, which only
adds links."""

from __future__ import annotations

import io
import json
import zipfile

import yaml

from chartwright.client import SupersetClient


class Resp:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakeSuperset:
    base_url = "http://fake"
    charts_by_uuids = SupersetClient.charts_by_uuids

    def __init__(self, datasets: dict[str, dict] | None = None, merge_links: bool = False):
        self.dashboards: dict[str, dict] = {}   # uuid -> {id, slug, yaml}
        self.charts: dict[int, dict] = {}       # id -> {uuid, slice_name, params, dashboards:set}
        self.datasets = datasets or {}          # uuid -> {table_name, schema, database_name}
        self.merge_links = merge_links
        self.next_id = 100
        self.log: list[str] = []
        self.refuse_chart_puts = False
        self.refuse_unlinks = False

    # -- import / export ------------------------------------------------------

    def import_dashboard_bundle(self, blob: bytes, overwrite: bool = True) -> Resp:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        docs = {n: yaml.safe_load(zf.read(n)) for n in zf.namelist() if n.endswith(".yaml")}
        for n, d in docs.items():
            if "/charts/" in n and not any(c["uuid"] == d["uuid"] for c in self.charts.values()):
                self.next_id += 1
                self.charts[self.next_id] = {"uuid": d["uuid"], "slice_name": d["slice_name"],
                                             "params": d.get("params"), "viz_type": d.get("viz_type"),
                                             "dataset_uuid": d.get("dataset_uuid"), "dashboards": set()}
        for n, d in docs.items():
            if "/dashboards/" in n:
                dash = self.dashboards.get(d["uuid"])
                if dash is None:
                    self.next_id += 1
                    dash = self.dashboards[d["uuid"]] = {"id": self.next_id}
                    self.log.append(f"created dashboard {dash['id']}")
                else:
                    self.log.append(f"overwrote dashboard {dash['id']}")
                dash.update(slug=d.get("slug"), yaml=d)
                placed = {v["meta"]["uuid"] for v in (d.get("position") or {}).values()
                          if isinstance(v, dict) and v.get("type") == "CHART"}
                for c in self.charts.values():
                    if not self.merge_links:
                        c["dashboards"].discard(dash["id"])
                    if c["uuid"] in placed:
                        c["dashboards"].add(dash["id"])
        return Resp()

    def export_dashboard(self, dashboard_id: int) -> bytes:
        u, dash = self._dashboard(dashboard_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("export/dashboards/d.yaml", yaml.safe_dump(dash["yaml"]))
            for cid, c in self.charts.items():
                if dashboard_id in c["dashboards"]:
                    zf.writestr(f"export/charts/c{cid}.yaml", yaml.safe_dump(
                        {"slice_name": c["slice_name"], "uuid": c["uuid"], "params": c["params"],
                         "viz_type": c["viz_type"], "dataset_uuid": c["dataset_uuid"]}))
        return buf.getvalue()

    # -- REST -----------------------------------------------------------------

    def _dashboard(self, dashboard_id: int) -> tuple[str, dict]:
        return next((u, d) for u, d in self.dashboards.items() if d["id"] == dashboard_id)

    def find_dashboard_by_slug(self, slug: str) -> dict | None:
        for u, d in self.dashboards.items():
            if d["slug"] == slug:
                return {"id": d["id"], "uuid": u}
        return None

    def get(self, path: str, **params) -> dict:
        if path == "/api/v1/dataset/":
            if (params.get("q") or {}).get("page", 0):
                return {"result": []}
            return {"result": [{"uuid": u, "table_name": d["table_name"], "schema": d.get("schema"),
                                "database": {"database_name": d["database_name"]}}
                               for u, d in self.datasets.items()]}
        kind, ident = path.rstrip("/").rsplit("/", 2)[-2:]
        if kind == "dashboard":
            u, d = self._dashboard(int(ident))
            return {"result": {"uuid": u, "json_metadata": json.dumps((d["yaml"] or {}).get("metadata") or {})}}
        if kind == "chart":
            c = self.charts[int(ident)]
            return {"result": {"dashboards": [{"id": x} for x in sorted(c["dashboards"])]}}
        raise AssertionError(f"unexpected GET {path}")

    def dashboard_charts(self, dashboard_id: int) -> list[dict]:
        return [{"id": cid, "slice_name": c["slice_name"]}
                for cid, c in self.charts.items() if dashboard_id in c["dashboards"]]

    def find_charts_by_name(self, name: str) -> list[dict]:
        return [{"id": cid, "slice_name": c["slice_name"], "uuid": c["uuid"]}
                for cid, c in self.charts.items() if c["slice_name"] == name]

    def put_json(self, path: str, payload: dict) -> Resp:
        kind, ident = path.rstrip("/").rsplit("/", 2)[-2:]
        if kind == "chart":
            if self.refuse_chart_puts or (self.refuse_unlinks and "dashboards" in payload):
                return Resp(403, "forbidden")
            c = self.charts[int(ident)]
            if "dashboards" in payload:
                c["dashboards"] = set(payload["dashboards"])
            if "slice_name" in payload:
                c["slice_name"] = payload["slice_name"]
            if "params" in payload:
                c["params"] = json.loads(payload["params"])
        return Resp()

    def delete_chart(self, chart_id: int) -> None:
        self.log.append(f"deleted chart {chart_id}")
        del self.charts[chart_id]

    # -- helpers for tests ------------------------------------------------------

    def linked(self, dashboard_id: int) -> dict[str, int]:
        return {c["slice_name"]: cid for cid, c in self.charts.items() if dashboard_id in c["dashboards"]}
