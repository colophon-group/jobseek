-- Reschedule a task after processing. Adds it back to the recurring
-- per-domain queue and updates the domain's ready queue position.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = domain
-- ARGV[3] = task_id
-- ARGV[4] = task_type ("monitor" or "scrape")
-- ARGV[5] = next_due (float timestamp)
-- ARGV[6] = optional claim token returned by tokenized claim
-- ARGV[7] = optional failure-associated host from a native committed receipt
--           Published atomically after lease retirement; legacy calls omit it.
--
-- Returns: 1 if rescheduled, 0 if guarded or a stale tokenized attempt
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
local b0_guard_key = "lightpanda-b0:legacy-guard"
local scrape_rotation_key = "ready:rotation:" .. wtype
local monitor_repair_key = "monitor_repair_due:" .. wtype
local inflight_member = task_type .. "|" .. domain .. "|" .. task_id
local supplied_token = ARGV[6] or ""
local token_key = "inflight_tokens:" .. wtype
local token_type = redis.call("TYPE", token_key)["ok"]
if token_type ~= "none" and token_type ~= "hash" then
    return redis.error_reply("ordinary claim token index is corrupt")
end
if supplied_token ~= "" and (#supplied_token ~= 32 or
    string.find(supplied_token, "[^0-9a-f]") ~= nil) then
    return redis.error_reply("ordinary claim token is invalid")
end
local learned_host = ARGV[7] or ""
if learned_host ~= "" then
    if (wtype ~= "simple" and wtype ~= "browser") or task_type ~= "monitor" or supplied_token == "" or
        #learned_host > 253 or string.find(learned_host, "[%c|]") ~= nil then
        return redis.error_reply("ordinary receipt host is invalid")
    end
    -- Preflight every optional write before touching queue or inflight state.
    if redis.call("TYPE", "board:" .. task_id)["ok"] ~= "hash" then
        return redis.error_reply("ordinary receipt board is unavailable")
    end
end
local current_token = redis.call("HGET", token_key, inflight_member)
-- Legacy callers cannot mutate a tokenized lease; a tokenized caller cannot
-- mutate an absent/new generation. Expiry revokes authority before the reaper.
if current_token ~= false or supplied_token ~= "" then
    if current_token ~= supplied_token then return 0 end
    local deadline = redis.call("ZSCORE", "inflight:" .. wtype, inflight_member)
    local clock = redis.call("TIME")
    local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
    if deadline == false or tonumber(deadline) <= now then return 0 end
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
-- Redis scripts do not roll back earlier writes after a runtime type error.
-- Validate every queue/index read and lease-ending write before any mutation,
-- including the optional learned-host publication at the end of this script.
local function finite(value)
    return value ~= nil and value == value and value ~= math.huge and value ~= -math.huge
end
if not finite(next_due) then
    return redis.error_reply("ordinary next deadline is invalid")
end
for tier = 0, 2 do
    local state = redis.call("TYPE", "ready:" .. wtype .. ":" .. tier)["ok"]
    if state ~= "none" and state ~= "zset" then
        return redis.error_reply("ordinary ready queue is corrupt")
    end
end
for _, prefix in ipairs({"ft_monitors_", "ft_scrapes_", "monitors_", "scrapes_"}) do
    local key = prefix .. wtype .. ":" .. domain
    local state = redis.call("TYPE", key)["ok"]
    if state ~= "none" and state ~= "zset" then
        return redis.error_reply("ordinary recurring queue is corrupt")
    end
    local first = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
    if #first >= 2 and not finite(tonumber(first[2])) then
        return redis.error_reply("ordinary queue deadline is corrupt")
    end
end
for _, item in ipairs({{"inflight:" .. wtype, "zset"}, {"inflight_strikes:" .. wtype, "hash"}, {"ratelimit:" .. domain, "string"}}) do
    local state = redis.call("TYPE", item[1])["ok"]
    if state ~= "none" and state ~= item[2] then
        return redis.error_reply("ordinary settlement index is corrupt")
    end
end
local rate = redis.call("GET", "ratelimit:" .. domain)
local rotation = redis.call("ZSCORE", scrape_rotation_key, domain)
local repair = redis.call("HGET", monitor_repair_key, inflight_member)
if (rate ~= false and not finite(tonumber(rate))) or
   (rotation ~= false and not finite(tonumber(rotation))) or
   (repair ~= false and not finite(tonumber(repair))) then
    return redis.error_reply("ordinary settlement deadline is corrupt")
end

if task_type == "scrape" and redis.call("HEXISTS", b0_guard_key, task_id) == 1 then
    redis.call("ZREM", "inflight:" .. wtype, inflight_member)
    redis.call("HDEL", token_key, inflight_member)
    redis.call("HDEL", "inflight_strikes:" .. wtype, inflight_member)
    return 0
end

-- A deploy-time repair can race a monitor already running with the previous
-- config. Honor the earliest deferred repair deadline instead of letting the
-- stale run overwrite it with its normal cadence or failure backoff.
if task_type == "monitor" then
    local repair_due = redis.call("HGET", monitor_repair_key, inflight_member)
    if repair_due ~= false and tonumber(repair_due) < next_due then
        next_due = tonumber(repair_due)
    end
end

-- Add to recurring queue (not first-time)
local queue_key
if task_type == "monitor" then
    queue_key = "monitors_" .. wtype .. ":" .. domain
else
    queue_key = "scrapes_" .. wtype .. ":" .. domain
end
redis.call("ZADD", queue_key, next_due, task_id)

if task_type == "monitor" then
    redis.call("HDEL", monitor_repair_key, inflight_member)
end

-- Clear inflight lease entry — the task is back on the per-domain
-- queue, so the reaper must not double-enqueue it.
redis.call("ZREM", "inflight:" .. wtype, inflight_member)
redis.call("HDEL", token_key, inflight_member)
redis.call("HDEL", "inflight_strikes:" .. wtype, inflight_member)

-- claim_work records rotation separately from the ready marker, whose score
-- may be an authoritative future task deadline rather than a fairness floor.
local scrape_rotation_floor = tonumber(
    redis.call("ZSCORE", scrape_rotation_key, domain) or "0"
)

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

if learned_host ~= "" then
    redis.call("HSET", "board:" .. task_id, "egress_host", learned_host)
end
return 1
