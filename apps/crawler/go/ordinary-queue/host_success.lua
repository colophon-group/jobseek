
local open_until = redis.call("GET", KEYS[2])
redis.call("DEL", KEYS[1])

if open_until and tonumber(open_until) <= tonumber(ARGV[1]) then
    redis.call("DEL", KEYS[2], KEYS[3])
    return "0"
end

if open_until then
    return tostring(open_until)
end

redis.call("DEL", KEYS[3])
return "0"
