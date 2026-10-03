-- Extend the lease on an in-flight task.
--
-- Workers call this periodically while processing long-running tasks
-- (large monitor cycles, slow scrapers) to push out the
-- ``leased_until`` timestamp and avoid being reaped mid-flight.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = task_type ("monitor" or "scrape")
-- ARGV[3] = domain
-- ARGV[4] = task_id
-- ARGV[5] = new_leased_until (float timestamp)
-- ARGV[6] = optional claim token returned by tokenized claim
--
-- Returns: 1 if extended (or unchanged matching token), 0 if the inflight
-- entry no longer exists or a supplied/current token is stale
-- (the reaper already moved on, or the task was completed in another
-- branch — either way the caller should stop heartbeating).
--
-- Uses ZADD ``XX`` so a stale heartbeat from a worker whose lease was
-- already reaped doesn't reinstate the entry — that would race against
-- the reaper's re-enqueue and could double-execute the task.

local wtype = ARGV[1]
local task_type = ARGV[2]
local domain = ARGV[3]
local task_id = ARGV[4]
local new_until = tonumber(ARGV[5])

local member = task_type .. "|" .. domain .. "|" .. task_id
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
local current_token = redis.call("HGET", token_key, member)
-- Legacy callers cannot mutate a tokenized lease; a tokenized caller cannot
-- mutate an absent/new generation. Expiry revokes authority before the reaper.
if current_token ~= false or supplied_token ~= "" then
    if current_token ~= supplied_token then return 0 end
    local deadline = redis.call("ZSCORE", "inflight:" .. wtype, member)
    local clock = redis.call("TIME")
    local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
    if deadline == false or tonumber(deadline) <= now then return 0 end
end
if supplied_token ~= "" then
    -- Matching, unexpired authority is accepted even for an unchanged deadline.
    local deadline = tonumber(redis.call("ZSCORE", "inflight:" .. wtype, member))
    redis.call("ZADD", "inflight:" .. wtype, "XX", math.max(deadline, new_until), member)
    return 1
end
local updated = redis.call("ZADD", "inflight:" .. wtype, "XX", "CH", new_until, member)
return updated
