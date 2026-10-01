-- Runs after the source-pinned, unmodified B0 conservation audit in ONE EVAL.
-- KEYS 1..7 retain the B0 lifecycle ABI; 8 ordinary projection, 9 joint witness.
-- Extra arguments follow the exact producer board-slug list.
local audit = audited_b0()
if type(audit) ~= "table" or #audit ~= 12 or audit[1] ~= "accepted"
    or audit[2] ~= "audit_ok" or audit[12] ~= "0" then
    return redis.error_reply("joint ownership publication rejected")
end
local count = tonumber(audit[11])
local boards = tonumber(ARGV[22])
if not count or count < 1 or count > 1600 or not boards or boards < 1 or boards > 16
    or redis.call("SCARD", KEYS[5]) ~= 0 then
    return redis.error_reply("joint ownership publication rejected")
end
local owner = "lightpanda-b0:producer-owner"
if redis.call("HGET", owner, "cohort") ~= ARGV[21]
    or redis.call("HGET", owner, "board_count") ~= ARGV[22] then
    return redis.error_reply("joint ownership publication rejected")
end
for index = 1, boards do
    if redis.call("HGET", owner, "board_slug:" .. ARGV[23 + index]) ~= "1" then
        return redis.error_reply("joint ownership publication rejected")
    end
end
local offset = 23 + boards
local operation, previous, target, pending, published, previous_marker = unpack(ARGV, offset + 1, offset + 6)
if #KEYS ~= 9 or #ARGV ~= offset + 6 or not operation or not previous or not previous_marker
    or not target or #target < 1 or not pending or #pending < 1
    or not published or #published < 1 then
    return redis.error_reply("joint ownership publication rejected")
end
local projection_type = redis.call("TYPE", KEYS[8])["ok"]
local marker_type = redis.call("TYPE", KEYS[9])["ok"]
if (projection_type ~= "none" and projection_type ~= "string")
    or (marker_type ~= "none" and marker_type ~= "string") then
    return redis.error_reply("joint ownership publication rejected")
end
local projection = projection_type == "string" and redis.call("GET", KEYS[8]) or false
local marker = marker_type == "string" and redis.call("GET", KEYS[9]) or false
local previous_matches = (previous == "" and projection_type == "none")
    or (previous ~= "" and projection == previous and redis.call("PTTL", KEYS[8]) == -1)
local target_matches = projection == target and redis.call("PTTL", KEYS[8]) == -1
local pending_matches = marker == pending and redis.call("PTTL", KEYS[9]) == -1
local published_matches = marker == published and redis.call("PTTL", KEYS[9]) == -1
if operation == "prepare" then
    local prior_matches = (previous_marker == "" and marker_type == "none")
        or (previous_marker ~= "" and marker == previous_marker and redis.call("PTTL", KEYS[9]) == -1)
    if not previous_matches or (not prior_matches and not pending_matches) then
        return redis.error_reply("joint ownership publication rejected")
    end
    if not pending_matches then redis.call("SET", KEYS[9], pending) end
    return "pending"
end
if published_matches and target_matches then
    if operation == "publish" or operation == "inspect" or operation == "inspect-published" then
        return "published"
    end
end
if pending_matches and previous_matches then
    if operation == "inspect" then return "pending" end
    if operation == "publish" then
        -- Both routing bytes change in ONE command. No script rollback is assumed.
        redis.call("MSET", KEYS[8], target, KEYS[9], published)
        return "published"
    end
end
-- Missing/expired/partial witnesses are not automatically rebuilt after attempt.
return redis.error_reply("joint ownership publication rejected")
