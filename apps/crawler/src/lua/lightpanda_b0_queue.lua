-- Inactive Lightpanda B0 queue lifecycle. Python is the sole queue owner.
--
-- KEYS (all seven share one hash tag):
--   1 route hash        4 inflight zset
--   2 records hash      5 dead set
--   3 ready zset        6 terminal set
--                       7 origin-holder hash
--
-- This script also reads/writes the existing ratelimit:<domain> strings and
-- reads delay:<domain>. Consequently it is intentionally limited to the
-- crawler's standalone Redis deployment, not Redis Cluster.
--
-- ARGV:
--   1 operation
--   2 shard_id
--   3 routing_epoch
--   4 engine_owner
--   5 task_id
--   6 config_revision
--   7 claim_token
--   8 lease_ttl_ms
--   9 absolute_ready_at_ms
--  10 max_failures
--  11 canonical_payload
--  12 payload_sha256
--  13 payload_sha1
--  14 scan_limit
--  15 default_delay_seconds
--  16 previous_payload_sha256 (reactivate only)
--  17 expected_lease_until_ms (leased-task mutations only)

local MAX_INTEGER = 9999999999999
local MAX_PAYLOAD_BYTES = 131072
local MAX_RECORDS = 512
local MAX_SCAN = 64
local MAX_LEASE_TTL_MS = 3600000
local MAX_FAILURES = 100
local ORIGIN_RECHECK_MS = 1000

local operation = ARGV[1]
local shard_id = ARGV[2]
local routing_epoch = ARGV[3]
local engine_owner = ARGV[4]
local task_id = ARGV[5]
local config_revision = ARGV[6]
local claim_token = ARGV[7]
local payload = ARGV[11]
local payload_sha256 = ARGV[12]
local payload_sha1 = ARGV[13]

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

local function bounded_number(value, minimum, maximum)
    if type(value) ~= "number" or value ~= value or value < minimum or value > maximum
        or value ~= math.floor(value) then
        return nil
    end
    return value
end

local function checked_add(left, right)
    if not left or not right or right > MAX_INTEGER - left then return nil end
    return left + right
end

local function redis_now()
    local raw = redis.call("TIME")
    if type(raw) ~= "table" or #raw ~= 2 then return nil, nil end
    local seconds = canonical_uint(raw[1])
    local microseconds = canonical_uint(raw[2])
    if not seconds or not microseconds or microseconds > 999999 then return nil, nil end
    if seconds > math.floor(MAX_INTEGER / 1000) then return nil, nil end
    local milliseconds = checked_add(seconds * 1000, math.floor(microseconds / 1000))
    if not milliseconds then return nil, nil end
    return milliseconds, seconds + (microseconds / 1000000)
end

local now_ms, now_seconds = redis_now()

local function result(decision, reason, record, value, secondary)
    return {
        decision,
        reason,
        now_ms and tostring(now_ms) or "0",
        record and record.task_id or "",
        record and record.claim_token or "",
        record and tostring(record.lease_until_ms) or "",
        record and tostring(record.config_revision) or "",
        record and record.payload_sha256 or "",
        record and record.payload or "",
        record and record.policy_key or "",
        value ~= nil and tostring(value) or "",
        secondary ~= nil and tostring(secondary) or "",
    }
end

if not now_ms then return result("not_current", "redis_time_invalid") end

local function safe_identifier(value)
    return type(value) == "string" and #value >= 1 and #value <= 128
        and string.match(value, "^[A-Za-z0-9][A-Za-z0-9_.:-]*$") ~= nil
end

local function safe_domain(value)
    if type(value) ~= "string" or #value < 1 or #value > 253
        or string.match(value, "^[a-z0-9.-]+$") == nil
        or string.sub(value, 1, 1) == "." or string.sub(value, -1) == "."
        or string.match(value, "%.%.") ~= nil then
        return false
    end
    for label in string.gmatch(value .. ".", "(.-)%.") do
        if #label < 1 or #label > 63
            or string.match(label, "^[a-z0-9]") == nil
            or string.match(label, "[a-z0-9]$") == nil then
            return false
        end
    end
    return true
end

local function routing_revision_valid(value)
    return type(value) == "string" and #value >= 1 and #value <= 64
        and string.match(value, "^[A-Za-z0-9][A-Za-z0-9._+%-]*$") ~= nil
end

local function valid_sha(value, length)
    return type(value) == "string" and #value == length
        and string.match(value, "^[0-9a-f]+$") ~= nil
end

local function decimal_seconds(text, maximum)
    if type(text) ~= "string" or #text == 0 then return nil end
    if string.match(text, "^[0-9]+$") == nil
        and string.match(text, "^[0-9]+%.[0-9]+$") == nil then
        return nil
    end
    local value = tonumber(text)
    if not value or value ~= value or value < 0 or value > maximum then return nil end
    return value
end

local expected_types = {"hash", "hash", "zset", "zset", "set", "set", "hash"}

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
    if not safe_identifier(route.shard_id)
        or not canonical_positive(route.routing_epoch)
        or route.engine_owner ~= "python"
        or canonical_uint(route.claim_sequence) == nil then
        return nil
    end
    return route
end

local function route_matches(route)
    if route.shard_id ~= shard_id then return false, "shard_id_mismatch" end
    if route.routing_epoch ~= routing_epoch then return false, "routing_epoch_mismatch" end
    if route.engine_owner ~= engine_owner then return false, "engine_owner_mismatch" end
    return true, nil
end

local function namespace_valid()
    if not key_types_valid() then return nil end
    return load_route()
end

local function index_count(task)
    local count = 0
    if redis.call("ZSCORE", KEYS[3], task) then count = count + 1 end
    if redis.call("ZSCORE", KEYS[4], task) then count = count + 1 end
    count = count + redis.call("SISMEMBER", KEYS[5], task)
    count = count + redis.call("SISMEMBER", KEYS[6], task)
    return count
end

local function zscore(key, task)
    local raw = redis.call("ZSCORE", key, task)
    if not raw then return nil end
    local value = tonumber(raw)
    if not value or value ~= value or value < 0 or value > MAX_INTEGER
        or value ~= math.floor(value) then
        return nil
    end
    return value
end

local record_fields = {
    task_id = true,
    task_kind = true,
    state = true,
    shard_id = true,
    routing_epoch = true,
    engine_owner = true,
    config_revision = true,
    policy_key = true,
    domain = true,
    payload = true,
    payload_sha256 = true,
    payload_sha1 = true,
    claim_token = true,
    claim_sequence = true,
    lease_until_ms = true,
    ready_at_ms = true,
    visible_at_ms = true,
    failures = true,
}

local envelope_fields = {
    schema_version = true,
    task_kind = true,
    task_id = true,
    board_id = true,
    source_url = true,
    policy_key = true,
    domain = true,
    shard_id = true,
    routing_epoch = true,
    engine_owner = true,
    config_revision = true,
    initial_ready_at_ms = true,
    browser_backend = true,
    routing_revision = true,
    scraper_type = true,
    scraper_step = true,
    render = true,
    wait = true,
    wait_fallback = true,
    timeout_ms = true,
    parser_config = true,
    assignment_digest_sha256 = true,
}

local function exact_fields(value, allowed, expected)
    local count = 0
    for field, _ in pairs(value) do
        if not allowed[field] then return false end
        count = count + 1
    end
    return count == expected
end

local function valid_record(record)
    if type(record) ~= "table" or not exact_fields(record, record_fields, 18)
        or not safe_identifier(record.task_id)
        or record.task_kind ~= "scrape"
        or not safe_identifier(record.shard_id)
        or not bounded_number(record.routing_epoch, 1, MAX_INTEGER)
        or record.engine_owner ~= "python"
        or not bounded_number(record.config_revision, 1, MAX_INTEGER)
        or record.policy_key ~= "lightpanda-b0-v1"
        or not safe_domain(record.domain)
        or type(record.payload) ~= "string" or #record.payload > MAX_PAYLOAD_BYTES
        or not valid_sha(record.payload_sha256, 64)
        or not valid_sha(record.payload_sha1, 40)
        or redis.sha1hex(record.payload) ~= record.payload_sha1
        or not bounded_number(record.failures, 0, MAX_FAILURES) then
        return false
    end

    local decoded_ok, envelope = pcall(cjson.decode, record.payload)
    if not decoded_ok or type(envelope) ~= "table"
        or not exact_fields(envelope, envelope_fields, 22)
        or envelope.schema_version ~= "lightpanda-b0-task-v1"
        or envelope.task_kind ~= "scrape"
        or envelope.task_id ~= record.task_id
        or not safe_identifier(envelope.board_id)
        or type(envelope.source_url) ~= "string" or #envelope.source_url == 0
        or envelope.policy_key ~= record.policy_key
        or envelope.domain ~= record.domain
        or envelope.shard_id ~= record.shard_id
        or envelope.routing_epoch ~= record.routing_epoch
        or envelope.engine_owner ~= record.engine_owner
        or envelope.config_revision ~= record.config_revision
        or not bounded_number(envelope.initial_ready_at_ms, 0, MAX_INTEGER)
        or envelope.browser_backend ~= "lightpanda"
        or not routing_revision_valid(envelope.routing_revision)
        or envelope.scraper_type ~= "json-ld"
        or envelope.scraper_step ~= 0
        or envelope.render ~= true
        or envelope.wait ~= "load"
        or envelope.wait_fallback ~= cjson.null
        or not bounded_number(envelope.timeout_ms, 1, MAX_LEASE_TTL_MS)
        or type(envelope.parser_config) ~= "table"
        or envelope.parser_config.browser_backend ~= "lightpanda"
        or envelope.parser_config.routing_revision ~= envelope.routing_revision
        or envelope.parser_config.render ~= true
        or envelope.parser_config.wait ~= "load"
        or envelope.parser_config.wait_fallback ~= cjson.null
        or envelope.parser_config.timeout ~= envelope.timeout_ms
        or not valid_sha(envelope.assignment_digest_sha256, 64) then
        return false
    end

    if record.state == "ready" then
        return record.claim_token == cjson.null
            and record.claim_sequence == cjson.null
            and record.lease_until_ms == cjson.null
            and bounded_number(record.ready_at_ms, 0, MAX_INTEGER) ~= nil
            and bounded_number(record.visible_at_ms, record.ready_at_ms, MAX_INTEGER) ~= nil
    end
    if record.state == "inflight" then
        if type(record.claim_token) ~= "string"
            or not bounded_number(record.claim_sequence, 1, MAX_INTEGER)
            or not bounded_number(record.lease_until_ms, 1, MAX_INTEGER)
            or record.ready_at_ms ~= cjson.null
            or record.visible_at_ms ~= cjson.null then
            return false
        end
        return record.claim_token
            == tostring(record.routing_epoch) .. ":" .. tostring(record.claim_sequence)
    end
    if record.state == "dead" or record.state == "terminal" then
        return record.claim_token == cjson.null
            and record.claim_sequence == cjson.null
            and record.lease_until_ms == cjson.null
            and record.ready_at_ms == cjson.null
            and record.visible_at_ms == cjson.null
    end
    return false
end

local function decode_record(task)
    local encoded = redis.call("HGET", KEYS[2], task)
    if not encoded then return nil end
    local decode_ok, record = pcall(cjson.decode, encoded)
    if not decode_ok or not valid_record(record) or record.task_id ~= task then return nil end
    return record
end

local function record_index_valid(record)
    if index_count(record.task_id) ~= 1 then return false end
    if record.state == "ready" then
        local score = zscore(KEYS[3], record.task_id)
        return score ~= nil and score == record.visible_at_ms
    end
    if record.state == "inflight" then
        return zscore(KEYS[4], record.task_id) == record.lease_until_ms
    end
    if record.state == "dead" then
        return redis.call("SISMEMBER", KEYS[5], record.task_id) == 1
    end
    return redis.call("SISMEMBER", KEYS[6], record.task_id) == 1
end

local holder_fields = {
    task_id = true,
    domain = true,
    shard_id = true,
    routing_epoch = true,
    engine_owner = true,
    config_revision = true,
    claim_token = true,
    lease_until_ms = true,
    payload_sha256 = true,
}

local function valid_holder_shape(holder, expected_domain)
    return type(holder) == "table"
        and exact_fields(holder, holder_fields, 9)
        and safe_identifier(holder.task_id)
        and holder.domain == expected_domain
        and safe_identifier(holder.shard_id)
        and bounded_number(holder.routing_epoch, 1, MAX_INTEGER) ~= nil
        and holder.engine_owner == "python"
        and bounded_number(holder.config_revision, 1, MAX_INTEGER) ~= nil
        and type(holder.claim_token) == "string"
        and bounded_number(holder.lease_until_ms, 1, MAX_INTEGER) ~= nil
        and valid_sha(holder.payload_sha256, 64)
end

local function holder_matches_record(holder, record)
    return valid_holder_shape(holder, record.domain)
        and holder.task_id == record.task_id
        and holder.shard_id == record.shard_id
        and holder.routing_epoch == record.routing_epoch
        and holder.engine_owner == record.engine_owner
        and holder.config_revision == record.config_revision
        and holder.claim_token == record.claim_token
        and holder.lease_until_ms == record.lease_until_ms
        and holder.payload_sha256 == record.payload_sha256
end

local function holder_for(record)
    local encoded = redis.call("HGET", KEYS[7], record.domain)
    if not encoded then return nil end
    local decode_ok, holder = pcall(cjson.decode, encoded)
    if not decode_ok or not holder_matches_record(holder, record) then
        return false
    end
    return holder
end

local function holder_is_current(holder)
    if type(holder) ~= "table" or not valid_holder_shape(holder, holder.domain) then
        return false
    end
    local held = decode_record(holder.task_id)
    if held == nil or held.state ~= "inflight" or not record_index_valid(held) then
        return false
    end
    return holder_matches_record(holder, held)
end

local function store(record)
    redis.call("HSET", KEYS[2], record.task_id, cjson.encode(record))
end

local function store_holder(record)
    redis.call("HSET", KEYS[7], record.domain, cjson.encode({
        task_id = record.task_id,
        domain = record.domain,
        shard_id = record.shard_id,
        routing_epoch = record.routing_epoch,
        engine_owner = record.engine_owner,
        config_revision = record.config_revision,
        claim_token = record.claim_token,
        lease_until_ms = record.lease_until_ms,
        payload_sha256 = record.payload_sha256,
    }))
end

local function load_current(expected_state, require_token)
    local route = namespace_valid()
    if not route then return nil, "not_current", "namespace_corrupt" end
    local matches, route_reason = route_matches(route)
    if not matches then return nil, "fenced", route_reason end
    if not safe_identifier(task_id) or canonical_positive(config_revision) == nil then
        return nil, "not_current", "invalid_task_identity"
    end
    local record = decode_record(task_id)
    if not record then return nil, "not_current", "record_corrupt" end
    if not record_index_valid(record) then
        return nil, "not_current", "conservation_violation"
    end
    if record.state ~= expected_state then return nil, "not_current", "state_mismatch" end
    if record.shard_id ~= shard_id or tostring(record.routing_epoch) ~= routing_epoch
        or record.engine_owner ~= engine_owner then
        return nil, "fenced", "record_fence_mismatch"
    end
    if tostring(record.config_revision) ~= config_revision then
        return nil, "fenced", "config_revision_mismatch"
    end
    if payload_sha256 ~= record.payload_sha256 or payload_sha1 ~= record.payload_sha1 then
        return nil, "fenced", "payload_digest_mismatch"
    end
    if require_token and claim_token ~= record.claim_token then
        return nil, "fenced", "claim_token_mismatch"
    end
    if expected_state == "inflight" then
        local expected_lease_until = canonical_positive(ARGV[17])
        if not expected_lease_until then
            return nil, "not_current", "invalid_lease_fence"
        end
        if expected_lease_until ~= record.lease_until_ms then
            return nil, "fenced", "lease_deadline_mismatch"
        end
        local holder = holder_for(record)
        if not holder then return nil, "not_current", "origin_holder_corrupt" end
    end
    return record, nil, nil
end

if not safe_identifier(shard_id) or canonical_positive(routing_epoch) == nil
    or engine_owner ~= "python" then
    return result("not_current", "invalid_route")
end

if operation == "initialize" then
    if not key_types_valid() then return result("not_current", "namespace_corrupt") end
    if redis.call("EXISTS", unpack(KEYS)) == 0 then
        redis.call(
            "HSET", KEYS[1],
            "shard_id", shard_id,
            "routing_epoch", routing_epoch,
            "engine_owner", engine_owner,
            "claim_sequence", "0"
        )
        return result("accepted", "initialized")
    end
    local route = namespace_valid()
    if not route then return result("not_current", "namespace_corrupt") end
    local matches, reason = route_matches(route)
    if not matches then return result("fenced", reason) end
    return result("accepted", "already_initialized")
end

local route = namespace_valid()
if not route then return result("not_current", "namespace_corrupt") end
local matches, route_reason = route_matches(route)
if not matches then return result("fenced", route_reason) end

if operation == "register" then
    local ready_at = canonical_uint(ARGV[9])
    if not safe_identifier(task_id) or canonical_positive(config_revision) == nil
        or ready_at == nil or type(payload) ~= "string" or #payload > MAX_PAYLOAD_BYTES
        or not valid_sha(payload_sha256, 64) or not valid_sha(payload_sha1, 40)
        or redis.sha1hex(payload) ~= payload_sha1 then
        return result("not_current", "invalid_task_envelope")
    end
    if redis.call("HEXISTS", KEYS[2], task_id) == 1 then
        local existing = decode_record(task_id)
        if not existing then return result("not_current", "record_corrupt") end
        if not record_index_valid(existing) then
            return result("not_current", "conservation_violation")
        end
        if existing.shard_id == shard_id
            and tostring(existing.routing_epoch) == routing_epoch
            and existing.engine_owner == engine_owner
            and tostring(existing.config_revision) == config_revision
            and existing.payload == payload
            and existing.payload_sha256 == payload_sha256
            and existing.payload_sha1 == payload_sha1 then
            return result("accepted", "already_registered", nil, ready_at)
        end
        return result("not_current", "task_already_exists")
    end
    if index_count(task_id) ~= 0 then
        return result("not_current", "conservation_violation")
    end
    if redis.call("HLEN", KEYS[2]) >= MAX_RECORDS then
        return result("not_current", "namespace_full")
    end
    local record = {
        task_id = task_id,
        task_kind = "scrape",
        state = "ready",
        shard_id = shard_id,
        routing_epoch = tonumber(routing_epoch),
        engine_owner = engine_owner,
        config_revision = tonumber(config_revision),
        policy_key = "",
        domain = "",
        payload = payload,
        payload_sha256 = payload_sha256,
        payload_sha1 = payload_sha1,
        claim_token = cjson.null,
        claim_sequence = cjson.null,
        lease_until_ms = cjson.null,
        ready_at_ms = ready_at,
        visible_at_ms = ready_at,
        failures = 0,
    }
    local decode_ok, envelope = pcall(cjson.decode, payload)
    if not decode_ok or type(envelope) ~= "table" then
        return result("not_current", "invalid_task_envelope")
    end
    record.policy_key = envelope.policy_key
    record.domain = envelope.domain
    if not valid_record(record) then return result("not_current", "invalid_task_envelope") end
    store(record)
    redis.call("ZADD", KEYS[3], ready_at, task_id)
    return result("accepted", "registered", nil, ready_at)
end

if operation == "claim_next" then
    local lease_ttl = canonical_positive(ARGV[8])
    local scan_limit = canonical_positive(ARGV[14])
    local default_delay = decimal_seconds(ARGV[15], 86400)
    if not lease_ttl or lease_ttl > MAX_LEASE_TTL_MS then
        return result("not_current", "invalid_lease_ttl")
    end
    if not scan_limit or scan_limit > MAX_SCAN or not default_delay then
        return result("not_current", "invalid_claim_policy")
    end
    local due = redis.call("ZRANGEBYSCORE", KEYS[3], "-inf", now_ms, "LIMIT", 0, scan_limit)
    for _, candidate in ipairs(due) do
        local record = decode_record(candidate)
        if not record then return result("not_current", "record_corrupt") end
        if record.state ~= "ready" or not record_index_valid(record) then
            return result("not_current", "conservation_violation")
        end
        if record.shard_id ~= shard_id or tostring(record.routing_epoch) ~= routing_epoch
            or record.engine_owner ~= engine_owner then
            return result("fenced", "record_fence_mismatch")
        end

        local encoded_holder = redis.call("HGET", KEYS[7], record.domain)
        if encoded_holder then
            local holder_ok, holder = pcall(cjson.decode, encoded_holder)
            if not holder_ok or not valid_holder_shape(holder, record.domain)
                or not holder_is_current(holder) then
                return result("not_current", "origin_holder_corrupt")
            end
            if holder.lease_until_ms <= now_ms then
                local recheck_at = now_ms + ORIGIN_RECHECK_MS
                record.visible_at_ms = recheck_at
                store(record)
                redis.call("ZADD", KEYS[3], recheck_at, candidate)
            else
                local recheck_at = math.min(holder.lease_until_ms, now_ms + ORIGIN_RECHECK_MS)
                record.visible_at_ms = recheck_at
                store(record)
                redis.call("ZADD", KEYS[3], recheck_at, candidate)
            end
        else
            local rate_key = "ratelimit:" .. record.domain
            local rate_type = redis.call("TYPE", rate_key)["ok"]
            if rate_type ~= "none" and rate_type ~= "string" then
                return result("not_current", "rate_limit_corrupt")
            end
            local rate_until_raw = redis.call("GET", rate_key)
            local rate_until = nil
            if rate_until_raw then
                rate_until = decimal_seconds(rate_until_raw, MAX_INTEGER / 1000)
                if not rate_until then return result("not_current", "rate_limit_corrupt") end
            end
            if rate_until and rate_until > now_seconds then
                local parked_until = math.ceil(rate_until * 1000)
                if parked_until > MAX_INTEGER then
                    return result("not_current", "numeric_overflow")
                end
                record.visible_at_ms = parked_until
                store(record)
                redis.call("ZADD", KEYS[3], parked_until, candidate)
            else
                local delay_key = "delay:" .. record.domain
                local delay_type = redis.call("TYPE", delay_key)["ok"]
                if delay_type ~= "none" and delay_type ~= "string" then
                    return result("not_current", "delay_corrupt")
                end
                local delay_raw = redis.call("GET", delay_key)
                local delay = delay_raw and decimal_seconds(delay_raw, 86400) or default_delay
                if not delay then return result("not_current", "delay_corrupt") end
                local sequence = canonical_uint(route.claim_sequence)
                if not sequence or sequence >= MAX_INTEGER then
                    return result("not_current", "claim_sequence_exhausted")
                end
                local lease_until = checked_add(now_ms, lease_ttl)
                if not lease_until then return result("not_current", "numeric_overflow") end
                sequence = sequence + 1
                local next_allowed = now_seconds + delay
                redis.call("SET", rate_key, tostring(next_allowed), "EX", math.ceil(delay) + 1)
                redis.call("HSET", KEYS[1], "claim_sequence", tostring(sequence))
                record.state = "inflight"
                record.claim_sequence = sequence
                record.claim_token = routing_epoch .. ":" .. tostring(sequence)
                record.lease_until_ms = lease_until
                record.ready_at_ms = cjson.null
                record.visible_at_ms = cjson.null
                redis.call("ZREM", KEYS[3], candidate)
                redis.call("ZADD", KEYS[4], lease_until, candidate)
                store(record)
                store_holder(record)
                return result("accepted", "claimed", record)
            end
        end
    end
    return result("not_current", "no_work")
end

if operation == "heartbeat" then
    local lease_ttl = canonical_positive(ARGV[8])
    if not lease_ttl or lease_ttl > MAX_LEASE_TTL_MS then
        return result("not_current", "invalid_lease_ttl")
    end
    local record, decision, reason = load_current("inflight", true)
    if not record then return result(decision, reason) end
    if record.lease_until_ms <= now_ms then return result("not_current", "lease_expired") end
    local lease_until = checked_add(now_ms, lease_ttl)
    if not lease_until then return result("not_current", "numeric_overflow") end
    if lease_until <= record.lease_until_ms then
        return result("not_current", "lease_not_extended")
    end
    record.lease_until_ms = lease_until
    redis.call("ZADD", KEYS[4], lease_until, task_id)
    store(record)
    store_holder(record)
    return {
        "accepted",
        "lease_extended",
        tostring(now_ms),
        record.task_id,
        record.claim_token,
        tostring(record.lease_until_ms),
        tostring(record.config_revision),
        record.payload_sha256,
        "",
        "",
        "",
        "",
    }
end

if operation == "complete" then
    local record, decision, reason = load_current("inflight", true)
    if not record then return result(decision, reason) end
    if record.lease_until_ms <= now_ms then return result("not_current", "lease_expired") end
    local completed_token = record.claim_token
    local completed_lease_until = record.lease_until_ms
    redis.call("ZREM", KEYS[4], task_id)
    redis.call("HDEL", KEYS[7], record.domain)
    record.state = "terminal"
    record.claim_token = cjson.null
    record.claim_sequence = cjson.null
    record.lease_until_ms = cjson.null
    record.ready_at_ms = cjson.null
    record.visible_at_ms = cjson.null
    store(record)
    redis.call("SADD", KEYS[6], task_id)
    return {
        "accepted",
        "completed",
        tostring(now_ms),
        record.task_id,
        completed_token,
        tostring(completed_lease_until),
        tostring(record.config_revision),
        record.payload_sha256,
        "",
        "",
        "",
        "",
    }
end

if operation == "reschedule_at" then
    local ready_at = canonical_uint(ARGV[9])
    if ready_at == nil then return result("not_current", "invalid_ready_at") end
    local record, decision, reason = load_current("inflight", true)
    if not record then return result(decision, reason) end
    if record.lease_until_ms <= now_ms then return result("not_current", "lease_expired") end
    local prior_token = record.claim_token
    local prior_lease_until = record.lease_until_ms
    redis.call("ZREM", KEYS[4], task_id)
    redis.call("HDEL", KEYS[7], record.domain)
    record.state = "ready"
    record.claim_token = cjson.null
    record.claim_sequence = cjson.null
    record.lease_until_ms = cjson.null
    record.ready_at_ms = ready_at
    record.visible_at_ms = ready_at
    record.failures = 0
    store(record)
    redis.call("ZADD", KEYS[3], ready_at, task_id)
    return {
        "accepted",
        "rescheduled",
        tostring(now_ms),
        record.task_id,
        prior_token,
        tostring(prior_lease_until),
        tostring(record.config_revision),
        record.payload_sha256,
        "",
        "",
        tostring(ready_at),
        "",
    }
end

if operation == "reactivate" then
    local ready_at = canonical_uint(ARGV[9])
    local previous_payload_sha256 = ARGV[16]
    if not safe_identifier(task_id) or canonical_positive(config_revision) == nil
        or ready_at == nil or type(payload) ~= "string" or #payload > MAX_PAYLOAD_BYTES
        or not valid_sha(payload_sha256, 64) or not valid_sha(payload_sha1, 40)
        or not valid_sha(previous_payload_sha256, 64)
        or redis.sha1hex(payload) ~= payload_sha1 then
        return result("not_current", "invalid_task_envelope")
    end
    local current = decode_record(task_id)
    if not current then return result("not_current", "record_corrupt") end
    if not record_index_valid(current) then
        return result("not_current", "conservation_violation")
    end
    if current.shard_id ~= shard_id or tostring(current.routing_epoch) ~= routing_epoch
        or current.engine_owner ~= engine_owner then
        return result("fenced", "record_fence_mismatch")
    end
    if current.payload_sha256 ~= previous_payload_sha256 then
        return result("fenced", "payload_digest_mismatch")
    end
    if tonumber(config_revision) <= current.config_revision then
        return result("fenced", "config_revision_not_advanced")
    end
    if current.state ~= "dead" and current.state ~= "terminal" then
        return result("not_current", "state_mismatch")
    end
    local record = {
        task_id = task_id,
        task_kind = "scrape",
        state = "ready",
        shard_id = shard_id,
        routing_epoch = tonumber(routing_epoch),
        engine_owner = engine_owner,
        config_revision = tonumber(config_revision),
        policy_key = "",
        domain = "",
        payload = payload,
        payload_sha256 = payload_sha256,
        payload_sha1 = payload_sha1,
        claim_token = cjson.null,
        claim_sequence = cjson.null,
        lease_until_ms = cjson.null,
        ready_at_ms = ready_at,
        visible_at_ms = ready_at,
        failures = 0,
    }
    local decode_ok, envelope = pcall(cjson.decode, payload)
    if not decode_ok or type(envelope) ~= "table" then
        return result("not_current", "invalid_task_envelope")
    end
    record.policy_key = envelope.policy_key
    record.domain = envelope.domain
    if not valid_record(record) then return result("not_current", "invalid_task_envelope") end
    if current.state == "dead" then
        redis.call("SREM", KEYS[5], task_id)
    else
        redis.call("SREM", KEYS[6], task_id)
    end
    store(record)
    redis.call("ZADD", KEYS[3], ready_at, task_id)
    return {
        "accepted",
        "reactivated",
        tostring(now_ms),
        record.task_id,
        "",
        "",
        tostring(record.config_revision),
        record.payload_sha256,
        "",
        "",
        tostring(ready_at),
        "",
    }
end

if operation == "reap_expired" then
    local max_failures = canonical_positive(ARGV[10])
    local scan_limit = canonical_positive(ARGV[14])
    if not max_failures or max_failures > MAX_FAILURES
        or not scan_limit or scan_limit > MAX_SCAN then
        return result("not_current", "invalid_reap_policy")
    end
    local expired = redis.call("ZRANGEBYSCORE", KEYS[4], "-inf", now_ms, "LIMIT", 0, scan_limit)
    local records = {}
    for index, candidate in ipairs(expired) do
        local record = decode_record(candidate)
        if not record or record.state ~= "inflight" or not record_index_valid(record) then
            return result("not_current", "conservation_violation")
        end
        if record.shard_id ~= shard_id or tostring(record.routing_epoch) ~= routing_epoch
            or record.engine_owner ~= engine_owner then
            return result("fenced", "record_fence_mismatch")
        end
        local holder = holder_for(record)
        if not holder then return result("not_current", "origin_holder_corrupt") end
        records[index] = record
    end
    local requeued = 0
    local dead = 0
    for _, record in ipairs(records) do
        redis.call("ZREM", KEYS[4], record.task_id)
        redis.call("HDEL", KEYS[7], record.domain)
        record.failures = record.failures + 1
        record.claim_token = cjson.null
        record.claim_sequence = cjson.null
        record.lease_until_ms = cjson.null
        record.visible_at_ms = cjson.null
        if record.failures >= max_failures then
            record.state = "dead"
            record.ready_at_ms = cjson.null
            redis.call("SADD", KEYS[5], record.task_id)
            dead = dead + 1
        else
            record.state = "ready"
            record.ready_at_ms = now_ms
            record.visible_at_ms = now_ms
            redis.call("ZADD", KEYS[3], now_ms, record.task_id)
            requeued = requeued + 1
        end
        store(record)
    end
    return result("accepted", "reaped", nil, requeued, dead)
end

if operation == "audit" then
    local record_count = redis.call("HLEN", KEYS[2])
    if record_count > MAX_RECORDS then return result("not_current", "audit_too_large") end
    local ready_count = redis.call("ZCARD", KEYS[3])
    local inflight_count = redis.call("ZCARD", KEYS[4])
    local dead_count = redis.call("SCARD", KEYS[5])
    local terminal_count = redis.call("SCARD", KEYS[6])
    if record_count ~= ready_count + inflight_count + dead_count + terminal_count then
        return result("not_current", "conservation_violation")
    end
    local encoded_records = redis.call("HGETALL", KEYS[2])
    for index = 1, #encoded_records, 2 do
        local record = decode_record(encoded_records[index])
        if not record or not record_index_valid(record) then
            return result("not_current", "conservation_violation")
        end
        if record.shard_id ~= shard_id or tostring(record.routing_epoch) ~= routing_epoch
            or record.engine_owner ~= engine_owner then
            return result("fenced", "record_fence_mismatch")
        end
        if record.state == "inflight" then
            local holder = holder_for(record)
            if not holder then return result("not_current", "origin_holder_corrupt") end
        end
    end
    if redis.call("HLEN", KEYS[7]) ~= inflight_count then
        return result("not_current", "conservation_violation")
    end
    local holders = redis.call("HGETALL", KEYS[7])
    for index = 1, #holders, 2 do
        local holder_ok, holder = pcall(cjson.decode, holders[index + 1])
        if not holder_ok or holder.domain ~= holders[index] or not holder_is_current(holder) then
            return result("not_current", "origin_holder_corrupt")
        end
    end
    return result("accepted", "audit_ok", nil, record_count, inflight_count)
end

return result("not_current", "invalid_operation")
