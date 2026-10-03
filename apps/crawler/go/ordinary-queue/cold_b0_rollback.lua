-- Read-only, after the pinned actual B0 audit. Does not settle or restore work.
local audit = audited_b0()
if type(audit) ~= "table" or #audit ~= 12 or audit[1] ~= "accepted"
    or audit[2] ~= "audit_ok" or audit[12] ~= "0" then
    return redis.error_reply("cold B0 rollback observation rejected")
end
local owner = "lightpanda-b0:producer-owner"
local boards = tonumber(ARGV[22])
if not boards or boards < 1 or boards > 16
    or redis.call("HGET", owner, "cohort") ~= ARGV[21]
    or redis.call("HGET", owner, "board_count") ~= ARGV[22] then
    return redis.error_reply("cold B0 rollback observation rejected")
end
for index = 1, boards do
    if redis.call("HGET", owner, "board_slug:" .. ARGV[23 + index]) ~= "1" then
        return redis.error_reply("cold B0 rollback observation rejected")
    end
end
if #KEYS ~= 7 or #ARGV ~= 23 + boards then
    return redis.error_reply("cold B0 rollback observation rejected")
end
-- Audited keys and selectors must be permanent; expiry is lost authority.
for _, key in ipairs(KEYS) do
    if redis.call("EXISTS", key) == 1 and redis.call("PTTL", key) ~= -1 then
        return redis.error_reply("cold B0 rollback observation rejected")
    end
end
for _, key in ipairs({owner, "lightpanda-b0:legacy-guard"}) do
    if redis.call("EXISTS", key) == 1 and redis.call("PTTL", key) ~= -1 then
        return redis.error_reply("cold B0 rollback observation rejected")
    end
end
local records = redis.call("HGETALL", KEYS[2])
local bytes = 0
for _, value in ipairs(records) do
    bytes = bytes + #value
    if bytes > 32 * 1024 * 1024 then
        return redis.error_reply("cold B0 rollback observation rejected")
    end
end
local authority = {}
for _, key in ipairs({"inflight:simple", "inflight:browser", "deadletter:simple", "deadletter:browser"}) do
    local kind = redis.call("TYPE", key)["ok"]
    if kind ~= "none" and kind ~= "zset" then
        return redis.error_reply("cold B0 rollback observation rejected")
    end
    if kind == "zset" and redis.call("ZCARD", key) > 65536 then
        return redis.error_reply("cold B0 rollback observation rejected")
    end
    authority[#authority + 1] = redis.call("ZRANGE", key, 0, -1)
end
return {records, redis.call("HGETALL", "lightpanda-b0:legacy-guard"), authority}
