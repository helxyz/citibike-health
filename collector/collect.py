"""Poll Citi Bike's public GBFS feed and save station snapshots as Parquet.

Each run polls station_status every --interval seconds for --minutes minutes,
then writes ONE parquet file: data/status/date=YYYY-MM-DD/<run start>.parquet
It also refreshes the station list: data/stations/stations.parquet

Run locally:  python collector/collect.py --minutes 10 --interval 60
"""
import argparse, json, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE = "https://gbfs.lyft.com/gbfs/2.3/bkn/en"
STATUS_COLS = ["station_id", "num_bikes_available", "num_ebikes_available",
               "num_bikes_disabled", "num_docks_available", "num_docks_disabled",
               "is_installed", "is_renting", "is_returning", "last_reported"]


def fetch(feed: str) -> dict:
    req = urllib.request.Request(f"{BASE}/{feed}.json",
                                 headers={"User-Agent": "citibike-health-student-project"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:  # network blips shouldn't kill the whole run
            print(f"fetch {feed} failed (attempt {attempt + 1}): {e}")
            time.sleep(5)
    raise RuntimeError(f"could not fetch {feed}")


def poll_status() -> pd.DataFrame:
    feed = fetch("station_status")
    df = pd.DataFrame(feed["data"]["stations"])
    for c in STATUS_COLS:  # tolerate a missing optional field
        if c not in df:
            df[c] = 0
    df = df[STATUS_COLS]
    df = df[df["is_installed"] == 1]  # decommissioned stations report all zeros
    df["snapshot_ts"] = pd.Timestamp(datetime.now(timezone.utc)).floor("s")
    df["feed_last_updated"] = pd.to_datetime(feed.get("last_updated", 0), unit="s", utc=True)
    df["last_reported"] = pd.to_datetime(df["last_reported"], unit="s", utc=True)
    return df


def save_stations(root: Path) -> None:
    info = pd.DataFrame(fetch("station_information")["data"]["stations"])
    regions = pd.DataFrame(fetch("system_regions")["data"]["regions"])
    info = info[["station_id", "name", "short_name", "lat", "lon", "region_id", "capacity"]]
    info = info.merge(regions[["region_id", "name"]].rename(columns={"name": "region_name"}),
                      on="region_id", how="left")
    out = root / "data" / "stations"
    out.mkdir(parents=True, exist_ok=True)
    info.to_parquet(out / "stations.parquet", index=False)
    print(f"saved {len(info)} stations")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=40)
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    root = Path(a.root)

    start = datetime.now(timezone.utc)
    save_stations(root)
    frames, deadline = [], time.time() + a.minutes * 60
    while True:
        t0 = time.time()
        try:
            frames.append(poll_status())
            print(f"{datetime.now(timezone.utc):%H:%M:%S} polled {len(frames[-1])} stations")
        except RuntimeError as e:
            print(e)
        if t0 + a.interval > deadline:
            break
        time.sleep(max(0, a.interval - (time.time() - t0)))

    if not frames:
        raise SystemExit("no snapshots collected")
    out = root / "data" / "status" / f"date={start:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{start:%H%M%S}.parquet"
    pd.concat(frames).to_parquet(path, index=False, compression="zstd")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
