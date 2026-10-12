"""Build KPI tables from raw snapshots, run data-quality checks, write metrics/.

    python pipeline/build_metrics.py              # complete days only (default)
    python pipeline/build_metrics.py --include-today

Metrics are only written if every 'fail'-severity check passes, so a bad feed
day never reaches the dashboard.
"""
import argparse, json, sys
from pathlib import Path

import duckdb
import pandas as pd
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[1]
NON_NYC = {"JC District": "Jersey City", "Hoboken District": "Hoboken"}


def assign_boroughs(stations: pd.DataFrame, root: Path) -> pd.DataFrame:
    BOROUGHS = root / "data" / "reference" / "borough_boundaries.geojson"
    stations = stations.copy()
    if not BOROUGHS.exists():
        print(f"WARNING: {BOROUGHS.name} missing - using feed regions instead of boroughs")
        stations["borough"] = stations["region_name"].fillna("Unknown")
        return stations
    feats = json.loads(BOROUGHS.read_text())["features"]
    def name_of(props):
        for k in ("boroname", "BoroName", "boro_name", "borough"):
            if k in props:
                return props[k]
        raise KeyError(f"no borough name field in {list(props)}")
    polys = [(name_of(f["properties"]), shape(f["geometry"])) for f in feats]

    def lookup(row):
        if row.region_name in NON_NYC:
            return NON_NYC[row.region_name]
        p = Point(row.lon, row.lat)
        for name, poly in polys:
            if poly.contains(p):
                return name
        # shoreline stations can sit a few metres outside the clipped polygon
        name, poly = min(polys, key=lambda np_: np_[1].distance(p))
        return name if poly.distance(p) < 0.002 else "Unknown"   # ~200 m

    stations["borough"] = [lookup(r) for r in stations.itertuples()]
    return stations


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-today", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    root = Path(a.root)

    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    stations = assign_boroughs(pd.read_parquet(root / "data/stations/stations.parquet"), root)
    con.register("stations", stations)
    con.execute(f"""CREATE VIEW status AS
        SELECT * FROM read_parquet('{root}/data/status/*/*.parquet', hive_partitioning = false)""")

    con.execute((ROOT / "sql/kpis.sql").read_text())
    if not a.include_today:   # today is still partial in New York time
        for t in ("daily_station_kpis", "daily_borough_kpis", "empty_episodes"):
            con.execute(f"""DELETE FROM {t}
                WHERE day >= CAST(timezone('America/New_York', now()) AS DATE)""")

    checks = con.execute((ROOT / "sql/checks.sql").read_text()).df()
    lower_bound = checks.check_name.str.startswith("min_")
    bad = (checks.value < checks.threshold).where(lower_bound, checks.value > checks.threshold)
    checks["status"] = ["pass" if not b else s for b, s in zip(bad, checks.severity)]
    checks["run_at"] = pd.Timestamp.now(tz="UTC")
    print(checks[["check_name", "value", "threshold", "status"]].to_string(index=False))

    out = root / "metrics"
    out.mkdir(exist_ok=True)
    checks.to_parquet(out / "data_quality.parquet", index=False)
    if (checks.status == "fail").any():
        sys.exit("Data-quality check failed - metrics NOT published.")

    for t in ("daily_station_kpis", "daily_borough_kpis", "empty_episodes"):
        con.table(t).df().to_parquet(out / f"{t}.parquet", index=False)
    stations.to_parquet(out / "stations.parquet", index=False)
    print("metrics written:", sorted(p.name for p in out.glob("*.parquet")))


if __name__ == "__main__":
    main()
