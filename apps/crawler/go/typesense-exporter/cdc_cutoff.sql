
WITH captured AS MATERIALIZED (
    SELECT clock_timestamp() AS captured_at
),
initial_writers AS MATERIALIZED (
    SELECT 1 AS present, locks.pid
    FROM captured
    JOIN pg_locks AS locks ON true
    WHERE locks.locktype = 'advisory'
      AND locks.database = (
          SELECT oid FROM pg_database WHERE datname = current_database()
      )
      AND locks.classid = (($1::bigint >> 32) & 4294967295)::oid
      AND locks.objid = ($1::bigint & 4294967295)::oid
      AND locks.objsubid = 1
      AND locks.mode = 'ShareLock'
      AND locks.granted
),
scan_gate AS MATERIALIZED (
    -- This dependency guarantees that the initial lock set is captured before
    -- activity is inspected. The no-op gate is replaced with pg_sleep only by
    -- the isolated concurrency test that commits a writer between the scans.
    SELECT true AS ready
    FROM (SELECT count(*) FROM initial_writers) AS captured_locks
),
gated_writers AS MATERIALIZED (
    SELECT initial_writers.present,
           initial_writers.pid
    FROM initial_writers
    CROSS JOIN scan_gate
),
writers AS MATERIALIZED (
    SELECT gated_writers.present,
           gated_writers.pid,
           activity.xact_start
    FROM gated_writers
    LEFT JOIN pg_stat_activity AS activity ON activity.pid = gated_writers.pid
),
classified AS MATERIALIZED (
    SELECT writers.present,
           writers.xact_start,
           CASE
               WHEN writers.xact_start IS NOT NULL THEN 'active'
               WHEN writers.pid IS NULL THEN 'unknown'
               WHEN EXISTS (
                   SELECT 1
                   FROM pg_locks AS recheck
                   WHERE recheck.locktype = 'advisory'
                     AND recheck.database = (
                         SELECT oid
                         FROM pg_database
                         WHERE datname = current_database()
                     )
                     AND recheck.classid =
                         (($1::bigint >> 32) & 4294967295)::oid
                     AND recheck.objid =
                         ($1::bigint & 4294967295)::oid
                     AND recheck.objsubid = 1
                     AND recheck.mode = 'ShareLock'
                     AND recheck.granted
                     AND recheck.pid = writers.pid
               ) THEN 'unknown'
               ELSE 'released'
           END AS writer_state
    FROM writers
)
SELECT captured.captured_at,
       LEAST(
           captured.captured_at,
           COALESCE(
               min(classified.xact_start) FILTER (
                   WHERE classified.writer_state = 'active'
               ),
               captured.captured_at
           )
       ) AS cutoff,
       count(classified.present) FILTER (
           WHERE classified.writer_state IN ('active', 'unknown')
       )::int AS active_writers,
       count(classified.present) FILTER (
           WHERE classified.writer_state = 'released'
       )::int AS released_writers,
       count(classified.present) FILTER (
           WHERE classified.writer_state = 'unknown'
       )::int AS unknown_writers
FROM captured
LEFT JOIN classified ON true
GROUP BY captured.captured_at
