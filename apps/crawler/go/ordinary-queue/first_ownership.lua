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
local operation, body, retirement, projection = ARGV[offset + 1], ARGV[offset + 2], ARGV[offset + 3], ARGV[offset + 4]
if #ARGV ~= offset + 4 or not body or #body < 1 or not retirement or not projection or #projection < 1
    or (operation ~= "publish" and operation ~= "retire"
        and operation ~= "inspect-active" and operation ~= "inspect-retired"
        and operation ~= "cancel-staged" and operation ~= "inspect-staged-cancelled") then
    return redis.error_reply("first ordinary ownership rejected")
end
-- Only the exclusive SQL caller selects these operations for a never-active
-- staged plan with no current attempts. Preserve legacy Redis inflight entries
-- for the restored workers; cancellation grants no queue or SQL write owner.
local cancel_staged = operation == "cancel-staged" or operation == "inspect-staged-cancelled"
if cancel_staged and retirement ~= "[]" then
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
if exists and (redis.call("GET", KEYS[8]) ~= projection or redis.call("PTTL", KEYS[8]) ~= -1) then
    return redis.error_reply("first ordinary ownership rejected")
end
local ok, plan = pcall(cjson.decode, body)
if not ok or type(plan) ~= "table" or type(plan.members) ~= "table" then
    return redis.error_reply("first ordinary ownership rejected")
end
if plan.routing_projection == nil then
    if projection ~= body then return redis.error_reply("first ordinary ownership rejected") end
else
    local projected, routing = pcall(cjson.decode, projection)
    if not projected or type(routing) ~= "table" or
        routing.version ~= "jobseek.ordinary.ownership-projection/v1" or
        routing.routing_epoch ~= plan.routing_epoch or routing.source_revision ~= plan.source_revision or
        type(routing.plan_sha256) ~= "string" or #routing.plan_sha256 ~= 64 or
        string.find(routing.plan_sha256, "[^0-9a-f]") or type(routing.members) ~= "table" or
        #plan.members == 0 then
        return redis.error_reply("first ordinary ownership rejected")
    end
    local count = 0
    for _ in pairs(routing.members) do count = count + 1 end
    if count ~= #plan.members then return redis.error_reply("first ordinary ownership rejected") end
    for _, member in ipairs(plan.members) do
        if routing.members[member.board_id] ~= member.domain then
            return redis.error_reply("first ordinary ownership rejected")
        end
    end
    if (plan.details == nil) ~= (routing.details == nil) then return redis.error_reply("first ordinary ownership rejected") end
    if plan.details ~= nil then
        if type(routing.details) ~= "table" then return redis.error_reply("first ordinary ownership rejected") end
        local count = 0
        for _ in pairs(routing.details) do count = count + 1 end
        if count ~= #plan.details then return redis.error_reply("first ordinary ownership rejected") end
        for _, member in ipairs(plan.details) do
            if routing.details[member.board_id] ~= member.domain then return redis.error_reply("first ordinary ownership rejected") end
        end
    end
end
local changes, domains
if operation == "retire" and retirement ~= "[]" then
    changes, domains = prepare_first_retirement(plan, retirement, exists)
    if changes == nil then return redis.error_reply("first ordinary ownership rejected") end
elseif retirement ~= "[]" then
    return redis.error_reply("first ordinary ownership rejected")
end
for _, worker in ipairs({"simple", "browser"}) do
    if not retirement_type("inflight:" .. worker, "zset") or not retirement_type("inflight_tokens:" .. worker, "hash") then
        return redis.error_reply("first ordinary ownership rejected")
    end
    for _, member in ipairs(plan.members) do
        local task = "monitor|" .. member.domain .. "|" .. member.board_id
        if not cancel_staged and operation ~= "inspect-active" and changes == nil
            and (redis.call("ZSCORE", "inflight:" .. worker, task)
              or redis.call("HEXISTS", "inflight_tokens:" .. worker, task) == 1) then
            return redis.error_reply("first ordinary ownership rejected")
        end
    end
end
if not cancel_staged and operation ~= "inspect-active" and changes == nil and plan.details ~= nil then
    local owned = {}
    for _, member in ipairs(plan.details) do owned[member.board_id] = member.domain end
    for _, worker in ipairs({"simple", "browser"}) do
        if not retirement_type("inflight:" .. worker, "zset") or not retirement_type("inflight_tokens:" .. worker, "hash") then
            return redis.error_reply("first ordinary ownership rejected")
        end
        for _, task in ipairs(redis.call("ZRANGE", "inflight:" .. worker,0,-1)) do
            local kind, domain, id = string.match(task,"^([^|]+)|([^|]+)|([^|]+)$")
            if kind == "scrape" and owned[redis.call("HGET","scrape:"..id,"board_id") or ""] then
                return redis.error_reply("first ordinary ownership rejected")
            end
        end
    end
end
if operation == "inspect-active" and not exists then
    return redis.error_reply("first ordinary ownership rejected")
end
if (operation == "inspect-retired" or operation == "inspect-staged-cancelled") and exists then
    return redis.error_reply("first ordinary ownership rejected")
end
-- All type/value/lease checks precede owned restoration and projection effects.
if changes ~= nil then apply_first_retirement(changes, domains) end
if operation == "publish" and not exists then redis.call("SET", KEYS[8], projection) end
if (operation == "retire" or operation == "cancel-staged") and exists then redis.call("DEL", KEYS[8]) end
return "accepted"
