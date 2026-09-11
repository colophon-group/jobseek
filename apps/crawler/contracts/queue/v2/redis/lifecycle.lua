-- Inactive queue-v2 Redis lifecycle candidate. Never imported by production.
--
-- Every key shares one Redis Cluster hash tag:
--   1 route hash       5 inflight zset
--   2 configs hash     6 dead-letter set
--   3 records hash     7 terminal set
--   4 ready zset
--
-- Route is an exact-shape hash: shard_id, routing_epoch, engine_owner, and
-- claim_sequence. claim_sequence is the bounded non-reuse high-water mark.
--
-- ARGV:
--   1 operation: initialize|register|claim|heartbeat|complete|reschedule|reap
--   2 task_id
--   3 shard_id
--   4 routing_epoch
--   5 engine_owner
--   6 config_revision
--   7 claim_token (empty for initialize/register/claim)
--   8 lease_ttl_ms (claim/heartbeat)
--   9 reschedule_delay_ms (reschedule)
--  10 max_failures (reap)
--
-- All numeric inputs, state, scores, Redis TIME values, and replies use the
-- same finite canonical decimal domain. No caller supplies wall time.

local MAX_INTEGER = 9999999999999
local operation = ARGV[1]
local task_id = ARGV[2]
local shard_id = ARGV[3]
local routing_epoch = ARGV[4]
local engine_owner = ARGV[5]
local config_revision = ARGV[6]
local claim_token = ARGV[7]

local function canonical_uint(text)
    if type(text) ~= "string" then return nil end
    if text ~= "0" and string.match(text, "^[1-9][0-9]*$") == nil then return nil end
    local value = tonumber(text)
    if not value or value < 0 or value > MAX_INTEGER or value ~= math.floor(value) then
        return nil
    end
    if tostring(value) ~= text then return nil end
    return value
end

local function canonical_positive(text)
    local value = canonical_uint(text)
    if not value or value < 1 then return nil end
    return value
end

local function canonical_number(value, minimum)
    if type(value) ~= "number" or value < minimum or value > MAX_INTEGER
        or value ~= math.floor(value) then
        return nil
    end
    local text = tostring(value)
    if canonical_uint(text) ~= value then return nil end
    return value
end

local function checked_add(left, right)
    if not left or not right or right > MAX_INTEGER - left then return nil end
    return left + right
end

local function redis_now_ms()
    local raw = redis.call("TIME")
    if type(raw) ~= "table" or #raw ~= 2 then return nil end
    local seconds = canonical_uint(raw[1])
    local microseconds = canonical_uint(raw[2])
    if not seconds or not microseconds or microseconds > 999999 then return nil end
    if seconds > math.floor(MAX_INTEGER / 1000) then return nil end
    return checked_add(seconds * 1000, math.floor(microseconds / 1000))
end

local now_ms = redis_now_ms()

local function result(decision, reason, token, value)
    return {
        decision,
        reason,
        now_ms and tostring(now_ms) or "0",
        token or "",
        value ~= nil and tostring(value) or "",
    }
end

if not now_ms then return result("not_current", "redis_time_invalid") end

local expected_types = {"hash", "hash", "hash", "zset", "zset", "set", "set"}

local function key_types_valid()
    for index, expected in ipairs(expected_types) do
        local actual = redis.call("TYPE", KEYS[index])["ok"]
        if actual ~= "none" and actual ~= expected then return false end
    end
    return true
end

local function load_route()
    if redis.call("TYPE", KEYS[1])["ok"] ~= "hash" then return nil end
    local fields = redis.call("HGETALL", KEYS[1])
    if #fields ~= 8 then return nil end
    local route = {}
    for index = 1, #fields, 2 do
        local field = fields[index]
        if field ~= "shard_id" and field ~= "routing_epoch"
            and field ~= "engine_owner" and field ~= "claim_sequence" then
            return nil
        end
        route[field] = fields[index + 1]
    end
    if not route.shard_id or route.shard_id == ""
        or not canonical_positive(route.routing_epoch)
        or (route.engine_owner ~= "python" and route.engine_owner ~= "go")
        or canonical_uint(route.claim_sequence) == nil then
        return nil
    end
    return route
end

local function namespace_valid()
    if not key_types_valid() then return nil end
    return load_route()
end

local function route_matches(route)
    if route.shard_id ~= shard_id then return false, "shard_id_mismatch" end
    if route.routing_epoch ~= routing_epoch then return false, "routing_epoch_mismatch" end
    if route.engine_owner ~= engine_owner then return false, "engine_owner_mismatch" end
    return true, nil
end

local function index_count(task)
    local count = 0
    if redis.call("ZSCORE", KEYS[4], task) then count = count + 1 end
    if redis.call("ZSCORE", KEYS[5], task) then count = count + 1 end
    count = count + redis.call("SISMEMBER", KEYS[6], task)
    count = count + redis.call("SISMEMBER", KEYS[7], task)
    return count
end

local function indexed_score(key, task)
    local text = redis.call("ZSCORE", key, task)
    if not text then return nil end
    return canonical_uint(text)
end

local record_fields = {
    task_id = true,
    state = true,
    shard_id = true,
    routing_epoch = true,
    engine_owner = true,
    config_revision = true,
    claim_token = true,
    claim_sequence = true,
    lease_until_ms = true,
    ready_at_ms = true,
    failures = true,
}

local function valid_record(record)
    local field_count = 0
    for key, _ in pairs(record) do
        if not record_fields[key] then return false end
        field_count = field_count + 1
    end
    if field_count ~= 11
        or type(record.task_id) ~= "string" or record.task_id == ""
        or type(record.shard_id) ~= "string" or record.shard_id == ""
        or not canonical_number(record.routing_epoch, 1)
        or (record.engine_owner ~= "python" and record.engine_owner ~= "go")
        or not canonical_number(record.config_revision, 1)
        or not canonical_number(record.failures, 0) then
        return false
    end
    if record.state == "ready" then
        return record.claim_token == cjson.null
            and record.claim_sequence == cjson.null
            and record.lease_until_ms == cjson.null
            and canonical_number(record.ready_at_ms, 0) ~= nil
    end
    if record.state == "inflight" then
        if type(record.claim_token) ~= "string"
            or not canonical_number(record.claim_sequence, 1)
            or not canonical_number(record.lease_until_ms, 0)
            or record.ready_at_ms ~= cjson.null then
            return false
        end
        return record.claim_token
            == tostring(record.routing_epoch) .. ":" .. tostring(record.claim_sequence)
    end
    if record.state == "dead_letter" or record.state == "terminal" then
        return record.claim_token == cjson.null
            and record.claim_sequence == cjson.null
            and record.lease_until_ms == cjson.null
            and record.ready_at_ms == cjson.null
    end
    return false
end

local function load_current(route, expected_state, token_required)
    local route_ok, route_reason = route_matches(route)
    if not route_ok then return nil, "fenced", route_reason end

    local configured = redis.call("HGET", KEYS[2], task_id)
    if not configured then return nil, "not_current", "config_missing" end
    if canonical_positive(configured) == nil then
        return nil, "not_current", "namespace_corrupt"
    end
    if configured ~= config_revision then
        return nil, "fenced", "config_revision_mismatch"
    end

    local encoded = redis.call("HGET", KEYS[3], task_id)
    if not encoded then return nil, "not_current", "record_missing" end
    local decode_ok, record = pcall(cjson.decode, encoded)
    if not decode_ok or type(record) ~= "table" or not valid_record(record)
        or record.task_id ~= task_id then
        return nil, "not_current", "record_corrupt"
    end
    if index_count(task_id) ~= 1 then
        return nil, "not_current", "conservation_violation"
    end
    if record.state == "ready" then
        if indexed_score(KEYS[4], task_id) ~= record.ready_at_ms then
            return nil, "not_current", "conservation_violation"
        end
    elseif record.state == "inflight" then
        if indexed_score(KEYS[5], task_id) ~= record.lease_until_ms then
            return nil, "not_current", "conservation_violation"
        end
    elseif record.state == "dead_letter" then
        if redis.call("SISMEMBER", KEYS[6], task_id) ~= 1 then
            return nil, "not_current", "conservation_violation"
        end
    elseif record.state == "terminal" then
        if redis.call("SISMEMBER", KEYS[7], task_id) ~= 1 then
            return nil, "not_current", "conservation_violation"
        end
    end
    if record.state ~= expected_state then return nil, "not_current", "state_mismatch" end
    if tostring(record.shard_id) ~= shard_id
        or tostring(record.routing_epoch) ~= routing_epoch
        or tostring(record.engine_owner) ~= engine_owner
        or tostring(record.config_revision) ~= config_revision then
        return nil, "fenced", "record_fence_mismatch"
    end
    if token_required and record.claim_token ~= claim_token then
        return nil, "fenced", "claim_token_mismatch"
    end
    if record.state == "inflight"
        and record.claim_sequence > canonical_uint(route.claim_sequence) then
        return nil, "not_current", "record_corrupt"
    end
    return record, nil, nil
end

local function store(record)
    redis.call("HSET", KEYS[3], task_id, cjson.encode(record))
end

local function leave_inflight(record, next_state, score)
    redis.call("ZREM", KEYS[5], task_id)
    record.state = next_state
    record.claim_token = cjson.null
    record.claim_sequence = cjson.null
    record.lease_until_ms = cjson.null
    if next_state == "ready" then
        record.ready_at_ms = score
        redis.call("ZADD", KEYS[4], score, task_id)
    elseif next_state == "dead_letter" then
        record.ready_at_ms = cjson.null
        redis.call("SADD", KEYS[6], task_id)
    elseif next_state == "terminal" then
        record.ready_at_ms = cjson.null
        redis.call("SADD", KEYS[7], task_id)
    end
    store(record)
end

if shard_id == "" or canonical_positive(routing_epoch) == nil
    or (engine_owner ~= "python" and engine_owner ~= "go") then
    return result("not_current", "invalid_route")
end

if operation == "initialize" then
    local existing_count = redis.call("EXISTS", unpack(KEYS))
    if existing_count == 0 then
        redis.call(
            "HSET", KEYS[1],
            "shard_id", shard_id,
            "routing_epoch", routing_epoch,
            "engine_owner", engine_owner,
            "claim_sequence", "0"
        )
        return result("accepted", "initialized")
    end
    local existing_route = namespace_valid()
    if not existing_route then return result("not_current", "namespace_corrupt") end
    local matches, reason = route_matches(existing_route)
    if not matches then return result("fenced", reason) end
    return result("accepted", "already_initialized")
end

local route = namespace_valid()
if not route then return result("not_current", "namespace_corrupt") end
local matches, route_reason = route_matches(route)
if not matches then return result("fenced", route_reason) end
if task_id == "" or canonical_positive(config_revision) == nil then
    return result("not_current", "invalid_task_identity")
end

if operation == "register" then
    if redis.call("HEXISTS", KEYS[2], task_id) == 1
        or redis.call("HEXISTS", KEYS[3], task_id) == 1
        or index_count(task_id) ~= 0 then
        return result("not_current", "task_already_exists")
    end
    local record = {
        task_id = task_id,
        state = "ready",
        shard_id = shard_id,
        routing_epoch = tonumber(routing_epoch),
        engine_owner = engine_owner,
        config_revision = tonumber(config_revision),
        claim_token = cjson.null,
        claim_sequence = cjson.null,
        lease_until_ms = cjson.null,
        ready_at_ms = now_ms,
        failures = 0,
    }
    redis.call("HSET", KEYS[2], task_id, config_revision)
    store(record)
    redis.call("ZADD", KEYS[4], tostring(now_ms), task_id)
    return result("accepted", "registered", nil, now_ms)
end

if operation == "claim" then
    local record, decision, reason = load_current(route, "ready", false)
    if not record then return result(decision, reason) end
    if record.ready_at_ms > now_ms then
        return result("not_current", "not_due", nil, record.ready_at_ms)
    end
    local lease_ttl = canonical_positive(ARGV[8])
    if not lease_ttl then return result("not_current", "invalid_lease_ttl") end
    local lease_until = checked_add(now_ms, lease_ttl)
    if not lease_until then return result("not_current", "numeric_overflow") end
    local sequence = canonical_uint(route.claim_sequence)
    if not sequence or sequence >= MAX_INTEGER then
        return result("not_current", "claim_sequence_exhausted")
    end
    local expected_sequence = sequence + 1
    local incremented = redis.call("HINCRBY", KEYS[1], "claim_sequence", 1)
    if incremented ~= expected_sequence or not canonical_number(incremented, 1) then
        redis.call("HSET", KEYS[1], "claim_sequence", route.claim_sequence)
        return result("not_current", "claim_sequence_corrupt")
    end
    local issued_token = routing_epoch .. ":" .. tostring(incremented)
    redis.call("ZREM", KEYS[4], task_id)
    redis.call("ZADD", KEYS[5], tostring(lease_until), task_id)
    record.state = "inflight"
    record.claim_token = issued_token
    record.claim_sequence = incremented
    record.lease_until_ms = lease_until
    record.ready_at_ms = cjson.null
    store(record)
    return result("accepted", "claimed", issued_token, lease_until)
end

if operation == "heartbeat" then
    local record, decision, reason = load_current(route, "inflight", true)
    if not record then return result(decision, reason) end
    if now_ms >= record.lease_until_ms then
        return result("not_current", "lease_expired")
    end
    local lease_ttl = canonical_positive(ARGV[8])
    if not lease_ttl then return result("not_current", "invalid_lease_ttl") end
    local lease_until = checked_add(now_ms, lease_ttl)
    if not lease_until then return result("not_current", "numeric_overflow") end
    if lease_until <= record.lease_until_ms then
        return result("not_current", "lease_not_extended")
    end
    record.lease_until_ms = lease_until
    redis.call("ZADD", KEYS[5], tostring(lease_until), task_id)
    store(record)
    return result("accepted", "lease_extended", claim_token, lease_until)
end

if operation == "complete" then
    local record, decision, reason = load_current(route, "inflight", true)
    if not record then return result(decision, reason) end
    if now_ms >= record.lease_until_ms then
        return result("not_current", "lease_expired")
    end
    leave_inflight(record, "terminal", nil)
    return result("accepted", "completed", claim_token)
end

if operation == "reschedule" then
    local record, decision, reason = load_current(route, "inflight", true)
    if not record then return result(decision, reason) end
    if now_ms >= record.lease_until_ms then
        return result("not_current", "lease_expired")
    end
    local delay = canonical_uint(ARGV[9])
    if delay == nil then return result("not_current", "invalid_reschedule_delay") end
    local ready_at = checked_add(now_ms, delay)
    if not ready_at then return result("not_current", "numeric_overflow") end
    leave_inflight(record, "ready", ready_at)
    return result("accepted", "rescheduled", claim_token, ready_at)
end

if operation == "reap" then
    local record, decision, reason = load_current(route, "inflight", true)
    if not record then return result(decision, reason) end
    local maximum_failures = canonical_positive(ARGV[10])
    if not maximum_failures then return result("not_current", "invalid_max_failures") end
    if now_ms < record.lease_until_ms then
        return result("not_current", "lease_active", claim_token, record.lease_until_ms)
    end
    local failures = checked_add(record.failures, 1)
    if not failures then return result("not_current", "numeric_overflow") end
    record.failures = failures
    if failures >= maximum_failures then
        leave_inflight(record, "dead_letter", nil)
        return result("accepted", "dead_lettered", claim_token, failures)
    end
    leave_inflight(record, "ready", now_ms)
    return result("accepted", "requeued", claim_token, failures)
end

return result("not_current", "unsupported_operation")
