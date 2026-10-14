
SELECT job_posting_id::text AS posting_id
FROM lightpanda_b0_write_fence
WHERE engine_owner = 'go'
  AND shard_id = $1
  AND routing_epoch = $2
ORDER BY job_posting_id
