"""Lifecycle soak harness: the loop users actually live in, hundreds of
times, with invariants asserted every cycle.

Each cycle mutates the spec within the supported surface (add/remove/rename
charts, retitle, filter changes, layout regeneration, rows<->tabs) and runs
apply -> plan -> decompile, asserting:
  1. apply reaches stage=done (linkage + smoke included);
  2. surviving charts keep their slice ids (the stale-tab clobber root cause);
  3. `plan` is clean immediately after apply (fixpoint);
  4. decompile of the live dashboard is loss-free (representability);
  5. every ~10 cycles: absorb dry-run is a no-op (heights round-trip).

Mutations are drawn from a seeded RNG and validated through load_spec; an
invalid mutation is discarded and redrawn, so the harness can only ever apply
specs the contract accepts. On any invariant failure the cycle's spec, seed,
and report are dumped to a JSON file (the input for a minimized regression
test) and the run exits 1.

Usage:
    SDC_CI_PASSWORD=admin python tools/soak.py --base-url http://host:8098 \
        --cycles 500 --seed 1 [--slug sdc-soak]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pydantic import ValidationError

from chartwright.apply import apply as run_apply
from chartwright.client import SupersetClient
from chartwright.dashdiff import plan as run_plan
from chartwright.decompile import decompile_live
from chartwright.spec import load_spec

FIXTURE = REPO / "tests" / "fixtures" / "kitchen_sink.json"


class SpecMutator:
    """Seeded, validity-preserving spec mutations over the kitchen-sink
    vocabulary (real example-dataset fields, so specs resolve live)."""

    def __init__(self, base: dict, seed: int, slug: str):
        self.rng = random.Random(seed)
        self.counter = 0
        base = copy.deepcopy(base)
        base["dashboard"]["slug"] = slug
        base["dashboard"]["title"] = "Soak Command Center"
        # Template per chart type + the filter vocabulary, all from the fixture.
        self.chart_templates = {}
        for c in base["charts"]:
            self.chart_templates.setdefault(c["type"], copy.deepcopy(c))
        self.select_templates = [f for f in base["filters"] if f["type"] == "select"]
        self.time_filter = next((f for f in base["filters"] if f["type"] == "time_range"), None)
        histo = self.chart_templates.get("histogram", {})
        self.range_template = {
            "type": "range",
            "name": "Soak Window",
            "dataset": copy.deepcopy(histo.get("dataset") or base["charts"][0]["dataset"]),
            "column": histo.get("column", "quantity_ordered"),
            "le": 50,
        }
        # Start smaller than the full sink so add/remove both have room.
        keep = self.rng.sample(base["charts"], 6)
        self.spec = {
            "spec_version": base["spec_version"],
            "dashboard": base["dashboard"],
            "charts": copy.deepcopy(keep),
            "filters": copy.deepcopy(base["filters"]),
            "layout": {},
        }
        self._rebuild_layout(self.spec)
        load_spec(self.spec)  # the starting point must itself be valid

    # -- helpers --------------------------------------------------------------

    def _new_name(self, ctype: str) -> str:
        self.counter += 1
        return f"Soak {ctype} {self.counter}"

    def _rebuild_layout(self, spec: dict) -> None:
        names = [c["name"] for c in spec["charts"]]
        self.rng.shuffle(names)
        rows: list[list] = []
        if self.rng.random() < 0.2:
            rows.append([{"markdown": "## Soak run\nGenerated layout.", "height": 2}])
        i = 0
        while i < len(names):
            take = min(self.rng.choice([1, 2]), len(names) - i)
            rows.append(names[i:i + take])
            i += take
        if self.rng.random() < 0.25 and len(rows) >= 2:
            cut = self.rng.randint(1, len(rows) - 1)
            spec["layout"] = {"tabs": [
                {"title": "Tab A", "rows": rows[:cut]},
                {"title": "Tab B", "rows": rows[cut:]},
            ]}
        else:
            spec["layout"] = {"rows": rows}

    # -- mutations ------------------------------------------------------------

    def _add_chart(self, s):
        ctype = self.rng.choice(list(self.chart_templates))
        c = copy.deepcopy(self.chart_templates[ctype])
        c["name"] = self._new_name(ctype)
        s["charts"].append(c)
        return f"add_chart({c['name']})"

    def _remove_chart(self, s):
        if len(s["charts"]) <= 2:
            return None
        c = s["charts"].pop(self.rng.randrange(len(s["charts"])))
        # A scoped range filter may reference it; drop stale scopes.
        for f in s["filters"]:
            if f.get("charts") and c["name"] in f["charts"]:
                f["charts"] = [n for n in f["charts"] if n != c["name"]] or None
                if f["charts"] is None:
                    del f["charts"]
        return f"remove_chart({c['name']})"

    def _rename_chart(self, s):
        c = self.rng.choice(s["charts"])
        old, new = c["name"], self._new_name(c["type"])
        c["name"] = new
        for f in s["filters"]:
            if f.get("charts"):
                f["charts"] = [new if n == old else n for n in f["charts"]]
        return f"rename_chart({old} -> {new})"

    def _tweak_chart(self, s):
        c = self.rng.choice(s["charts"])
        if "row_limit" in c or self.rng.random() < 0.5:
            c["row_limit"] = self.rng.choice([5, 20, 100])
        else:
            c["height"] = self.rng.choice([6, 8, 10])
        return f"tweak_chart({c['name']})"

    def _retitle(self, s):
        s["dashboard"]["title"] = f"Soak Command Center v{self.rng.randint(2, 999)}"
        return "retitle"

    def _toggle_select_filter(self, s):
        current = [f for f in s["filters"] if f["type"] == "select"]
        if current and self.rng.random() < 0.5:
            s["filters"].remove(self.rng.choice(current))
            return "remove_select_filter"
        tpl = copy.deepcopy(self.rng.choice(self.select_templates))
        tpl["name"] = f"{tpl['name']} {self.rng.randint(2, 999)}"
        s["filters"].append(tpl)
        return "add_select_filter"

    def _toggle_range_filter(self, s):
        current = [f for f in s["filters"] if f["type"] == "range"]
        if current:
            f = current[0]
            if self.rng.random() < 0.4:
                s["filters"].remove(f)
                return "remove_range_filter"
            f["charts"] = [c["name"] for c in self.rng.sample(s["charts"], min(2, len(s["charts"])))]
            f["le"] = self.rng.choice([10, 50, 90])
            return "rescope_range_filter"
        s["filters"].append(copy.deepcopy(self.range_template))
        return "add_range_filter"

    def _relayout(self, s):
        self._rebuild_layout(s)
        return "relayout"

    def mutate(self) -> str:
        ops = [self._add_chart, self._remove_chart, self._rename_chart,
               self._tweak_chart, self._retitle, self._toggle_select_filter,
               self._toggle_range_filter, self._relayout]
        for _ in range(10):
            candidate = copy.deepcopy(self.spec)
            op = self.rng.choice(ops)
            desc = op(candidate)
            if desc is None:
                continue
            self._rebuild_layout(candidate)
            try:
                load_spec(candidate)
            except ValidationError:
                continue
            self.spec = candidate
            return desc
        return "noop(no valid mutation drawn)"


def dump_failure(kind: str, cycle: int, seed: int, op: str, spec: dict, detail) -> Path:
    out = REPO / f"soak-failure-{kind}-cycle{cycle}.json"
    out.write_text(json.dumps(
        {"kind": kind, "cycle": cycle, "seed": seed, "op": op,
         "spec": spec, "detail": detail}, indent=2, default=str), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--cycles", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--slug", default="sdc-soak")
    ap.add_argument("--profile", default="soak",
                    help="backup-dir namespace; use a distinct label per target instance")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")

    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mut = SpecMutator(base, args.seed, args.slug)
    client = SupersetClient(args.base_url, args.username, password)
    client.login()

    prev_ids: dict[str, int] = {}
    t0 = time.time()
    for cycle in range(1, args.cycles + 1):
        op = mut.mutate() if cycle > 1 else "initial"
        spec = load_spec(mut.spec)

        report = run_apply(spec, client, args.profile)
        if not report.ok:
            print(dump_failure("apply", cycle, args.seed, op, mut.spec, json.loads(report.to_json())))
            print(f"FAIL cycle {cycle} [{op}]: apply ended at stage {report.stage!r}: {report.import_detail}")
            return 1

        live = {c["slice_name"]: c["id"] for c in client.dashboard_charts(report.dashboard_id)}
        churned = {n: (prev_ids[n], live[n]) for n in live if n in prev_ids and prev_ids[n] != live[n]}
        if churned:
            print(dump_failure("id-churn", cycle, args.seed, op, mut.spec, churned))
            print(f"FAIL cycle {cycle} [{op}]: surviving slice ids churned: {churned}")
            return 1
        prev_ids = live

        p = run_plan(spec, client)
        if not p.clean:
            print(dump_failure("plan", cycle, args.seed, op, mut.spec, json.loads(p.to_json())))
            print(f"FAIL cycle {cycle} [{op}]: plan not clean immediately after apply")
            return 1

        dec = decompile_live(args.slug, client)
        if dec.losses:
            print(dump_failure("losses", cycle, args.seed, op, mut.spec,
                               [loss.as_dict() for loss in dec.losses]))
            print(f"FAIL cycle {cycle} [{op}]: decompile of own dashboard is lossy")
            return 1

        if cycle % 10 == 0:
            from chartwright.absorb import absorb_heights
            detail = client.get(f"/api/v1/dashboard/{report.dashboard_id}")["result"]
            live_position = json.loads(detail.get("position_json") or "{}")
            _, ar = absorb_heights(spec, mut.spec, live_position)
            if ar.absorbed:
                print(dump_failure("absorb", cycle, args.seed, op, mut.spec,
                                   json.loads(ar.to_json())))
                print(f"FAIL cycle {cycle} [{op}]: absorb dry-run not a no-op (heights drifted)")
                return 1

        if cycle % 10 == 0 or cycle == 1:
            rate = cycle / max(time.time() - t0, 1e-9)
            print(f"cycle {cycle}/{args.cycles} ok [{op}] charts={len(mut.spec['charts'])} "
                  f"filters={len(mut.spec['filters'])} ({rate:.2f} cyc/s)", flush=True)

    print(f"SOAK PASS: {args.cycles} cycles, seed {args.seed}, {int(time.time() - t0)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
