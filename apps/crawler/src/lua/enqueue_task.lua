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
-- ARGV[8..] = optional scrape config field/value pairs
--
-- Returns: 1 if newly added, 0 if already existed

local wtype = ARGV[1]
local domain = ARGV[2]
local task_id = ARGV[3]
local score = tonumber(ARGV[4])
local task_type = ARGV[5]
local first_time = ARGV[6] == "1"
local now = tonumber(ARGV[7])

-- Scrape queue membership and its config hash are one lifecycle record. Keep
-- them in this script so an orphan-prune/completion script can never observe
-- a newly queued posting without its new config (or vice versa). Monitor
-- hashes remain deploy-owned and are intentionally written by sync in bulk.
if task_type == "scrape" then
    local config_args = {"domain", domain}
    for index = 8, #ARGV, 2 do
        if ARGV[index + 1] ~= nil then
            table.insert(config_args, ARGV[index])
            table.insert(config_args, ARGV[index + 1])
        end
    end
    redis.call("HSET", "scrape:" .. task_id, unpack(config_args))
end

-- Build both lifecycle queue keys. A monitor is one logical schedule across
-- its first-time, recurring, and inflight representations. Checking only the
-- requested ZSET lets every deploy-time sync add an already-recurring board
-- to ft_monitors as a duplicate (#6135).
local task_prefix
if task_type == "monitor" then
    task_prefix = "monitors_"
else
    task_prefix = "scrapes_"
end
local first_time_key = "ft_" .. task_prefix .. wtype .. ":" .. domain
local recurring_key = task_prefix .. wtype .. ":" .. domain
local queue_key = first_time and first_time_key or recurring_key
local inflight_member = task_type .. "|" .. domain .. "|" .. task_id

local already_scheduled
if task_type == "monitor" then
    already_scheduled = (
        redis.call("ZSCORE", first_time_key, task_id) ~= false or
        redis.call("ZSCORE", recurring_key, task_id) ~= false or
        redis.call("ZSCORE", "inflight:" .. wtype, inflight_member) ~= false
    )
else
    -- Scrape fallbacks intentionally enqueue the same posting while the
    -- previous step is inflight, and relisting can promote a recurring scrape
    -- into the first-time tier. Keep their established per-ZSET NX semantics.
    already_scheduled = redis.call("ZSCORE", queue_key, task_id) ~= false
end
local added = 0
if not already_scheduled then
    added = redis.call("ZADD", queue_key, "NX", score, task_id)
end

-- Always recompute ready membership. Besides making a new schedule visible,
-- this repairs a missing/stale ready-domain entry when sync only rewrites the
-- board hash and the logical task already exists elsewhere.
do
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
