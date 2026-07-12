#!/usr/bin/env python3
"""Load one month of NYC yellow-taxi trips into the sandbox's examples database.

Run on the Docker host, next to sandbox/up.sh, after the sandbox is up:

    pip install pandas pyarrow requests
    python3 sandbox/load_nyc_taxi.py --month 2026-05

The script downloads the public TLC trip records and the taxi zone lookup,
keeps one calendar month of trips, joins borough and zone names, derives the
columns the demo dashboard charts against (pickup hour, weekday, trip
minutes), merges the result into the sandbox's examples database, and
registers it as a dataset named nyc_yellow_taxi with pickup_time as the time
column. Re-running replaces the table and refreshes the dataset.

Data source: NYC Taxi & Limousine Commission trip record data,
https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

TRIP_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{month}.parquet"
ZONE_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
EXAMPLES_DB = "/app/superset_home/examples.db"
INCOMING_DB = "/app/superset_home/_nyc_taxi_incoming.db"

PAYMENT_NAMES = {
    0: "Flex fare",
    1: "Credit card",
    2: "Cash",
    3: "No charge",
    4: "Dispute",
    5: "Unknown",
    6: "Voided trip",
}


def download(url: str, dest: Path) -> Path:
    if dest.exists():
        print(f"using cached {dest.name}")
        return dest
    print(f"downloading {url} ...")
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    return dest


def build_table(month: str, work_dir: Path):
    import pandas as pd

    trips_file = download(TRIP_URL.format(month=month), work_dir / f"yellow_tripdata_{month}.parquet")
    zones_file = download(ZONE_URL, work_dir / "taxi_zone_lookup.csv")

    df = pd.read_parquet(
        trips_file,
        columns=[
            "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count",
            "trip_distance", "PULocationID", "DOLocationID", "payment_type",
            "fare_amount", "tip_amount", "tolls_amount", "total_amount",
        ],
    )
    raw = len(df)

    # Keep the calendar month and drop obvious recording errors: trips dated
    # outside the file's month, non-positive fares, and impossible distances
    # or durations. The thresholds are deliberately loose; the goal is a
    # faithful month, not a curated one.
    start = pd.Timestamp(f"{month}-01")
    end = start + pd.offsets.MonthBegin(1)
    minutes = (df["tpep_dropoff_datetime"] - df["tpep_pickup_datetime"]).dt.total_seconds() / 60
    df = df[
        (df["tpep_pickup_datetime"] >= start) & (df["tpep_pickup_datetime"] < end)
        & (df["total_amount"] > 0) & (df["total_amount"] < 1000)
        & (df["trip_distance"] > 0) & (df["trip_distance"] < 100)
        & (minutes > 0) & (minutes < 720)
    ].copy()

    zones = pd.read_csv(zones_file)[["LocationID", "Borough", "Zone"]]
    for side, key in (("pickup", "PULocationID"), ("dropoff", "DOLocationID")):
        df = df.merge(zones, left_on=key, right_on="LocationID", how="left")
        df[f"{side}_borough"] = df.pop("Borough").fillna("Unknown")
        df[f"{side}_zone"] = df.pop("Zone").fillna("Unknown")
        df = df.drop(columns=["LocationID", key])

    df["payment_method"] = df.pop("payment_type").map(PAYMENT_NAMES).fillna("Unknown")
    df["pickup_hour"] = df["tpep_pickup_datetime"].dt.hour
    # Superset sorts category axes alphabetically, so the weekday label
    # carries its own order ("1 Mon" .. "7 Sun" reads Monday-first).
    weekdays = {i: w for i, w in enumerate(
        ["1 Mon", "2 Tue", "3 Wed", "4 Thu", "5 Fri", "6 Sat", "7 Sun"])}
    df["pickup_weekday"] = df["tpep_pickup_datetime"].dt.dayofweek.map(weekdays)
    df["trip_minutes"] = (
        (df["tpep_dropoff_datetime"] - df["tpep_pickup_datetime"]).dt.total_seconds() / 60
    ).round(1)
    df["pickup_time"] = df.pop("tpep_pickup_datetime").dt.strftime("%Y-%m-%d %H:%M:%S")
    df["dropoff_time"] = df.pop("tpep_dropoff_datetime").dt.strftime("%Y-%m-%d %H:%M:%S")

    df = df[[
        "pickup_time", "dropoff_time", "pickup_hour", "pickup_weekday",
        "trip_minutes", "trip_distance", "passenger_count", "payment_method",
        "fare_amount", "tip_amount", "tolls_amount", "total_amount",
        "pickup_borough", "pickup_zone", "dropoff_borough", "dropoff_zone",
    ]]
    print(f"kept {len(df):,} of {raw:,} trips for {month}")
    return df


def merge_into_sandbox(df, table: str, container: str, work_dir: Path) -> None:
    build_file = work_dir / "nyc_taxi_build.db"
    build_file.unlink(missing_ok=True)
    # Close before docker cp so the finished file is what gets copied.
    conn = sqlite3.connect(build_file)
    try:
        df.to_sql(table, conn, index=False, if_exists="replace", chunksize=50_000)
        conn.commit()
    finally:
        conn.close()

    # The DROP must name main explicitly: SQLite resolves an unqualified
    # table name across attached databases too, and with no old table in
    # main it would drop the incoming table instead.
    merge_code = (
        "import sqlite3\n"
        f"c = sqlite3.connect('{EXAMPLES_DB}')\n"
        f"c.execute(\"ATTACH '{INCOMING_DB}' AS incoming\")\n"
        f"c.execute('DROP TABLE IF EXISTS main.{table}')\n"
        f"c.execute('CREATE TABLE main.{table} AS SELECT * FROM incoming.{table}')\n"
        f"c.execute('CREATE INDEX main.idx_{table}_pickup_time ON {table}(pickup_time)')\n"
        "c.commit()\n"
        f"print(c.execute('SELECT COUNT(*) FROM main.{table}').fetchone()[0], 'rows in examples db')\n"
    )
    subprocess.run(["docker", "cp", str(build_file), f"{container}:{INCOMING_DB}"], check=True)
    subprocess.run(["docker", "exec", container, "python", "-c", merge_code], check=True)
    subprocess.run(["docker", "exec", container, "rm", INCOMING_DB], check=True)
    build_file.unlink(missing_ok=True)


def register_dataset(base_url: str, username: str, password: str, table: str) -> None:
    import requests

    s = requests.Session()
    resp = s.post(
        f"{base_url}/api/v1/security/login",
        json={"username": username, "password": password, "provider": "db", "refresh": False},
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"login failed ({resp.status_code}); check the sandbox is up and the credentials")
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    csrf = s.get(f"{base_url}/api/v1/security/csrf_token/", headers=headers, timeout=30)
    headers["X-CSRFToken"] = csrf.json()["result"]
    headers["Referer"] = base_url

    dbs = s.get(
        f"{base_url}/api/v1/database/",
        params={"q": '(filters:!((col:database_name,opr:eq,value:examples)))'},
        headers=headers, timeout=30,
    ).json()["result"]
    if not dbs:
        sys.exit("no database named 'examples' in this Superset; run sandbox/up.sh first")
    db_id = dbs[0]["id"]

    existing = s.get(
        f"{base_url}/api/v1/dataset/",
        params={"q": f'(filters:!((col:table_name,opr:eq,value:{table})))'},
        headers=headers, timeout=30,
    ).json()["result"]
    if existing:
        ds_id = existing[0]["id"]
        s.put(f"{base_url}/api/v1/dataset/{ds_id}/refresh", headers=headers, timeout=60)
        print(f"dataset {table} already registered (id {ds_id}); columns refreshed")
    else:
        created = s.post(
            f"{base_url}/api/v1/dataset/",
            json={"database": db_id, "schema": "main", "table_name": table},
            headers=headers, timeout=60,
        )
        if created.status_code != 201:
            sys.exit(f"dataset registration failed ({created.status_code}): {created.text[:300]}")
        ds_id = created.json()["id"]
        print(f"dataset {table} registered (id {ds_id})")

    # Mark the pickup and dropoff times as temporal so time-series charts and
    # time filters have a time column to bind to.
    detail = s.get(f"{base_url}/api/v1/dataset/{ds_id}", headers=headers, timeout=30).json()["result"]
    columns = [
        {
            "id": c["id"],
            "column_name": c["column_name"],
            "type": c.get("type"),
            "is_dttm": c["column_name"] in ("pickup_time", "dropoff_time"),
        }
        for c in detail["columns"]
    ]
    updated = s.put(
        f"{base_url}/api/v1/dataset/{ds_id}",
        json={"main_dttm_col": "pickup_time", "columns": columns},
        headers=headers, timeout=60,
    )
    if updated.status_code != 200:
        sys.exit(f"marking time columns failed ({updated.status_code}): {updated.text[:300]}")
    print(f"time column set: pickup_time (dataset id {ds_id})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", required=True, help="calendar month to load, e.g. 2026-05")
    parser.add_argument("--container", default="chartwright-superset", help="sandbox container name")
    parser.add_argument("--url", default="http://localhost:8098", help="sandbox base URL")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--table", default="nyc_yellow_taxi")
    args = parser.parse_args()

    work_dir = Path(tempfile.gettempdir()) / "chartwright-nyc-taxi"
    work_dir.mkdir(exist_ok=True)

    df = build_table(args.month, work_dir)
    merge_into_sandbox(df, args.table, args.container, work_dir)
    register_dataset(args.url, args.username, args.password, args.table)
    print(f"done: query it in Superset as main.{args.table}")


if __name__ == "__main__":
    main()
