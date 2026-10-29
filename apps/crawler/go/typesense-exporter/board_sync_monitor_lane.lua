-- Retire a stale opposite-worker monitor duplicate after registry publication.
-- Keep the selected lane's cadence, cache and inflight attempt unchanged.
-- Mixed-owner migration owns its own routing; this repair is for the default
-- catalogue. All refusal checks precede the first Redis effect.
-- ARGV: selected worker, canonical domain, board identity.
local wtype, domain, board_id = ARGV[1], ARGV[2], ARGV[3]
if #ARGV ~= 3 or (wtype ~= "simple" and wtype ~= "browser") or not domain or domain == "" or not board_id or board_id == "" then
    return redis.error_reply("monitor lane repair arguments are invalid")
end
local other = wtype == "simple" and "browser" or "simple"
local function require_type(key, expected)
    local actual = redis.call("TYPE", key)["ok"]
    if actual ~= "none" and actual ~= expected then
        return false
    end
    return true
end
local wrong_first = "ft_monitors_" .. other .. ":" .. domain
local wrong_recurring = "monitors_" .. other .. ":" .. domain
if not require_type(wrong_first, "zset") or not require_type(wrong_recurring, "zset") then
    return redis.error_reply("monitor opposite lane is corrupt")
end
local wrong_queued = redis.call("ZSCORE", wrong_first, board_id) or redis.call("ZSCORE", wrong_recurring, board_id)
local member = "monitor|" .. domain .. "|" .. board_id
if not require_type("inflight:" .. other, "zset") or not require_type("inflight_tokens:" .. other, "hash") then
    return redis.error_reply("monitor opposite inflight state is corrupt")
end
local wrong_inflight = redis.call("ZSCORE", "inflight:" .. other, member) or redis.call("ZSCORE", "inflight:" .. other, board_id)
    or redis.call("HEXISTS", "inflight_tokens:" .. other, member) == 1 or redis.call("HEXISTS", "inflight_tokens:" .. other, board_id) == 1
if not wrong_queued and not wrong_inflight then return 0 end
if not require_type("ordinary:ownership:active", "string") or not require_type("lightpanda-b0:legacy-guard", "hash") then
    return redis.error_reply("monitor lane ownership guard is corrupt")
end
if redis.call("EXISTS", "ordinary:ownership:active") == 1 or redis.call("HLEN", "lightpanda-b0:legacy-guard") > 0 or redis.call("EXISTS", "lightpanda-b0:producer-owner") == 1 then
    return 0
end
for _, lane in ipairs({wtype, other}) do
    if not require_type("inflight:" .. lane, "zset") or not require_type("inflight_tokens:" .. lane, "hash") or not require_type("monitor_repair_due:" .. lane, "hash") then
        return redis.error_reply("monitor lane inflight state is corrupt")
    end
    for _, prefix in ipairs({"ft_monitors_", "monitors_"}) do
        if not require_type(prefix .. lane .. ":" .. domain, "zset") then
            return redis.error_reply("monitor lane schedule is corrupt")
        end
    end
end
if redis.call("ZSCORE", "inflight:" .. other, member) or redis.call("ZSCORE", "inflight:" .. other, board_id) or redis.call("HEXISTS", "inflight_tokens:" .. other, member) == 1 or redis.call("HEXISTS", "inflight_tokens:" .. other, board_id) == 1 then
    return redis.error_reply("monitor opposite lane is inflight")
end
if not require_type("board:" .. board_id, "hash") or redis.call("HGET", "board:" .. board_id, "domain") ~= domain or redis.call("HGET", "board:" .. board_id, "monitor_needs_browser") ~= (wtype == "browser" and "1" or "0") then
    return redis.error_reply("monitor canonical lane cache is unavailable")
end
local selected_ready = redis.call("ZSCORE", "ft_monitors_" .. wtype .. ":" .. domain, board_id) or redis.call("ZSCORE", "monitors_" .. wtype .. ":" .. domain, board_id)
local selected_inflight = redis.call("ZSCORE", "inflight:" .. wtype, member)
if not selected_ready then
    local token = redis.call("HGET", "inflight_tokens:" .. wtype, member)
    if not selected_inflight or not token or #token ~= 32 or not string.match(token, "^[0-9a-f]+$") then
        return redis.error_reply("monitor selected lane has no authoritative schedule")
    end
end
if not require_type("ratelimit:" .. domain, "string") or not require_type("ready:rotation:" .. other, "zset") then
    return redis.error_reply("monitor lane readiness state is corrupt")
end
local function finite(value)
    local number = tonumber(value)
    return number and number == number and number ~= math.huge and number ~= -math.huge
end
local limit = redis.call("GET", "ratelimit:" .. domain)
local rotation = redis.call("ZSCORE", "ready:rotation:" .. other, domain)
if (limit and not finite(limit)) or (rotation and not finite(rotation)) or (selected_inflight and not finite(selected_inflight)) then
    return redis.error_reply("monitor lane readiness score is corrupt")
end
for tier = 0, 2 do
    if not require_type("ready:" .. other .. ":" .. tier, "zset") then
        return redis.error_reply("monitor lane readiness index is corrupt")
    end
end
for _, prefix in ipairs({"ft_monitors_", "monitors_", "ft_scrapes_", "scrapes_"}) do
    local key = prefix .. other .. ":" .. domain
    if not require_type(key, "zset") then
        return redis.error_reply("monitor lane domain schedule is corrupt")
    end
    local heads = redis.call("ZRANGE", key, 0, 1, "WITHSCORES")
    for index = 2, #heads, 2 do
        if not finite(heads[index]) then
            return redis.error_reply("monitor lane domain deadline is corrupt")
        end
    end
end

local function refresh_ready(wtype)
    local scrape_rotation_key = "ready:rotation:" .. wtype
    local scrape_rotation_floor = tonumber(
        redis.call("ZSCORE", scrape_rotation_key, domain) or "0"
    )
    for tier = 0, 2 do
        redis.call("ZREM", "ready:" .. wtype .. ":" .. tier, domain)
    end

    local rl_val = redis.call("GET", "ratelimit:" .. domain)
    local floor = 0
    if rl_val then floor = tonumber(rl_val) end

    local ft_score = nil
    for _, prefix in ipairs({"ft_monitors_", "ft_scrapes_"}) do
        local head = redis.call("ZRANGE", prefix .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
        if #head >= 2 then
            local score = tonumber(head[2])
            if ft_score == nil or score < ft_score then ft_score = score end
        end
    end
    if ft_score ~= nil then
        if redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain) == 0 then
            redis.call("ZREM", scrape_rotation_key, domain)
        end
        redis.call("ZADD", "ready:" .. wtype .. ":0", math.max(floor, ft_score), domain)
        return
    end

    local mon_head = redis.call("ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    if #mon_head >= 2 then
        redis.call("ZADD", "ready:" .. wtype .. ":1", math.max(floor, tonumber(mon_head[2])), domain)
    end

    local scrape_head = redis.call("ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    if #scrape_head >= 2 then
        redis.call(
            "ZADD",
            "ready:" .. wtype .. ":2",
            math.max(floor, scrape_rotation_floor, tonumber(scrape_head[2])),
            domain
        )
    else
        redis.call("ZREM", scrape_rotation_key, domain)
    end
end

local removed = redis.call("ZREM", wrong_first, board_id) + redis.call("ZREM", wrong_recurring, board_id)
redis.call("HDEL", "monitor_repair_due:" .. other, member)
refresh_ready(other)
return removed
