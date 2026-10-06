# Sundown

A six-tab dashboard for siting data centres on the US power grid, built from one spec with Chartwright. The data is EIA-930 hourly grid data and EIA-860M. It's the dashboard in Chartwright's README.

## What's here
- `sundown.json`: the spec. `chartwright validate`, `compile` and `advise` run on it as it is.
- `build_spec_sundown.py`: writes the spec from `facts.json` and a palette. `facts.json` holds the numbers the titles and time windows are built from.
- `palettes/`: seven palettes, `coral-teal-v2` by default. A palette sets every colour in the spec (its CSS, label colours and colour rules) and in the theme.
- `theme/`: the Superset theme the spec names (`"theme": "Sundown"`), and a script that creates or updates it through Superset's API.

## Rebuild it in another palette
```sh
python3 build_spec_sundown.py facts.json robins-egg > sundown.json
SUPERSET_URL=https://superset.example.com SUPERSET_PASSWORD=... python3 theme/create_theme.py robins-egg
chartwright apply sundown.json --profile <profile>
```

## What it needs to run
- **`apply` needs the warehouse.** The spec reads a Postgres warehouse with a `sundown` schema built from EIA-930 and EIA-860M, which are public domain. The data pipeline isn't included, so `apply` works only against such a warehouse.
- **The theme needs Superset 6.0 or later.** On 4.1 or 5.0, remove `"theme"` from the dashboard block; the dashboard keeps the colours its CSS sets.
