-- First ordinary adoption/retirement keeps the existing B0 incarnation intact.
-- The original host flock and live exclusive SQL scope belong to the caller.
local audit = audited_b0()
if type(audit) ~= "table" or #audit ~= 12 or audit[1] ~= "accepted"
    or audit[2] ~= "audit_ok" or audit[12] ~= "0" then
    return redis.error_reply("first ordinary ownership rejected")
end
local boards = tonumber(ARGV[22])
if not boards or boards < 1 or boards > 16 or #KEYS ~= 10
    or redis.call("ZCARD", KEYS[4]) ~= 0
    or redis.call("HLEN", KEYS[7]) ~= 0 then
    return redis.error_reply("first ordinary ownership rejected")
end
local owner = "lightpanda-b0:producer-owner"
if redis.call("HGET", owner, "cohort") ~= ARGV[21]
    or redis.call("HGET", owner, "board_count") ~= ARGV[22] then
    return redis.error_reply("first ordinary ownership rejected")
end
for index = 1, boards do
    if redis.call("HGET", owner, "board_slug:" .. ARGV[23 + index]) ~= "1" then
        return redis.error_reply("first ordinary ownership rejected")
    end
end
local offset = 23 + boards
local operation, body, retirement = ARGV[offset + 1], ARGV[offset + 2], ARGV[offset + 3]
if #ARGV ~= offset + 3 or not body or #body < 1 or not retirement
    or (operation ~= "publish" and operation ~= "retire"
        and operation ~= "inspect-active" and operation ~= "inspect-retired") then
    return redis.error_reply("first ordinary ownership rejected")
end
local projection_type = redis.call("TYPE", KEYS[8])["ok"]
local inflight_type = redis.call("TYPE", KEYS[10])["ok"]
if (projection_type ~= "none" and projection_type ~= "string")
    or (inflight_type ~= "none" and inflight_type ~= "zset")
    or redis.call("TYPE", KEYS[9])["ok"] ~= "none" then
    return redis.error_reply("first ordinary ownership rejected")
end
local exists = projection_type == "string"
if exists and (redis.call("GET", KEYS[8]) ~= body or redis.call("PTTL", KEYS[8]) ~= -1) then
    return redis.error_reply("first ordinary ownership rejected")
end
local ok, plan = pcall(cjson.decode, body)
if not ok or type(plan) ~= "table" or type(plan.members) ~= "table" then
    return redis.error_reply("first ordinary ownership rejected")
end
local changes, domains
if operation == "retire" and retirement ~= "[]" then
    changes, domains = prepare_first_retirement(plan, retirement, exists)
    if changes == nil then return redis.error_reply("first ordinary ownership rejected") end
elseif retirement ~= "[]" then
    return redis.error_reply("first ordinary ownership rejected")
end
if not retirement_type("inflight_tokens:simple", "hash") then
    return redis.error_reply("first ordinary ownership rejected")
end
for _, member in ipairs(plan.members) do
    local task = "monitor|" .. member.domain .. "|" .. member.board_id
    if operation ~= "inspect-active" and changes == nil
        and redis.call("ZSCORE", KEYS[10], "monitor|" .. member.domain .. "|" .. member.board_id) then
        return redis.error_reply("first ordinary ownership rejected")
    end
    if operation ~= "inspect-active" and changes == nil
        and redis.call("HEXISTS", "inflight_tokens:simple", task) == 1 then
        return redis.error_reply("first ordinary ownership rejected")
    end
end
if operation == "inspect-active" and not exists then
    return redis.error_reply("first ordinary ownership rejected")
end
if operation == "inspect-retired" and exists then
    return redis.error_reply("first ordinary ownership rejected")
end
-- All type/value/lease checks precede owned restoration and projection effects.
if changes ~= nil then apply_first_retirement(changes, domains) end
if operation == "publish" and not exists then redis.call("SET", KEYS[8], body) end
if operation == "retire" and exists then redis.call("DEL", KEYS[8]) end
return "accepted"
