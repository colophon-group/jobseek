-- Reschedule a task after processing. Adds it back to the recurring
-- per-domain queue and updates the domain's ready queue position.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = domain
-- ARGV[3] = task_id
-- ARGV[4] = task_type ("monitor" or "scrape")
-- ARGV[5] = next_due (float timestamp)
--
-- Returns: 1
--
-- Lease cleanup (added in #3159 / #3173):
--   This script also removes the inflight lease entry for the task,
--   since rescheduling means the worker successfully completed (or
--   failed in a way that already records the next_due backoff). The
--   reaper must not re-enqueue tasks that the worker has already
--   pushed back to the per-domain ZSET.

local wtype = ARGV[1]
local domain = ARGV[2]
local task_id = ARGV[3]
local task_type = ARGV[4]
local next_due = tonumber(ARGV[5])

-- Add to recurring queue (not first-time)
local queue_key
if task_type == "monitor" then
    queue_key = "monitors_" .. wtype .. ":" .. domain
else
    queue_key = "scrapes_" .. wtype .. ":" .. domain
end
redis.call("ZADD", queue_key, next_due, task_id)

-- Clear inflight lease entry — the task is back on the per-domain
-- queue, so the reaper must not double-enqueue it.
local inflight_member = task_type .. "|" .. domain .. "|" .. task_id
redis.call("ZREM", "inflight:" .. wtype, inflight_member)
redis.call("HDEL", "inflight_strikes:" .. wtype, inflight_member)

-- Remove stale representations from all tiers before rebuilding them.
for t = 0, 2 do
    redis.call("ZREM", "ready:" .. wtype .. ":" .. t, domain)
end

-- Get rate limit
local rl_val = redis.call("GET", "ratelimit:" .. domain)
local rl_at = 0
if rl_val then
    rl_at = tonumber(rl_val)
end

-- Recompute the domain's ready representations.
--
-- First-time tasks are strict inter-domain priority: if any ft_* queue has
-- work, the domain must stay in tier 0 even when a recurring task has an
-- older due timestamp (#3019). Once first-time work drains, recurring monitor
-- and scrape deadlines are advertised independently. Keeping only the current
-- minimum cannot promote a later monitor while an older scrape stays overdue.
local ft_mon_count = redis.call("ZCARD", "ft_monitors_" .. wtype .. ":" .. domain)
local ft_scr_count = redis.call("ZCARD", "ft_scrapes_" .. wtype .. ":" .. domain)

local ft_score = nil
if ft_mon_count > 0 then
    local r1 = redis.call("ZRANGE", "ft_monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    if #r1 >= 2 then ft_score = tonumber(r1[2]) end
end
if ft_scr_count > 0 then
    local r2 = redis.call("ZRANGE", "ft_scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    if #r2 >= 2 then
        local s = tonumber(r2[2])
        if ft_score == nil or s < ft_score then ft_score = s end
    end
end

local mon_score = nil
local has_monitors = redis.call("ZCARD", "monitors_" .. wtype .. ":" .. domain)
if has_monitors > 0 then
    local r3 = redis.call("ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    if #r3 >= 2 then mon_score = tonumber(r3[2]) end
end

local scr_score = nil
local has_scrapes = redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain)
if has_scrapes > 0 then
    local r4 = redis.call("ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    if #r4 >= 2 then scr_score = tonumber(r4[2]) end
end

if ft_score ~= nil then
    redis.call("ZADD", "ready:" .. wtype .. ":0", math.max(rl_at, ft_score), domain)
else
    if mon_score ~= nil then
        redis.call("ZADD", "ready:" .. wtype .. ":1", math.max(rl_at, mon_score), domain)
    end
    if scr_score ~= nil then
        redis.call("ZADD", "ready:" .. wtype .. ":2", math.max(rl_at, scr_score), domain)
    end
end

return 1
