
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], tonumber(ARGV[2]))
end

local open_until = redis.call("GET", KEYS[2])
local opened_now = 0
local expired_open = open_until and tonumber(open_until) <= tonumber(ARGV[3])
if expired_open or (not open_until and count >= tonumber(ARGV[1])) then
    open_until = tonumber(ARGV[3]) + tonumber(ARGV[4])
    -- Retain the timestamp through two half-open probe leases. The value,
    -- rather than key expiry, defines when ordinary traffic may resume.
    redis.call(
        "SET",
        KEYS[2],
        tostring(open_until),
        "EX",
        tonumber(ARGV[4]) + (2 * tonumber(ARGV[5]))
    )
    redis.call("DEL", KEYS[3])
    opened_now = 1
end

-- RESP2 serializes Lua numbers as integers. Return the timestamp as text so
-- Python receives the same sub-second value stored in Redis.
return {count, tostring(open_until or 0), opened_now}
