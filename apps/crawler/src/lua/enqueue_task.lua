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
    -- Determine the correct ready queue tier and score.
    --
    -- First-time tasks always win (tier 0, ready_score=now to claim ASAP).
    -- For recurring tasks, the tier is chosen by MIN next-due score
    -- across the monitor and scrape buckets — this avoids the priority-
    -- inversion bug (#3016) where a domain with a far-future recurring
    -- monitor and a due-now scrape backlog gets parked in tier 1 at the
    -- monitor's future score and never claims its scrapes.
    -- Monitor wins ties vs scrape (strict-less-than).
    local has_ft = (
        redis.call("ZCARD", "ft_monitors_" .. wtype .. ":" .. domain) +
        redis.call("ZCARD", "ft_scrapes_" .. wtype .. ":" .. domain)
    )

    local rl_val = redis.call("GET", "ratelimit:" .. domain)
    local rl_at = 0
    if rl_val then
        rl_at = tonumber(rl_val)
    end

    local next_tier = nil
    local ready_score = nil

    if has_ft > 0 then
        next_tier = 0
        ready_score = math.max(rl_at, now)
    else
        local mon_score = nil
        if redis.call("ZCARD", "monitors_" .. wtype .. ":" .. domain) > 0 then
            local r3 = redis.call("ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
            if #r3 >= 2 then mon_score = tonumber(r3[2]) end
        end

        local scr_score = nil
        if redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain) > 0 then
            local r4 = redis.call("ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
            if #r4 >= 2 then scr_score = tonumber(r4[2]) end
        end

        if mon_score ~= nil then
            next_tier = 1
            ready_score = math.max(rl_at, mon_score)
        end
        if scr_score ~= nil and (mon_score == nil or scr_score < mon_score) then
            next_tier = 2
            ready_score = math.max(rl_at, scr_score)
        end
    end

    if next_tier ~= nil then
        -- Remove from other tiers, add to correct one
        for t = 0, 2 do
            if t ~= next_tier then
                redis.call("ZREM", "ready:" .. wtype .. ":" .. t, domain)
            end
        end

        -- Use plain ZADD (not NX) — upgrade tier if domain was in a lower tier
        redis.call("ZADD", "ready:" .. wtype .. ":" .. next_tier, ready_score, domain)
    end
end

return added
