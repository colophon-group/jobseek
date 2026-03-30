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

-- Recompute domain's tier
local has_ft = (
    redis.call("ZCARD", "ft_monitors_" .. wtype .. ":" .. domain) +
    redis.call("ZCARD", "ft_scrapes_" .. wtype .. ":" .. domain)
)
local has_monitors = redis.call("ZCARD", "monitors_" .. wtype .. ":" .. domain)
local has_scrapes = redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain)

-- Remove from all tiers
for t = 0, 2 do
    redis.call("ZREM", "ready:" .. wtype .. ":" .. t, domain)
end

-- Get rate limit
local rl_val = redis.call("GET", "ratelimit:" .. domain)
local rl_at = 0
if rl_val then
    rl_at = tonumber(rl_val)
end

-- Re-add to correct tier
if has_ft > 0 then
    redis.call("ZADD", "ready:" .. wtype .. ":0", math.max(rl_at, 0), domain)
elseif has_monitors > 0 then
    local next_mon = redis.call("ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    local mon_score = next_due
    if #next_mon >= 2 then
        mon_score = tonumber(next_mon[2])
    end
    redis.call("ZADD", "ready:" .. wtype .. ":1", math.max(rl_at, mon_score), domain)
elseif has_scrapes > 0 then
    local next_scr = redis.call("ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    local scr_score = next_due
    if #next_scr >= 2 then
        scr_score = tonumber(next_scr[2])
    end
    redis.call("ZADD", "ready:" .. wtype .. ":2", math.max(rl_at, scr_score), domain)
end

return 1
