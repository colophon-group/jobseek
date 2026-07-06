-- Reap expired in-flight lease entries and re-enqueue the tasks.
--
-- Called periodically by the worker reaper coroutine. Scans
-- ``inflight:<wtype>`` for entries with score (leased_until) < now
-- and either re-enqueues them to their per-domain ZSET or, if the
-- strike count exceeds ``max_strikes``, moves them to the dead-letter
-- ZSET ``deadletter:<wtype>``.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = now (float timestamp)
-- ARGV[3] = max_entries (int — cap per call to bound script runtime)
-- ARGV[4] = max_strikes (int — entries with strike count >= this go
--           to the dead-letter ZSET instead of being re-enqueued)
-- ARGV[5] = retry_score (float — score to write back to the per-domain
--           ZSET; typically ``now`` for "retry ASAP")
--
-- Returns: {reenqueued, dead_lettered, missing_config}
--   - reenqueued: int — entries successfully re-enqueued
--   - dead_lettered: int — entries that exceeded max_strikes
--   - missing_config: int — entries whose ``board:<id>`` / ``scrape:<id>``
--     hash was missing, so they were dropped without re-enqueue
--
-- Idempotence: ZADD NX on the per-domain queue means a duplicate of
-- the same task already present (e.g. monitor re-enqueued by sync
-- while a worker was killed) is silently de-duped — we don't
-- double-schedule.

local wtype = ARGV[1]
local now = tonumber(ARGV[2])
local max_entries = tonumber(ARGV[3]) or 100
local max_strikes = tonumber(ARGV[4]) or 3
local retry_score = tonumber(ARGV[5]) or now

local inflight_key = "inflight:" .. wtype
local strikes_key = "inflight_strikes:" .. wtype
local deadletter_key = "deadletter:" .. wtype

-- Find expired entries, oldest first.
local expired = redis.call(
    "ZRANGEBYSCORE",
    inflight_key,
    "-inf",
    tostring(now),
    "LIMIT", 0, max_entries
)

local reenqueued = 0
local dead_lettered = 0
local missing_config = 0

for _, member in ipairs(expired) do
    -- Parse "task_type|domain|task_id" — note task_id may itself
    -- contain '|' so we split on the FIRST two delimiters only.
    local first_sep = string.find(member, "|", 1, true)
    if first_sep then
        local task_type = string.sub(member, 1, first_sep - 1)
        local second_sep = string.find(member, "|", first_sep + 1, true)
        if second_sep then
            local domain = string.sub(member, first_sep + 1, second_sep - 1)
            local task_id = string.sub(member, second_sep + 1)

            -- Increment strike count atomically.
            local strikes = redis.call("HINCRBY", strikes_key, member, 1)

            if strikes >= max_strikes then
                -- Move to dead-letter: score = now, member encodes
                -- everything an operator needs to investigate.
                redis.call("ZADD", deadletter_key, now, member)
                redis.call("ZREM", inflight_key, member)
                redis.call("HDEL", strikes_key, member)
                dead_lettered = dead_lettered + 1
            else
                -- Verify the task's config hash still exists. If sync
                -- removed it (e.g. board pulled from CSV while the
                -- worker was crashed), re-enqueueing would just
                -- recreate a phantom task we'd never be able to
                -- claim — drop instead.
                local config_key
                if task_type == "monitor" then
                    config_key = "board:" .. task_id
                else
                    config_key = "scrape:" .. task_id
                end
                local config_exists = redis.call("EXISTS", config_key)

                if config_exists == 0 then
                    redis.call("ZREM", inflight_key, member)
                    redis.call("HDEL", strikes_key, member)
                    missing_config = missing_config + 1
                else
                    -- Re-enqueue to the per-domain ZSET with ZADD NX
                    -- (don't overwrite a fresher score from a parallel
                    -- enqueue). Use the recurring queue, not first-time
                    -- — first-time semantics are spent once claimed.
                    local queue_key
                    if task_type == "monitor" then
                        queue_key = "monitors_" .. wtype .. ":" .. domain
                    else
                        queue_key = "scrapes_" .. wtype .. ":" .. domain
                    end
                    redis.call("ZADD", queue_key, "NX", retry_score, task_id)

                    -- Re-park the domain in its ready tier so a worker
                    -- will see it. Pick the lowest-score tier across
                    -- ft / monitor / scrape buckets — same MIN-score
                    -- rule as the rest of the scheduler (#3016).
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
                    if redis.call("ZCARD", "monitors_" .. wtype .. ":" .. domain) > 0 then
                        local r3 = redis.call("ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
                        if #r3 >= 2 then mon_score = tonumber(r3[2]) end
                    end

                    local scr_score = nil
                    if redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain) > 0 then
                        local r4 = redis.call("ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
                        if #r4 >= 2 then scr_score = tonumber(r4[2]) end
                    end

                    local rl_val = redis.call("GET", "ratelimit:" .. domain)
                    local rl_at = 0
                    if rl_val then rl_at = tonumber(rl_val) end

                    local next_score = nil
                    local next_tier = nil
                    if ft_score ~= nil then
                        next_score = ft_score
                        next_tier = 0
                    end
                    if mon_score ~= nil and (next_score == nil or mon_score < next_score) then
                        next_score = mon_score
                        next_tier = 1
                    end
                    if scr_score ~= nil and (next_score == nil or scr_score < next_score) then
                        next_score = scr_score
                        next_tier = 2
                    end

                    if next_tier ~= nil then
                        -- Remove from other tiers, add to chosen one.
                        for t = 0, 2 do
                            if t ~= next_tier then
                                redis.call("ZREM", "ready:" .. wtype .. ":" .. t, domain)
                            end
                        end
                        redis.call("ZADD",
                                   "ready:" .. wtype .. ":" .. next_tier,
                                   math.max(rl_at, next_score),
                                   domain)
                    end

                    -- Remove the in-flight entry — the task is back
                    -- on the per-domain queue, available for any
                    -- worker to claim again.
                    redis.call("ZREM", inflight_key, member)
                    reenqueued = reenqueued + 1
                end
            end
        else
            -- Malformed member with only one separator — drop it
            -- defensively so a corrupt entry doesn't loop forever.
            redis.call("ZREM", inflight_key, member)
            redis.call("HDEL", strikes_key, member)
        end
    else
        redis.call("ZREM", inflight_key, member)
        redis.call("HDEL", strikes_key, member)
    end
end

return {reenqueued, dead_lettered, missing_config}
