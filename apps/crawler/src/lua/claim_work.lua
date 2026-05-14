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

                -- Record lease entry in inflight ZSET (#3159 / #3173).
                -- Member encodes (task_type, domain, task_id) so the
                -- reaper can re-enqueue without a side hash.
                local inflight_member = source_type .. "|" .. domain .. "|" .. task_id
                redis.call("ZADD", "inflight:" .. wtype, now + lease_ttl, inflight_member)

                -- Recompute domain's tier and re-add if tasks remain.
                --
                -- Pick the bucket with the lowest next-due score (clamped to
                -- now + rate_delay). This avoids the priority-inversion bug
                -- where a domain with a far-future recurring monitor and a
                -- due-now scrape backlog gets parked in tier 1 at the
                -- monitor's future score and never claims its scrapes.
                -- See issue #3016 for the empirical reproduction.
                --
                -- Tier semantics preserved:
                --   - first-time tasks (ft_*) always land in tier 0
                --   - recurring monitors land in tier 1
                --   - recurring scrapes land in tier 2
                -- When ft has any task, ft wins (highest priority).
                -- Otherwise monitor vs scrape compete by next-due score with
                -- monitor as tiebreaker (strict-less-than means monitor wins
                -- when scores are equal — original tier ordering).
                local ft_mon_count = redis.call("ZCARD", "ft_monitors_" .. wtype .. ":" .. domain)
                local ft_scr_count = redis.call("ZCARD", "ft_scrapes_" .. wtype .. ":" .. domain)

                local ft_score = nil
                if ft_mon_count > 0 then
                    local r1 = redis.call("ZRANGE", "ft_monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
                    if #r1 >= 2 then ft_score = tonumber(r1[2]) end
                end
                if ft_scr_count > 0 then
                    local r2 = redis.call("ZRANGE", "ft_scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
                    if #r2 >= 2 then
                        local s = tonumber(r2[2])
                        if ft_score == nil or s < ft_score then ft_score = s end
                    end
                end

                local mon_score = nil
                local has_monitors = redis.call("ZCARD", "monitors_" .. wtype .. ":" .. domain)
                if has_monitors > 0 then
                    local r3 = redis.call("ZRANGE", "monitors_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
                    if #r3 >= 2 then mon_score = tonumber(r3[2]) end
                end

                local scr_score = nil
                local has_scrapes = redis.call("ZCARD", "scrapes_" .. wtype .. ":" .. domain)
                if has_scrapes > 0 then
                    local r4 = redis.call("ZRANGE", "scrapes_" .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
                    if #r4 >= 2 then scr_score = tonumber(r4[2]) end
                end

                local next_score = nil
                local next_tier = nil
                if ft_score ~= nil then
                    next_score = ft_score
                    next_tier = 0
                end
                if mon_score ~= nil and (next_score == nil or mon_score < next_score) then
                    next_score = mon_score
                    next_tier = 1
                end
                if scr_score ~= nil and (next_score == nil or scr_score < next_score) then
                    next_score = scr_score
                    next_tier = 2
                end

                if next_score ~= nil then
                    redis.call("ZADD", "ready:" .. wtype .. ":" .. next_tier,
                               math.max(now + rate_delay, next_score), domain)
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
