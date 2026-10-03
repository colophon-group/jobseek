
for _, wtype in ipairs({"simple", "browser"}) do
    local rotation_type = redis.call("TYPE", "ready:rotation:" .. wtype)["ok"]
    if rotation_type ~= "none" and rotation_type ~= "zset" then
        return redis.error_reply("scrape rotation index is corrupt")
    end
end

local function refresh_ready(wtype, domain)
    local scrape_rotation_key = "ready:rotation:" .. wtype
    local scrape_rotation_floor = tonumber(
        redis.call("ZSCORE", scrape_rotation_key, domain) or "0"
    )
    for tier = 0, 2 do
        redis.call("ZREM", "ready:" .. wtype .. ":" .. tier, domain)
    end

    local rate_limit = tonumber(redis.call("GET", "ratelimit:" .. domain) or "0")
    local first_time_score = nil
    for _, prefix in ipairs({"ft_monitors_", "ft_scrapes_"}) do
        local head = redis.call("ZRANGE", prefix .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
        if #head >= 2 then
            local score = tonumber(head[2])
            if first_time_score == nil or score < first_time_score then
                first_time_score = score
            end
        end
    end
    if first_time_score ~= nil then
        if redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain) == 0 then
            redis.call("ZREM", scrape_rotation_key, domain)
        end
        redis.call(
            "ZADD", "ready:" .. wtype .. ":0",
            math.max(rate_limit, first_time_score), domain
        )
        return
    end

    local monitor = redis.call(
        "ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES"
    )
    if #monitor >= 2 then
        redis.call(
            "ZADD", "ready:" .. wtype .. ":1",
            math.max(rate_limit, tonumber(monitor[2])), domain
        )
    end
    local scrape = redis.call(
        "ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES"
    )
    if #scrape >= 2 then
        redis.call(
            "ZADD", "ready:" .. wtype .. ":2",
            math.max(rate_limit, scrape_rotation_floor, tonumber(scrape[2])), domain
        )
    else
        redis.call("ZREM", scrape_rotation_key, domain)
    end
end

local parked = 0
for index = 1, #ARGV, 2 do
    local board_id = ARGV[index]
    local domain = ARGV[index + 1]
    for _, wtype in ipairs({"simple", "browser"}) do
        parked = parked + redis.call("ZREM", "ft_monitors_" .. wtype .. ":" .. domain, board_id)
        parked = parked + redis.call("ZREM", "monitors_" .. wtype .. ":" .. domain, board_id)
        local member = "monitor|" .. domain .. "|" .. board_id
        parked = parked + redis.call("ZREM", "inflight:" .. wtype, member)
        redis.call("HDEL", "inflight_strikes:" .. wtype, member)
        redis.call("ZREM", "deadletter:" .. wtype, member)
        refresh_ready(wtype, domain)
    end
end
return parked
