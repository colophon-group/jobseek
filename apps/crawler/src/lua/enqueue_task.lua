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
    -- First-time tasks always win and suppress recurring representations.
    -- Otherwise advertise monitor and scrape deadlines independently. A
    -- single MIN-score representation cannot promote a later monitor deadline
    -- after an older scrape backlog becomes due.
    local has_ft = (
        redis.call("ZCARD", "ft_monitors_" .. wtype .. ":" .. domain) +
        redis.call("ZCARD", "ft_scrapes_" .. wtype .. ":" .. domain)
    )

    local rl_val = redis.call("GET", "ratelimit:" .. domain)
    local rl_at = 0
    if rl_val then
        rl_at = tonumber(rl_val)
    end

    for tier = 0, 2 do
        redis.call("ZREM", "ready:" .. wtype .. ":" .. tier, domain)
    end

    if has_ft > 0 then
        redis.call("ZADD", "ready:" .. wtype .. ":0", math.max(rl_at, now), domain)
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
            redis.call("ZADD", "ready:" .. wtype .. ":1", math.max(rl_at, mon_score), domain)
        end
        if scr_score ~= nil then
            redis.call("ZADD", "ready:" .. wtype .. ":2", math.max(rl_at, scr_score), domain)
        end
    end
end

return added
