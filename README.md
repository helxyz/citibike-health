# Citi Bike System Health

A monitoring pipeline and dashboard for **station availability** across New York's
Citi Bike network, built from 5-minute snapshots of the public
[GBFS feed](https://gbfs.lyft.com/gbfs/2.3/bkn/gbfs.json).

Public trip histories only record completed rides. They can't show a rider who
walked up to an empty dock and gave up. This project measures that directly.

**Live dashboard:** _<add your Streamlit link>_ · **Findings memo:** _<add link>_

## Metrics

| KPI | Definition | Why it matters |
|---|---|---|
| % time empty | Station-minutes with 0 rentable bikes ÷ observed station-minutes (offline stations excluded from "empty") | Riders who want a bike can't get one; lost trips |
| % time full | Station-minutes with 0 open docks ÷ observed station-minutes | Riders can't end a trip; worst rider experience |
| Peak availability | Share of weekday 7–10am and 4–7pm station-minutes with **≥1 bike AND ≥1 dock** | One number for "does the system work when people need it" |
| Median / p90 time to recover | For each fully observed empty episode, minutes until the station has a bike again | Proxy for rebalancing responsiveness |
| E-bike share | Time-weighted e-bikes ÷ all docked rentable bikes | Fleet mix; e-bikes drive revenue and charging needs |
| % time offline | Station-minutes where the station neither rents nor accepts returns | Hardware / operations issues |

**Method notes**
- Each snapshot's state is assumed to last until the next poll, **capped at 10 minutes**.
  Collector outages count as *unobserved*, never as empty or full.
- A recovery episode counts only if the station was seen going empty **and** coming back,
  with no data gap in between, so censored episodes don't bias the median.
- "Day" and "peak" are in New York time. Decommissioned stations (`is_installed = 0`) are dropped at collection.
- Boroughs come from a point-in-polygon join against NYC Open Data
  [Borough Boundaries](https://data.cityofnewyork.us/d/gthc-hcne). Jersey City and Hoboken come from the feed's regions.

## Data quality

`sql/checks.sql` runs before every publish. Any **fail** blocks the metrics update, so bad data never reaches the dashboard.

| Check | Severity |
|---|---|
| Negative bike/dock counts | fail |
| E-bikes > total bikes | fail |
| > 1% of rows reference an unknown station | fail |
| > 5% of rows exceed station capacity | warn |
| > 5% "stale" rows (renting but no report in 1h+) | warn |
| Any day with < 80% coverage of station-minutes | warn |

## Layout

```
collector/collect.py          poll GBFS, write data/status/date=YYYY-MM-DD/*.parquet
sql/kpis.sql                  all KPI logic (DuckDB SQL: window functions, gaps-and-islands, GROUPING SETS)
sql/checks.sql                data-quality checks
pipeline/build_metrics.py     run SQL + checks, write metrics/*.parquet
app/dashboard.py              Streamlit dashboard
.github/workflows/            collect every 30 min; rebuild metrics daily
```

## Run locally

```bash
pip install -r requirements.txt
python collector/collect.py --minutes 15 --interval 300
python pipeline/build_metrics.py --include-today
streamlit run app/dashboard.py
```
