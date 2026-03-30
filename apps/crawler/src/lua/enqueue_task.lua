-- Enqueue a task into a per-domain ZSET and ensure the domain
-- appears in the correct ready queue tier.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = domain
-- ARGV[3] = task_id
-- ARGV[4] = score (next_check_at or next_scrape_at, 0 for first-time)
-- ARGV[5] = task_type ("monitor" or "scrape")
-- ARGV[6] = first_time ("1" or "0")
-- ARGV[7] = now (float timestamp)
--
-- Returns: 1 if newly added, 0 if already existed

local wtype = ARGV[1]
local domain = ARGV[2]
local task_id = ARGV[3]
local score = tonumber(ARGV[4])
local task_type = ARGV[5]
local first_time = ARGV[6] == "1"
local now = tonumber(ARGV[7])

-- Build the per-domain queue key
local prefix
if first_time then
    prefix = "ft_"
else
    prefix = ""
end
if task_type == "monitor" then
    prefix = prefix .. "monitors_"
else
    prefix = prefix .. "scrapes_"
end
local queue_key = prefix .. wtype .. ":" .. domain

-- ZADD NX — only add if not already present
local added = redis.call("ZADD", queue_key, "NX", score, task_id)

if added == 1 then
    -- Determine the correct ready queue tier
    local has_ft = (
        redis.call("ZCARD", "ft_monitors_" .. wtype .. ":" .. domain) +
        redis.call("ZCARD", "ft_scrapes_" .. wtype .. ":" .. domain)
    )

    local tier
    if has_ft > 0 then
        tier = 0
    elseif redis.call("ZCARD", "monitors_" .. wtype .. ":" .. domain) > 0 then
        tier = 1
    else
        tier = 2
    end

    -- Compute ready score: max(rate_limit_at, task_due)
    local rl_val = redis.call("GET", "ratelimit:" .. domain)
    local rl_at = 0
    if rl_val then
        rl_at = tonumber(rl_val)
    end

    local ready_score
    if first_time then
        ready_score = math.max(rl_at, now)
    else
        ready_score = math.max(rl_at, score)
    end

    -- Remove from other tiers, add to correct one
    for t = 0, 2 do
        if t ~= tier then
            redis.call("ZREM", "ready:" .. wtype .. ":" .. t, domain)
        end
    end

    -- Use plain ZADD (not NX) — upgrade tier if domain was in a lower tier
    redis.call("ZADD", "ready:" .. wtype .. ":" .. tier, ready_score, domain)
end

return added
