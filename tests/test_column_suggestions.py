"""A missing column names the dataset's real columns: close matches first (in
`candidates` and in the message), then the column list, capped. The count
alone left agents and people guessing."""

from chartwright.resolver import (
    LISTED_COLUMNS, Resolution, ResolvedDataset, _check_column, _check_metric, close_columns,
)


def _ds(columns):
    return ResolvedDataset(id=1, uuid="u", table="orders", schema=None,
                           database_name="warehouse", columns=columns, metrics=["revenue"])


COLS = ["order_id", "ordered_at", "region", "amount", "status"]


def _err(col, columns=COLS, what="groupby"):
    res = Resolution()
    _check_column(col, "Revenue by Region", _ds(columns), res, what)
    assert len(res.errors) == 1
    return res.errors[0]


def test_a_case_only_mismatch_is_the_first_suggestion():
    e = _err("Region")
    assert e.code == "column_not_found"
    assert e.candidates[0] == "region"
    assert "did you mean 'region'" in e.detail


def test_a_typo_suggests_the_column():
    assert "amount" in _err("amont").candidates


def test_no_close_match_still_lists_the_columns():
    e = _err("zzzz")
    assert e.candidates == []
    assert "did you mean" not in e.detail
    assert e.detail.endswith("Columns: order_id, ordered_at, region, amount, status")


def test_a_wide_dataset_lists_the_first_columns_and_counts_the_rest():
    wide = [f"col_{i:03d}" for i in range(200)]
    e = _err("missing", wide)
    assert f"col_{LISTED_COLUMNS - 1:03d}" in e.detail
    assert f"col_{LISTED_COLUMNS:03d}" not in e.detail
    assert f"and {200 - LISTED_COLUMNS} more" in e.detail


def test_the_column_inside_an_aggregate_gets_suggestions_too():
    res = Resolution()
    _check_metric("SUM(amont)", "Revenue", _ds(COLS), res)
    assert [e.code for e in res.errors] == ["column_not_found"]
    assert "amount" in res.errors[0].candidates


def test_candidates_reach_the_json_error():
    assert _err("Region").as_dict()["candidates"][0] == "region"


def test_at_most_three_suggestions():
    cols = ["amount", "amount_1", "amount_2", "amount_3", "amounts"]
    assert len(close_columns("amont", cols)) <= 3
