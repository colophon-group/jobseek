-- Remove a task's inflight lease entry.
--
-- Called after a task is successfully processed (or deliberately
-- dropped) — the per-domain ZSET no longer contains the task, so the
-- inflight set is the only remaining ownership record. Removing it
-- closes the lease so the reaper won't re-enqueue.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = task_type ("monitor" or "scrape")
-- ARGV[3] = domain
-- ARGV[4] = task_id
--
-- Returns: 1 if removed, 0 if not present (idempotent — duplicate
-- completions or completion after reaper already moved on are harmless).
--
-- Strikes hash (``inflight_strikes:<wtype>``) — if a strike entry was
-- recorded for this task by an earlier reap, clear it here. A
-- successful completion means we're back to a clean slate.

local wtype = ARGV[1]
local task_type = ARGV[2]
local domain = ARGV[3]
local task_id = ARGV[4]

local member = task_type .. "|" .. domain .. "|" .. task_id
local removed = redis.call("ZREM", "inflight:" .. wtype, member)

-- Always try to clear any leftover strike entry. Cheap (single HDEL)
-- and prevents a long-running task from accumulating phantom strikes
-- across many successful completions.
redis.call("HDEL", "inflight_strikes:" .. wtype, member)

-- Scrape config is derived scheduler state and is only useful while the
-- posting is represented by a queue, lease, or deadletter. Delete it when
-- completion drains the final representation. The reachability check and
-- UNLINK are atomic with respect to enqueue/reschedule Lua scripts, which
-- avoids wiping a concurrent relist or browser reroute.
if task_type == "scrape" then
    local config_key = "scrape:" .. task_id
    local config_domain = redis.call("HGET", config_key, "domain") or domain
    local simple_member = "scrape|" .. config_domain .. "|" .. task_id
    local browser_member = simple_member
    local reachable = (
        redis.call("ZSCORE", "ft_scrapes_simple:" .. config_domain, task_id) ~= false or
        redis.call("ZSCORE", "scrapes_simple:" .. config_domain, task_id) ~= false or
        redis.call("ZSCORE", "ft_scrapes_browser:" .. config_domain, task_id) ~= false or
        redis.call("ZSCORE", "scrapes_browser:" .. config_domain, task_id) ~= false or
        redis.call("ZSCORE", "inflight:simple", simple_member) ~= false or
        redis.call("ZSCORE", "inflight:browser", browser_member) ~= false or
        redis.call("ZSCORE", "deadletter:simple", simple_member) ~= false or
        redis.call("ZSCORE", "deadletter:browser", browser_member) ~= false
    )
    if not reachable then
        redis.call("UNLINK", config_key)
    end
end

return removed
