# The README dashboard

[nyc_taxi_operations.json](nyc_taxi_operations.json) is the spec behind the
README's screenshot. An AI session with this repo's skill wrote it, unedited,
from this request:

> Build an operations dashboard for NYC yellow taxi trips from the
> nyc_yellow_taxi table in the examples database. I want headline numbers for
> trips, revenue, average fare, and average distance; daily trip and revenue
> trends; demand by hour of day and day of week; how riders pay; the busiest
> boroughs and pickup zones; and the spread of trip distances. Let viewers
> filter by date, pickup borough, and payment method.

`chartwright apply` confirmed every dataset, column, and metric the spec names against
the live instance, built the dashboard, and ran all 11 chart queries to prove
they show data. When a spec names columns that do not exist, the same pipeline
stops before anything is created and reports every problem in one pass:

```json
{
  "ok": false,
  "stage": "resolve",
  "errors": [
    {
      "code": "column_not_found",
      "chart": "Average Fare",
      "ref": "fare",
      "detail": "ad-hoc metric 'AVG(fare)': column 'fare' not on dataset 'nyc_yellow_taxi'"
    },
    {
      "code": "column_not_found",
      "chart": "Trips by Pickup Borough",
      "ref": "borough",
      "detail": "x_column 'borough' not on dataset 'nyc_yellow_taxi' (has 16 columns)"
    }
  ]
}
```

## Rebuild it

The data is public, so the whole demo reproduces end to end:

```bash
sandbox/up.sh                                     # disposable Superset on localhost:8098 (Docker)
pip install pandas pyarrow requests
python3 sandbox/load_nyc_taxi.py --month 2026-05  # one month of public TLC trip records
export CHARTWRIGHT_LOCAL_PASSWORD=admin
chartwright apply examples/nyc_taxi_operations.json --profile local
```

`sandbox/up.sh` prints the `[local]` profile block to save as
`~/.config/chartwright/profiles.toml`.
