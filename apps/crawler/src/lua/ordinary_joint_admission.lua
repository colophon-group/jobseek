-- Read-only runtime admission after the unmodified source-pinned B0 audit.
-- Live, conserved inflight/dead/terminal states are valid; this is NOT cutover.
local audit = audited_b0()
if type(audit) ~= "table" or #audit ~= 12 or audit[1] ~= "accepted"
    or audit[2] ~= "audit_ok" then
    return redis.error_reply("joint ownership admission rejected")
end
local boards = tonumber(ARGV[22])
if not boards or boards < 1 or boards > 16 or #KEYS ~= 9
    or #ARGV ~= 23 + boards + 2 then
    return redis.error_reply("joint ownership admission rejected")
end
local owner = "lightpanda-b0:producer-owner"
if redis.call("PTTL", KEYS[1]) ~= -1 or redis.call("PTTL", owner) ~= -1
    or redis.call("HGET", owner, "cohort") ~= ARGV[21]
    or redis.call("HGET", owner, "board_count") ~= ARGV[22] then
    return redis.error_reply("joint ownership admission rejected")
end
for index = 1, boards do
    if redis.call("HGET", owner, "board_slug:" .. ARGV[23 + index]) ~= "1" then
        return redis.error_reply("joint ownership admission rejected")
    end
end
for index = 8, 9 do
    if redis.call("TYPE", KEYS[index])["ok"] ~= "string"
        or redis.call("PTTL", KEYS[index]) ~= -1
        or redis.call("GET", KEYS[index]) ~= ARGV[23 + boards + index - 7] then
        return redis.error_reply("joint ownership admission rejected")
    end
end
return "accepted"
