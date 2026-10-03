-- Read-only restoration witness; missing evidence is never recreated.
local owner = "lightpanda-b0:producer-owner"
if redis.call("TYPE", owner)["ok"] ~= "hash" or redis.call("PTTL", owner) ~= -1 then
    return redis.error_reply("cold B0 restoration rejected")
end
local schema = redis.call("HGET", owner, "schema")
if schema == "jobseek.lightpanda.producer-owner/v1" then return "present" end
if schema ~= "jobseek.lightpanda.producer-rollback/v1" or redis.call("HLEN", owner) ~= 8 then
    return redis.error_reply("cold B0 restoration rejected")
end
local fields = {"namespace", "shard_id", "routing_epoch", "engine_owner", "cohort", "rollback_plan_digest", "source_receipt_sha256"}
if #ARGV ~= 7 or #KEYS ~= 7 then return redis.error_reply("cold B0 restoration rejected") end
for index, field in ipairs(fields) do
    if redis.call("HGET", owner, field) ~= ARGV[index] then
        return redis.error_reply("cold B0 restoration rejected")
    end
end
for _, key in ipairs(KEYS) do
    if redis.call("EXISTS", key) ~= 0 then return redis.error_reply("cold B0 restoration rejected") end
end
if redis.call("EXISTS", "lightpanda-b0:legacy-guard") ~= 0 then
    return redis.error_reply("cold B0 restoration rejected")
end
return "restored"
