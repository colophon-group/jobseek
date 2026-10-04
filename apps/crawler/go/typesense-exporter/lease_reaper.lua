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
-- ARGV[6] = "guarded" only while holding the PG ordinary lease barrier
-- ARGV[7:8] = optional bounded SQL receipt observation and ownership SHA1,
--             supplied by the Go reaper under that same exclusive barrier
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
local b0_guard_key = "lightpanda-b0:legacy-guard"
local scrape_rotation_key = "ready:rotation:" .. wtype
local monitor_repair_key = "monitor_repair_due:" .. wtype
local token_key = "inflight_tokens:" .. wtype
local token_type = redis.call("TYPE", token_key)["ok"]
if token_type ~= "none" and token_type ~= "hash" then
    return redis.error_reply("ordinary claim token index is corrupt")
end

-- Fail before touching any expired member. Redis does not roll back writes
-- made earlier in a script when a later command raises WRONGTYPE.
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

-- A committed native run can lose its settlement acknowledgement. Expiry
-- must recover that success's canonical deadline, not retry the fetch at now.
-- Attest every receipt and affected index before revoking any batch token.
local receipts = {}
if ARGV[7] ~= nil then
    local function finite(value)
        return value ~= nil and value == value and value ~= math.huge and value ~= -math.huge
    end
    local function compatible(key, expected)
        local kind = redis.call("TYPE", key)["ok"]
        return kind == "none" or kind == expected
    end
    local ok, observation = pcall(cjson.decode, ARGV[7])
    if ARGV[6] ~= "guarded" or not ok or type(observation) ~= "table"
        or type(observation.expired) ~= "table" or type(observation.receipts) ~= "table"
        or #observation.expired ~= #expired then
        return redis.error_reply("ordinary expired batch changed")
    end
    local observed = {}
    for index, member in ipairs(expired) do
        if observation.expired[index] ~= member then return redis.error_reply("ordinary expired batch changed") end
        observed[member] = true
    end
    local count = 0
    for member, receipt in pairs(observation.receipts) do
        local domain, task_id = string.match(member, "^monitor|([^|]+)|([^|]+)$")
        if wtype ~= "simple" or not observed[member] or domain == nil or type(receipt) ~= "table"
            or type(receipt.token) ~= "string" or #receipt.token ~= 32
            or string.find(receipt.token, "[^0-9a-f]") ~= nil
            or redis.call("HGET", token_key, member) ~= receipt.token
            or type(receipt.due) ~= "string" or not finite(tonumber(receipt.due)) or tonumber(receipt.due) < 0
            or type(receipt.config) ~= "table" or type(receipt.learned_host) ~= "string"
            or #receipt.learned_host > 253 or string.find(receipt.learned_host, "[%c|]") ~= nil then
            return redis.error_reply("ordinary committed receipt changed")
        end
        local board = "board:" .. task_id
        if redis.call("TYPE", board)["ok"] ~= "hash" then return redis.error_reply("ordinary committed config changed") end
        local fields = 0
        for field, value in pairs(receipt.config) do
            if type(field) ~= "string" or type(value) ~= "string" or redis.call("HGET", board, field) ~= value then
                return redis.error_reply("ordinary committed config changed")
            end
            fields = fields + 1
        end
        if fields == 0 or fields ~= redis.call("HLEN", board) then return redis.error_reply("ordinary committed config changed") end
        for _, prefix in ipairs({"ft_monitors_", "ft_scrapes_", "monitors_", "scrapes_"}) do
            local key = prefix .. wtype .. ":" .. domain
            if not compatible(key, "zset") then return redis.error_reply("ordinary committed queue is corrupt") end
            for _, position in ipairs({0, -1}) do
                local edge = redis.call("ZRANGE", key, position, position, "WITHSCORES")
                if #edge >= 2 and not finite(tonumber(edge[2])) then return redis.error_reply("ordinary committed queue is corrupt") end
            end
        end
        for tier = 0, 2 do
            if not compatible("ready:" .. wtype .. ":" .. tier, "zset") then return redis.error_reply("ordinary committed ready index is corrupt") end
        end
        if not compatible(strikes_key, "hash") or not compatible("ratelimit:" .. domain, "string") then
            return redis.error_reply("ordinary committed recovery index is corrupt")
        end
        local rate = redis.call("GET", "ratelimit:" .. domain)
        local rotation = redis.call("ZSCORE", scrape_rotation_key, domain)
        local repair = redis.call("HGET", monitor_repair_key, member)
        if (rate ~= false and not finite(tonumber(rate))) or (rotation ~= false and not finite(tonumber(rotation)))
            or (repair ~= false and not finite(tonumber(repair))) then
            return redis.error_reply("ordinary committed recovery deadline is corrupt")
        end
        count = count + 1
    end
    if count > 0 then
        if not compatible("ordinary:ownership:active", "string") then return redis.error_reply("ordinary ownership projection changed") end
        local projection = redis.call("GET", "ordinary:ownership:active")
        if projection == false or redis.sha1hex(projection) ~= ARGV[8]
            or redis.call("PTTL", "ordinary:ownership:active") ~= -1 then
            return redis.error_reply("ordinary ownership projection changed")
        end
    end
    receipts = observation.receipts
end

local reenqueued = 0
local dead_lettered = 0
local missing_config = 0

for _, member in ipairs(expired) do
    local receipt = receipts[member]
    -- Legacy direct reapers leave tokenized attempts to the guarded Go reaper.
    -- They may still recover ordinary tokenless work in the same batch.
    if ARGV[6] == "guarded" or redis.call("HEXISTS", token_key, member) == 0 then
    -- Revoke this expired generation on every path (retry/deadletter/orphan/
    -- guard/malformed). New claims create a distinct token in the same queues.
    redis.call("HDEL", token_key, member)
    -- Parse "task_type|domain|task_id" — note task_id may itself
    -- contain '|' so we split on the FIRST two delimiters only.
    local first_sep = string.find(member, "|", 1, true)
    if first_sep then
        local task_type = string.sub(member, 1, first_sep - 1)
        local second_sep = string.find(member, "|", first_sep + 1, true)
        if second_sep then
            local domain = string.sub(member, first_sep + 1, second_sep - 1)
            local task_id = string.sub(member, second_sep + 1)

            -- A Go B0 ownership guard is authoritative by posting ID, even
            -- when a crashed legacy claimant used a stale domain. Quarantine
            -- that residual lease instead of reviving or dead-lettering it.
            local guarded = task_type == "scrape"
                and redis.call("HEXISTS", b0_guard_key, task_id) == 1
            if guarded then
                redis.call("ZREM", inflight_key, member)
                redis.call("HDEL", strikes_key, member)
            else
                -- A config-repair sync may have arrived while the old run was
                -- leased. That repair is a fresh attempt, so it supersedes
                -- stale strikes/dead-lettering and carries its earlier due
                -- time into the recurring queue.
                local repair_due = false
                if task_type == "monitor" then
                    repair_due = redis.call("HGET", monitor_repair_key, member)
                end

                local strikes = 0
                if receipt ~= nil or repair_due ~= false then
                    redis.call("HDEL", strikes_key, member)
                else
                    -- Increment strike count atomically.
                    strikes = redis.call("HINCRBY", strikes_key, member, 1)
                end

                if repair_due == false and strikes >= max_strikes then
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
                        redis.call("HDEL", monitor_repair_key, member)
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
                    if receipt ~= nil then
                        local due = tonumber(receipt.due)
                        if repair_due ~= false then due = math.min(due, tonumber(repair_due)) end
                        redis.call("ZREM", "ft_monitors_" .. wtype .. ":" .. domain, task_id)
                        redis.call("ZADD", queue_key, due, task_id)
                        redis.call("HDEL", monitor_repair_key, member)
                        if receipt.learned_host ~= "" then redis.call("HSET", config_key, "egress_host", receipt.learned_host) end
                    elseif repair_due ~= false then
                        local due = math.min(retry_score, tonumber(repair_due))
                        local queued_due = redis.call("ZSCORE", queue_key, task_id)
                        if queued_due == false or due < tonumber(queued_due) then
                            redis.call("ZADD", queue_key, due, task_id)
                        end
                        redis.call("HDEL", monitor_repair_key, member)
                    else
                        redis.call("ZADD", queue_key, "NX", retry_score, task_id)
                    end

                    -- Re-park every ready representation for the domain. A
                    -- monitor and scrape deadline must coexist: otherwise an
                    -- older scrape backlog can leave a later due monitor
                    -- advertised only in globally lower-priority tier 2.
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
                    local scrape_rotation_floor = tonumber(
                        redis.call("ZSCORE", scrape_rotation_key, domain) or "0"
                    )

                    for tier = 0, 2 do
                        redis.call("ZREM", "ready:" .. wtype .. ":" .. tier, domain)
                    end
                    if ft_score ~= nil then
                        if scr_score == nil then
                            redis.call("ZREM", scrape_rotation_key, domain)
                        end
                        redis.call("ZADD", "ready:" .. wtype .. ":0", math.max(rl_at, ft_score), domain)
                    else
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

                    -- Remove the in-flight entry — the task is back
                    -- on the per-domain queue, available for any
                    -- worker to claim again.
                        redis.call("ZREM", inflight_key, member)
                        reenqueued = reenqueued + 1
                    end
                end
            end
        else
            -- Malformed member with only one separator — drop it
            -- defensively so a corrupt entry doesn't loop forever.
            redis.call("ZREM", inflight_key, member)
            redis.call("HDEL", strikes_key, member)
            redis.call("HDEL", monitor_repair_key, member)
        end
    else
        redis.call("ZREM", inflight_key, member)
        redis.call("HDEL", strikes_key, member)
        redis.call("HDEL", monitor_repair_key, member)
    end
    end
end

return {reenqueued, dead_lettered, missing_config}
