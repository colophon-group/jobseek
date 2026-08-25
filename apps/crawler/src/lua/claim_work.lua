-- Claim one task from the tiered domain-based ready queues.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = now (float timestamp)
-- ARGV[3] = default_rate_delay (float seconds)
-- ARGV[4] = max_domains_to_check (int)
-- ARGV[5] = lease_ttl (float seconds; lease set on claim — see #3159 / #3173)
--
-- Returns: {task_id, source_type, domain} or nil
--
-- Lease semantics (added in #3159 / #3173):
--   When a task is claimed, this script also records a lease entry in
--   the per-worker-type inflight ZSET (``inflight:<wtype>``) with
--   member ``"<task_type>|<domain>|<task_id>"`` and score
--   ``now + lease_ttl``. If the worker dies between claim and
--   completion, a periodic reaper (``reap_expired.lua``) re-enqueues
--   the task back to its per-domain ZSET so it isn't lost.
--
--   On successful processing the worker MUST call ``complete_task.lua``
--   to remove the inflight entry. Heartbeats during long-running
--   processing extend the lease via ``heartbeat_task.lua``.

local wtype = ARGV[1]
local now = tonumber(ARGV[2])
local default_delay = tonumber(ARGV[3])
local max_check = tonumber(ARGV[4]) or 10
local lease_ttl = tonumber(ARGV[5]) or 600

-- Rebuild every ready representation for one domain from its authoritative
-- per-domain queues. A recurring domain may need TWO entries: one carrying
-- the next monitor deadline in tier 1 and one carrying the next scrape
-- deadline in tier 2. Keeping only whichever task is currently earliest can
-- strand a monitor behind a permanently overdue scrape backlog: once the
-- monitor's later deadline passes, nothing promotes the domain from tier 2,
-- and sustained tier-1 traffic prevents claim_work from ever entering it.
--
-- First-time work remains strict tier 0. ``not_before`` applies the shared
-- throttle after a claim without changing either underlying task deadline.
local function refresh_ready(domain, not_before)
    for tier = 0, 2 do
        redis.call("ZREM", "ready:" .. wtype .. ":" .. tier, domain)
    end

    local floor = tonumber(not_before) or 0
    local rl_val = redis.call("GET", "ratelimit:" .. domain)
    if rl_val then floor = math.max(floor, tonumber(rl_val)) end

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
            -- Rate-limited: move every representation to when the shared
            -- domain lease becomes available.
            refresh_ready(domain, tonumber(rl_val))
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

                -- Record lease entry in inflight ZSET (#3159 / #3173).
                -- Member encodes (task_type, domain, task_id) so the
                -- reaper can re-enqueue without a side hash.
                local inflight_member = source_type .. "|" .. domain .. "|" .. task_id
                redis.call("ZADD", "inflight:" .. wtype, now + lease_ttl, inflight_member)

                refresh_ready(domain, now + rate_delay)

                return {task_id, source_type, domain}
            else
                -- A stale marker must not erase a domain that still owns
                -- future work (for example after removing its earliest
                -- board). Rebuild from the authoritative queues instead.
                refresh_ready(domain, 0)
            end
        end
    end
end

return nil
