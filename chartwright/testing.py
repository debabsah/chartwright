"""Stub resolution for offline compiles and golden tests: fake-but-deterministic
dataset ids/uuids so bundle bytes are stable without a live Superset."""

from __future__ import annotations

import uuid

from . import ids
from .resolver import ResolvedDataset, Resolution
from .spec import DashboardSpec


def stub_resolution(spec: DashboardSpec) -> Resolution:
    res = Resolution()
    refs = [c.dataset for c in spec.charts]
    refs += [f.dataset for f in spec.filters if f.type in ("select", "range")]
    for ref in refs:
        key = ref.key()
        if key in res.datasets:
            continue
        res.datasets[key] = ResolvedDataset(
            id=1000 + len(res.datasets),
            uuid=str(uuid.uuid5(ids.NAMESPACE, f"stub-dataset/{key}")),
            table=ref.table,
            schema=ref.schema_,
            database_name=ref.database,
            columns=[],
            metrics=[],
            main_dttm_col=None,
        )
    return res
