-- Read-only source-pinned B0 audit, queue/legacy snapshot and prior publication
-- witness in one EVAL. No transfer, capacity allocation or lost-witness repair.
local base = observed_b0()
if type(base) ~= "table" or #base ~= 3 then
    return redis.error_reply("cold B0 forward observation rejected")
end
local offset = 23 + tonumber(ARGV[22])
local previous, previous_marker, count = ARGV[offset+1], ARGV[offset+2], tonumber(ARGV[offset+3])
if #KEYS ~= 9 or not count or count < 1 or count > 2048
    or #ARGV ~= offset + 3 + count*2 then
    return redis.error_reply("cold B0 forward observation rejected")
end
for index, expected in ipairs({previous, previous_marker}) do
    local key = KEYS[7+index]
    local kind = redis.call("TYPE", key)["ok"]
    if (expected == "" and kind ~= "none") or
        (expected ~= "" and (kind ~= "string" or redis.call("GET", key) ~= expected
            or redis.call("PTTL", key) ~= -1)) then
        return redis.error_reply("cold B0 forward observation rejected")
    end
end
local legacy = {}
local bytes = 0
for index=1,count do
    local id, domain = ARGV[offset+2+index*2], ARGV[offset+3+index*2]
    local key = "scrape:" .. id
    local kind = redis.call("TYPE", key)["ok"]
    if kind ~= "none" and (kind ~= "hash" or redis.call("HLEN", key) > 6
        or redis.call("PTTL", key) ~= -1) then
        return redis.error_reply("cold B0 forward observation rejected")
    end
    local item = {redis.call("HGETALL", key)}
    for _, value in ipairs(item[1]) do bytes = bytes + #value end
    for _, prefix in ipairs({"ft_scrapes_simple:", "scrapes_simple:", "ft_scrapes_browser:", "scrapes_browser:"}) do
        key = prefix .. domain
        kind = redis.call("TYPE", key)["ok"]
        if kind ~= "none" and (kind ~= "zset" or redis.call("PTTL", key) ~= -1) then
            return redis.error_reply("cold B0 forward observation rejected")
        end
        item[#item+1] = redis.call("ZSCORE", key, id) or false
    end
    if bytes > 16*1024*1024 then
        return redis.error_reply("cold B0 forward observation rejected")
    end
    legacy[#legacy+1] = item
end
return {base[1],base[2],base[3],legacy}
