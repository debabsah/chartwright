"""Bounded cardinality probes for data-aware rules.

One grouped COUNT per (dataset, column), capped: rules only ever need
"more than N distinct values", never the true count, so the query carries
row_limit = cap + 1 and the answer is min(cardinality, cap + 1). Probes are
cached per run and any HTTP/parse failure degrades to None (the rule skips;
advice never fails a pipeline over a probe).
"""

from __future__ import annotations

from ..client import SupersetAPIError, SupersetClient
from ..resolver import ResolvedDataset

_COUNT_METRIC = {"expressionType": "SQL", "sqlExpression": "COUNT(*)", "label": "count",
                 "optionName": "metric_sdc_probe"}


class CardinalityProber:
    def __init__(self, client: SupersetClient):
        self.client = client
        self._cache: dict[tuple[int, str, int], int | None] = {}

    def count_up_to(self, ds: ResolvedDataset, column: str, cap: int) -> int | None:
        """Distinct values in `column`, saturating at cap + 1; None on failure."""
        key = (ds.id, column, cap)
        if key in self._cache:
            return self._cache[key]
        # A smaller answer under a bigger cap is exact; reuse it.
        for (i, c, k), v in self._cache.items():
            if i == ds.id and c == column and v is not None and (v <= k or k >= cap):
                self._cache[key] = min(v, cap + 1)
                return self._cache[key]
        ctx = {
            "datasource": {"id": ds.id, "type": "table"},
            "queries": [{
                "columns": [column],
                "metrics": [_COUNT_METRIC],
                "filters": [],
                "orderby": [],
                "row_limit": cap + 1,
                "time_range": "No filter",
            }],
            "result_format": "json",
            "result_type": "full",
        }
        try:
            r = self.client.chart_data(ctx)
            if r.status_code != 200:
                self._cache[key] = None
                return None
            result = r.json()["result"]
            n = sum(len(q.get("data") or []) for q in result)
        except (SupersetAPIError, KeyError, ValueError):
            n = None
        self._cache[key] = n
        return n

    def more_than(self, ds: ResolvedDataset, column: str, n: int) -> bool | None:
        c = self.count_up_to(ds, column, n)
        return None if c is None else c > n
