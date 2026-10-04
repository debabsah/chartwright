"""Stub resolution for offline compiles and golden tests: fake-but-deterministic
dataset ids/uuids so bundle bytes are stable without a live Superset."""

from __future__ import annotations

import io
import uuid
import zipfile
from typing import Callable

import yaml

from . import ids
from .resolver import ResolvedDataset, Resolution
from .spec import DATASET_FILTER_TYPES, DashboardSpec


def stub_resolution(spec: DashboardSpec) -> Resolution:
    res = Resolution()
    refs = [c.dataset for c in spec.charts]
    refs += [f.dataset for f in spec.filters if f.type in DATASET_FILTER_TYPES]
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


def edit_bundle(bundle: bytes, edit: Callable[[str, dict], None],
                add: dict[str, dict] | None = None) -> bytes:
    """The same bundle with each yaml passed through `edit(path, doc)` (in place),
    plus `add` (path -> doc): a live dashboard whose stored state drifted from
    what the tool wrote, or an export that ships more than a compiled bundle."""
    src, out = zipfile.ZipFile(io.BytesIO(bundle)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            blob = src.read(name)
            if name.endswith(".yaml"):
                doc = yaml.safe_load(blob)
                edit(name, doc)
                blob = yaml.safe_dump(doc).encode()
            dst.writestr(name, blob)
        for name, doc in (add or {}).items():
            dst.writestr(name, yaml.safe_dump(doc))
    return out.getvalue()
