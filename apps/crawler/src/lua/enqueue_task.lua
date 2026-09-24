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
local b0_guard_key = "lightpanda-b0:legacy-guard"
local scrape_rotation_key = "ready:rotation:" .. wtype
local monitor_repair_key = "monitor_repair_due:" .. wtype
local b0_owner_key = "lightpanda-b0:producer-owner"

local function b0_safe_identifier(value)
    return type(value) == "string" and #value >= 1 and #value <= 128
        and string.match(value, "^[A-Za-z0-9][A-Za-z0-9_.:-]*$") ~= nil
end

local function b0_canonical_uint(value)
    if type(value) ~= "string" or (value ~= "0" and string.match(value, "^[1-9][0-9]*$") == nil) then
        return nil
    end
    local parsed = tonumber(value)
    if not parsed or parsed < 0 or parsed > 9999999999999 or parsed ~= math.floor(parsed)
        or tostring(parsed) ~= value then
        return nil
    end
    return parsed
end

local b0_guard_type = redis.call("TYPE", b0_guard_key)["ok"]
if b0_guard_type ~= "none" and b0_guard_type ~= "hash" then
    return redis.error_reply("lightpanda B0 legacy guard is corrupt")
end
local scrape_rotation_type = redis.call("TYPE", scrape_rotation_key)["ok"]
if scrape_rotation_type ~= "none" and scrape_rotation_type ~= "zset" then
    return redis.error_reply("scrape rotation index is corrupt")
end
local monitor_repair_type = redis.call("TYPE", monitor_repair_key)["ok"]
if monitor_repair_type ~= "none" and monitor_repair_type ~= "hash" then
    return redis.error_reply("monitor repair deadline index is corrupt")
end

-- A producer owner is Redis-wide so manual/off-mode processes cannot bypass
-- the Go cohort boundary merely because they lack the activation overlay.
-- The Go producer writes this manifest atomically with its route before its
-- first task mutation; rollback deletes it atomically with that route.
local b0_owner_type = redis.call("TYPE", b0_owner_key)["ok"]
if b0_owner_type ~= "none" and b0_owner_type ~= "hash" then
    return redis.error_reply("lightpanda B0 producer owner is corrupt")
end
if task_type == "scrape" and b0_owner_type == "none"
    and b0_guard_type == "hash" and redis.call("HLEN", b0_guard_key) > 0 then
    return redis.error_reply("lightpanda B0 producer owner is missing")
end
if task_type == "scrape" and b0_owner_type == "hash" then
    local owner_count = b0_canonical_uint(redis.call("HGET", b0_owner_key, "board_count"))
    local owner_namespace = redis.call("HGET", b0_owner_key, "namespace")
    local owner_shard = redis.call("HGET", b0_owner_key, "shard_id")
    local owner_epoch = redis.call("HGET", b0_owner_key, "routing_epoch")
    if redis.call("HGET", b0_owner_key, "schema")
            ~= "jobseek.lightpanda.producer-owner/v1"
        or redis.call("HGET", b0_owner_key, "engine_owner") ~= "go"
        or (redis.call("HGET", b0_owner_key, "cohort") ~= "c1"
            and redis.call("HGET", b0_owner_key, "cohort") ~= "c2"
            and redis.call("HGET", b0_owner_key, "cohort") ~= "c3"
            and redis.call("HGET", b0_owner_key, "cohort") ~= "c4")
        or not owner_count or owner_count < 1 or owner_count > 16
        or redis.call("HLEN", b0_owner_key) ~= 7 + owner_count
        or not b0_safe_identifier(owner_namespace) or not b0_safe_identifier(owner_shard)
        or not b0_canonical_uint(owner_epoch) or owner_epoch == "0" then
        return redis.error_reply("lightpanda B0 producer owner is corrupt")
    end
    local owner_members = 0
    for _, field in ipairs(redis.call("HKEYS", b0_owner_key)) do
        local slug = string.match(field, "^board_slug:(.+)$")
        if slug then
            if not b0_safe_identifier(slug)
                or redis.call("HGET", b0_owner_key, field) ~= "1" then
                return redis.error_reply("lightpanda B0 producer owner is corrupt")
            end
            owner_members = owner_members + 1
        elseif field ~= "schema" and field ~= "namespace" and field ~= "shard_id"
            and field ~= "routing_epoch" and field ~= "engine_owner"
            and field ~= "cohort" and field ~= "board_count" then
            return redis.error_reply("lightpanda B0 producer owner is corrupt")
        end
    end
    if owner_members ~= owner_count then
        return redis.error_reply("lightpanda B0 producer owner is corrupt")
    end
    local route_key = "lightpanda-b0:{" .. owner_namespace .. "}:route"
    if redis.call("TYPE", route_key)["ok"] ~= "hash"
        or redis.call("HLEN", route_key) ~= 4
        or redis.call("HGET", route_key, "shard_id") ~= owner_shard
        or redis.call("HGET", route_key, "routing_epoch") ~= owner_epoch
        or redis.call("HGET", route_key, "engine_owner") ~= "go"
        or b0_canonical_uint(redis.call("HGET", route_key, "claim_sequence")) == nil then
        return redis.error_reply("lightpanda B0 producer route is corrupt")
    end
    local board_id = nil
    for index = 8, #ARGV, 2 do
        if ARGV[index] == "board_id" then board_id = ARGV[index + 1] end
    end
    if not b0_safe_identifier(board_id) then
        return redis.error_reply("lightpanda B0 scrape board identity is missing")
    end
    local board_slug = redis.call("HGET", "board:" .. board_id, "board_slug")
    if not b0_safe_identifier(board_slug) then
        return redis.error_reply("lightpanda B0 scrape board identity is unavailable")
    end
    if redis.call("HGET", b0_owner_key, "board_slug:" .. board_slug) == "1" then
        return redis.error_reply("lightpanda B0 Go owner covers board")
    end
end
if task_type == "scrape" and redis.call("HEXISTS", b0_guard_key, task_id) == 1 then
    return 0
end

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
local recurring_monitor_score = false
local monitor_inflight = false
if task_type == "monitor" then
    recurring_monitor_score = redis.call("ZSCORE", recurring_key, task_id)
    monitor_inflight = redis.call("ZSCORE", "inflight:" .. wtype, inflight_member)
    already_scheduled = (
        redis.call("ZSCORE", first_time_key, task_id) ~= false or
        recurring_monitor_score ~= false or
        monitor_inflight ~= false
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

-- Config-repair syncs keep a board in the recurring tier while resetting its
-- durable next_check_at to now. If an older quarantine/backoff schedule is
-- already in Redis, logical-task deduplication must not leave that stale later
-- score in place. An inflight run cannot be queued concurrently, so preserve
-- the earliest requested repair deadline for reschedule/reaper to consume.
-- Active-board syncs request first-time priority and intentionally preserve
-- their existing cadence.
if task_type == "monitor" and not first_time then
    if monitor_inflight ~= false then
        local pending_repair = redis.call("HGET", monitor_repair_key, inflight_member)
        if pending_repair == false or score < tonumber(pending_repair) then
            redis.call("HSET", monitor_repair_key, inflight_member, score)
        end
    else
        if recurring_monitor_score ~= false and score < tonumber(recurring_monitor_score) then
            redis.call("ZADD", recurring_key, score, task_id)
        end
        -- Any marker without an inflight owner is stale. The recurring entry
        -- above (new, existing, or promoted) is now the durable schedule.
        redis.call("HDEL", monitor_repair_key, inflight_member)
    end
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
    local scrape_rotation_floor = tonumber(
        redis.call("ZSCORE", scrape_rotation_key, domain) or "0"
    )

    for tier = 0, 2 do
        redis.call("ZREM", "ready:" .. wtype .. ":" .. tier, domain)
    end

    if has_ft > 0 then
        if redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain) == 0 then
            redis.call("ZREM", scrape_rotation_key, domain)
        end
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
            redis.call(
                "ZADD",
                "ready:" .. wtype .. ":2",
                math.max(rl_at, scrape_rotation_floor, scr_score),
                domain
            )
        else
            redis.call("ZREM", scrape_rotation_key, domain)
        end
    end
end

return added
