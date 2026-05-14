-- Extend the lease on an in-flight task.
--
-- Workers call this periodically while processing long-running tasks
-- (large monitor cycles, slow scrapers) to push out the
-- ``leased_until`` timestamp and avoid being reaped mid-flight.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = task_type ("monitor" or "scrape")
-- ARGV[3] = domain
-- ARGV[4] = task_id
-- ARGV[5] = new_leased_until (float timestamp)
--
-- Returns: 1 if extended, 0 if the inflight entry no longer exists
-- (the reaper already moved on, or the task was completed in another
-- branch — either way the caller should stop heartbeating).
--
-- Uses ZADD ``XX`` so a stale heartbeat from a worker whose lease was
-- already reaped doesn't reinstate the entry — that would race against
-- the reaper's re-enqueue and could double-execute the task.

local wtype = ARGV[1]
local task_type = ARGV[2]
local domain = ARGV[3]
local task_id = ARGV[4]
local new_until = tonumber(ARGV[5])

local member = task_type .. "|" .. domain .. "|" .. task_id
local updated = redis.call("ZADD", "inflight:" .. wtype, "XX", "CH", new_until, member)
return updated
