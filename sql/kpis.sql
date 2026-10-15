-- KPI definitions. Inputs (registered by pipeline/build_metrics.py):
--   status   : raw 5-minute snapshots, one row per station per poll
--   stations : station list with borough, capacity, lat/lon
-- Times are stored in UTC and converted to New York time for "day" and "peak".

-- 1) One row per station per snapshot, plus how long that state lasted --------
CREATE OR REPLACE TABLE snap AS
WITH dedup AS (                          -- overlapping collector runs can double-poll
    SELECT DISTINCT ON (station_id, snapshot_ts) * FROM status
),
ordered AS (
    SELECT d.*, s.borough,
           LEAD(d.snapshot_ts) OVER (PARTITION BY d.station_id ORDER BY d.snapshot_ts) AS next_ts,
           LAG(d.snapshot_ts)  OVER (PARTITION BY d.station_id ORDER BY d.snapshot_ts) AS prev_ts
    FROM dedup d JOIN stations s USING (station_id)
)
SELECT *,
       timezone('America/New_York', snapshot_ts)                    AS local_ts,
       -- a state is assumed to last until the next poll (every 5 min), capped at
       -- 10 min so collector outages count as "unobserved", not as empty/full time
       LEAST(epoch(next_ts - snapshot_ts), 600) / 60.0              AS minutes,
       CASE WHEN is_renting = 0 AND is_returning = 0 THEN 'offline'
            WHEN num_bikes_available = 0              THEN 'empty'
            WHEN num_docks_available = 0              THEN 'full'
            ELSE 'ok' END                                           AS state,
       isodow(timezone('America/New_York', snapshot_ts)) <= 5
         AND hour(timezone('America/New_York', snapshot_ts)) IN (7, 8, 9, 16, 17, 18) AS is_peak
FROM ordered
WHERE next_ts IS NOT NULL;

-- 2) Daily KPIs per station -----------------------------------------------------
CREATE OR REPLACE TABLE daily_station_kpis AS
SELECT CAST(local_ts AS DATE)                                                AS day,
       station_id, borough,
       SUM(minutes)                                                          AS observed_min,
       COALESCE(SUM(minutes) FILTER (state = 'empty'), 0)                    AS empty_min,
       COALESCE(SUM(minutes) FILTER (state = 'full'), 0)                     AS full_min,
       COALESCE(SUM(minutes) FILTER (state = 'offline'), 0)                  AS offline_min,
       COALESCE(SUM(minutes) FILTER (is_peak AND state <> 'offline'), 0)     AS peak_min,
       COALESCE(SUM(minutes) FILTER (is_peak AND state = 'ok'), 0)           AS peak_ok_min,
       SUM(num_ebikes_available * minutes)                                   AS ebike_min,
       SUM(num_bikes_available * minutes)                                    AS bike_min
FROM snap
GROUP BY ALL;

-- 3) Empty episodes -> "time to recover" (gaps-and-islands) ---------------------
CREATE OR REPLACE TABLE empty_episodes AS
WITH marked AS (
    SELECT station_id, borough, snapshot_ts, next_ts, state,
           -- start a new island when the state changes OR the data has a gap
           CASE WHEN state IS DISTINCT FROM LAG(state) OVER w
                  OR epoch(snapshot_ts - prev_ts) > 600 THEN 1 ELSE 0 END AS new_island
    FROM snap
    WINDOW w AS (PARTITION BY station_id ORDER BY snapshot_ts)
),
numbered AS (
    SELECT *, SUM(new_island) OVER (PARTITION BY station_id ORDER BY snapshot_ts) AS island
    FROM marked
),
islands AS (
    SELECT station_id, borough, island, ANY_VALUE(state) AS state,
           MIN(snapshot_ts) AS start_ts, MAX(snapshot_ts) AS last_ts, MAX(next_ts) AS end_ts
    FROM numbered GROUP BY ALL
),
with_neighbors AS (
    SELECT *,
           LAG(last_ts)   OVER w AS prev_last, LAG(state)  OVER w AS prev_state,
           LEAD(start_ts) OVER w AS next_start, LEAD(state) OVER w AS next_state
    FROM islands
    WINDOW w AS (PARTITION BY station_id ORDER BY island)
)
SELECT station_id, borough, start_ts, end_ts,
       CAST(timezone('America/New_York', start_ts) AS DATE) AS day,
       epoch(end_ts - start_ts) / 60.0                      AS minutes_to_recover
FROM with_neighbors
WHERE state = 'empty'
  -- only fully observed episodes: we saw the station go empty AND come back,
  -- with no data gap on either side (a gap would hide when it really changed)
  AND prev_state IN ('ok', 'full') AND epoch(start_ts - prev_last) <= 600
  AND next_state IN ('ok', 'full') AND next_start = end_ts AND epoch(end_ts - last_ts) <= 600;

-- 4) Daily KPIs per borough, plus a city-wide "All" row (GROUPING SETS) ---------
CREATE OR REPLACE TABLE daily_borough_kpis AS
WITH base AS (
    SELECT day, COALESCE(borough, 'All') AS borough,
           COUNT(DISTINCT station_id)                       AS stations,
           SUM(observed_min)                                AS observed_min,
           SUM(empty_min)   / NULLIF(SUM(observed_min), 0)  AS pct_time_empty,
           SUM(full_min)    / NULLIF(SUM(observed_min), 0)  AS pct_time_full,
           SUM(offline_min) / NULLIF(SUM(observed_min), 0)  AS pct_time_offline,
           SUM(peak_ok_min) / NULLIF(SUM(peak_min), 0)      AS peak_availability,
           SUM(ebike_min)   / NULLIF(SUM(bike_min), 0)      AS ebike_share
    FROM daily_station_kpis
    GROUP BY GROUPING SETS ((day, borough), (day))
),
rec AS (
    SELECT day, COALESCE(borough, 'All') AS borough,
           COUNT(*)                                      AS empty_episodes,
           quantile_cont(minutes_to_recover, 0.5)        AS median_recovery_min,
           quantile_cont(minutes_to_recover, 0.9)        AS p90_recovery_min
    FROM empty_episodes
    GROUP BY GROUPING SETS ((day, borough), (day))
)
SELECT base.*, rec.empty_episodes, rec.median_recovery_min, rec.p90_recovery_min
FROM base LEFT JOIN rec USING (day, borough)
ORDER BY day, borough;
