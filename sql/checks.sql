-- Data-quality checks. Each returns: check_name, value, threshold, severity.
-- severity 'fail' blocks publishing metrics; 'warn' is reported only.
SELECT 'negative_counts' AS check_name,
       COUNT(*)::DOUBLE AS value, 0 AS threshold, 'fail' AS severity
FROM status
WHERE num_bikes_available < 0 OR num_docks_available < 0 OR num_ebikes_available < 0
UNION ALL
SELECT 'ebikes_exceed_bikes', COUNT(*), 0, 'fail'
FROM status WHERE num_ebikes_available > num_bikes_available
UNION ALL
SELECT 'pct_rows_unknown_station',
       100.0 * COUNT(*) FILTER (s.station_id IS NULL) / COUNT(*), 1, 'fail'
FROM status st LEFT JOIN stations s USING (station_id)
UNION ALL
SELECT 'pct_rows_over_capacity',     -- bikes + docks shouldn't exceed station size
       100.0 * COUNT(*) FILTER (num_bikes_available + num_bikes_disabled
                                + num_docks_available + num_docks_disabled > s.capacity
                                AND s.capacity > 0) / COUNT(*), 5, 'warn'
FROM status st JOIN stations s USING (station_id)
UNION ALL
SELECT 'pct_stale_reports',          -- station says it's renting but hasn't reported in 1h+
       100.0 * COUNT(*) FILTER (is_renting = 1 AND snapshot_ts - last_reported > INTERVAL 1 HOUR)
       / COUNT(*), 5, 'warn'
FROM status
UNION ALL
SELECT 'min_daily_coverage_pct',     -- worst day's observed share of station-minutes
       MIN(cov), 80, 'warn'
FROM (SELECT day, 100.0 * SUM(observed_min) / (COUNT(DISTINCT station_id) * 1440) AS cov
      FROM daily_station_kpis GROUP BY day);
