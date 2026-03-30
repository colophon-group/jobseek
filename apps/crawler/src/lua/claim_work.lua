-- Claim one task from the tiered domain-based ready queues.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = now (float timestamp)
-- ARGV[3] = default_rate_delay (float seconds)
-- ARGV[4] = max_domains_to_check (int)
--
-- Returns: {task_id, source_type, domain} or nil

local wtype = ARGV[1]
local now = tonumber(ARGV[2])
local default_delay = tonumber(ARGV[3])
local max_check = tonumber(ARGV[4]) or 10

-- Try tiers in priority order: 0=first-time, 1=monitors, 2=scrapes
for tier = 0, 2 do
    local ready_key = "ready:" .. wtype .. ":" .. tier

    -- Get candidate domains with score <= now (due or overdue)
    local candidates = redis.call("ZRANGEBYSCORE", ready_key, "-inf", tostring(now), "LIMIT", 0, max_check)

    for _, domain in ipairs(candidates) do
        -- Check shared rate limit
        local rl_key = "ratelimit:" .. domain
        local rl_val = redis.call("GET", rl_key)
        if rl_val and tonumber(rl_val) > now then
            -- Rate-limited: update ready score to when it becomes available
            redis.call("ZADD", ready_key, tonumber(rl_val), domain)
        else
            -- Domain is available — try to pop a task in priority order
            local task_id = nil
            local source_type = nil

            -- 1. First-time monitors (unconditional pop)
            local ft_mon = redis.call("ZPOPMIN", "ft_monitors_" .. wtype .. ":" .. domain, 1)
            if #ft_mon >= 2 then
                task_id = ft_mon[1]
                source_type = "monitor"
            end

            -- 2. First-time scrapes (unconditional pop)
            if not task_id then
                local ft_scr = redis.call("ZPOPMIN", "ft_scrapes_" .. wtype .. ":" .. domain, 1)
                if #ft_scr >= 2 then
                    task_id = ft_scr[1]
                    source_type = "scrape"
                end
            end

            -- 3. Recurring monitors (only if due)
            if not task_id then
                local items = redis.call("ZRANGEBYSCORE", "monitors_" .. wtype .. ":" .. domain, "-inf", tostring(now), "LIMIT", 0, 1)
                if #items > 0 then
                    redis.call("ZREM", "monitors_" .. wtype .. ":" .. domain, items[1])
                    task_id = items[1]
                    source_type = "monitor"
                end
            end

            -- 4. Recurring scrapes (only if due)
            if not task_id then
                local items = redis.call("ZRANGEBYSCORE", "scrapes_" .. wtype .. ":" .. domain, "-inf", tostring(now), "LIMIT", 0, 1)
                if #items > 0 then
                    redis.call("ZREM", "scrapes_" .. wtype .. ":" .. domain, items[1])
                    task_id = items[1]
                    source_type = "scrape"
                end
            end

            if task_id then
                -- Set shared rate limit
                local domain_delay = redis.call("GET", "delay:" .. domain)
                local rate_delay = default_delay
                if domain_delay then
                    rate_delay = tonumber(domain_delay)
                end
                local rl_ttl = math.ceil(rate_delay) + 1
                redis.call("SET", rl_key, tostring(now + rate_delay), "EX", rl_ttl)

                -- Remove domain from current ready tier
                redis.call("ZREM", ready_key, domain)

                -- Recompute domain's tier and re-add if tasks remain
                local has_ft = (
                    redis.call("ZCARD", "ft_monitors_" .. wtype .. ":" .. domain) +
                    redis.call("ZCARD", "ft_scrapes_" .. wtype .. ":" .. domain)
                )
                local has_monitors = redis.call("ZCARD", "monitors_" .. wtype .. ":" .. domain)
                local has_scrapes = redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain)

                if has_ft > 0 then
                    redis.call("ZADD", "ready:" .. wtype .. ":0", now + rate_delay, domain)
                elseif has_monitors > 0 then
                    local next_mon = redis.call("ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
                    local mon_score = now + rate_delay
                    if #next_mon >= 2 then
                        mon_score = math.max(now + rate_delay, tonumber(next_mon[2]))
                    end
                    redis.call("ZADD", "ready:" .. wtype .. ":1", mon_score, domain)
                elseif has_scrapes > 0 then
                    local next_scr = redis.call("ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
                    local scr_score = now + rate_delay
                    if #next_scr >= 2 then
                        scr_score = math.max(now + rate_delay, tonumber(next_scr[2]))
                    end
                    redis.call("ZADD", "ready:" .. wtype .. ":2", scr_score, domain)
                end
                -- else: domain fully drained, don't re-add

                return {task_id, source_type, domain}
            else
                -- Domain had no claimable tasks — remove from ready queue
                redis.call("ZREM", ready_key, domain)
            end
        end
    end
end

return nil
