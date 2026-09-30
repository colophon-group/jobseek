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
-- ARGV[5] = optional claim token returned by tokenized claim
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
local monitor_repair_key = "monitor_repair_due:" .. wtype
local member = task_type .. "|" .. domain .. "|" .. task_id
local supplied_token = ARGV[5] or ""
local token_key = "inflight_tokens:" .. wtype
local token_type = redis.call("TYPE", token_key)["ok"]
if token_type ~= "none" and token_type ~= "hash" then
    return redis.error_reply("ordinary claim token index is corrupt")
end
if supplied_token ~= "" and (#supplied_token ~= 32 or
    string.find(supplied_token, "[^0-9a-f]") ~= nil) then
    return redis.error_reply("ordinary claim token is invalid")
end
local current_token = redis.call("HGET", token_key, member)
-- Legacy callers cannot mutate a tokenized lease; a tokenized caller cannot
-- mutate an absent/new generation. Expiry revokes authority before the reaper.
if current_token ~= false or supplied_token ~= "" then
    if current_token ~= supplied_token then return 0 end
    local deadline = redis.call("ZSCORE", "inflight:" .. wtype, member)
    local clock = redis.call("TIME")
    local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
    if deadline == false or tonumber(deadline) <= now then return 0 end
end

local monitor_repair_type = redis.call("TYPE", monitor_repair_key)["ok"]
if monitor_repair_type ~= "none" and monitor_repair_type ~= "hash" then
    return redis.error_reply("monitor repair deadline index is corrupt")
end

-- A completion from a claimant that lost ownership during the cold B0
-- transfer is stale. Do not remove its legacy lease/config; the operator
-- cutover and guarded reaper own that cleanup.
if task_type == "scrape"
    and redis.call("HEXISTS", "lightpanda-b0:legacy-guard", task_id) == 1 then
    return 0
end

-- Some monitor paths deliberately finish without rescheduling (disabled,
-- rerouted, or defensive early returns). If a repair sync arrived during that
-- run, retain ownership and expire the lease immediately so the reaper can
-- consume the deferred repair deadline instead of dropping it here.
if task_type == "monitor"
    and redis.call("HEXISTS", monitor_repair_key, member) == 1
    and redis.call("ZSCORE", "inflight:" .. wtype, member) ~= false then
    redis.call("ZADD", "inflight:" .. wtype, 0, member)
    redis.call("HDEL", "inflight_strikes:" .. wtype, member)
    return 0
end

local removed = redis.call("ZREM", "inflight:" .. wtype, member)
redis.call("HDEL", token_key, member)

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
