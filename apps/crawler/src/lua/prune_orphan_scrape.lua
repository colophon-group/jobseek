-- Classify and optionally remove one unreachable scrape config hash.
--
-- Queue/config writers run in Lua, so this reachability check and UNLINK are
-- atomic with respect to enqueue, reschedule, completion, and reaping.
--
-- ARGV[1] = posting_id
-- ARGV[2] = apply ("1" deletes, any other value is dry-run)
--
-- Returns:
--   1  orphan (deleted when apply=1)
--   0  reachable from a queue, lease, or deadletter
--  -1  hash disappeared before classification
--  -2  hash has no domain and is left untouched for manual inspection

local task_id = ARGV[1]
local apply = ARGV[2] == "1"
local config_key = "scrape:" .. task_id

if redis.call("EXISTS", config_key) == 0 then
    return -1
end

local domain = redis.call("HGET", config_key, "domain")
if not domain or domain == "" then
    return -2
end

local member = "scrape|" .. domain .. "|" .. task_id
local reachable = (
    redis.call("ZSCORE", "ft_scrapes_simple:" .. domain, task_id) ~= false or
    redis.call("ZSCORE", "scrapes_simple:" .. domain, task_id) ~= false or
    redis.call("ZSCORE", "ft_scrapes_browser:" .. domain, task_id) ~= false or
    redis.call("ZSCORE", "scrapes_browser:" .. domain, task_id) ~= false or
    redis.call("ZSCORE", "inflight:simple", member) ~= false or
    redis.call("ZSCORE", "inflight:browser", member) ~= false or
    redis.call("ZSCORE", "deadletter:simple", member) ~= false or
    redis.call("ZSCORE", "deadletter:browser", member) ~= false
)

if reachable then
    return 0
end

if apply then
    redis.call("UNLINK", config_key)
end
return 1
