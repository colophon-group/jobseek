-- Remove one board monitor schedule and atomically rebuild every ready
-- representation for the affected domain.
--
-- ARGV[1] = domain
-- ARGV[2] = board_id
--
-- A board can move between simple/browser workers or first-time/recurring
-- queues across deploys, so remove it from every monitor queue variant. The
-- ready ZSETs must be rebuilt in the same script: leaving a stale tier-1
-- marker lets claim_work enter this domain and pop a due scrape before an
-- unrelated due tier-1 monitor.

local domain = ARGV[1]
local board_id = ARGV[2]

local function refresh_ready(wtype)
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
        redis.call("ZADD", "ready:" .. wtype .. ":0", math.max(floor, ft_score), domain)
        return
    end

    local mon_head = redis.call("ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    if #mon_head >= 2 then
        redis.call("ZADD", "ready:" .. wtype .. ":1", math.max(floor, tonumber(mon_head[2])), domain)
    end

    local scrape_head = redis.call("ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
    if #scrape_head >= 2 then
        redis.call("ZADD", "ready:" .. wtype .. ":2", math.max(floor, tonumber(scrape_head[2])), domain)
    end
end

for _, wtype in ipairs({"simple", "browser"}) do
    redis.call("ZREM", "ft_monitors_" .. wtype .. ":" .. domain, board_id)
    redis.call("ZREM", "monitors_" .. wtype .. ":" .. domain, board_id)
end

for _, wtype in ipairs({"simple", "browser"}) do
    refresh_ready(wtype)
end

redis.call("DEL", "board:" .. board_id)
return 1
