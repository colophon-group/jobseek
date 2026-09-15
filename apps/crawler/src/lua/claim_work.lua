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
local b0_guard_key = "lightpanda-b0:legacy-guard"
local recurring_monitor_streak_key = "claim:recurring-monitor-streak:" .. wtype
local max_recurring_monitor_streak = 8

-- Fail before popping any task if the persistent cutover guard is corrupt.
-- Redis scripts do not roll back writes after a runtime WRONGTYPE error.
local b0_guard_type = redis.call("TYPE", b0_guard_key)["ok"]
if b0_guard_type ~= "none" and b0_guard_type ~= "hash" then
    return redis.error_reply("lightpanda B0 legacy guard is corrupt")
end

-- Tier 0 remains strict for newly discovered work. Once it is empty, bound
-- recurring-detail starvation by allowing tier 2 to lead after a finite run
-- of tier-1 monitor claims. The shared per-worker-type counter is updated in
-- this same Lua transaction, so concurrent workers cannot each consume their
-- own independent monitor budget and postpone the fairness claim forever.
local recurring_monitor_streak_raw = redis.call("GET", recurring_monitor_streak_key)
local recurring_monitor_streak = 0
if recurring_monitor_streak_raw then
    recurring_monitor_streak = tonumber(recurring_monitor_streak_raw)
    if not recurring_monitor_streak or
        recurring_monitor_streak ~= recurring_monitor_streak or
        recurring_monitor_streak == math.huge or
        recurring_monitor_streak == -math.huge or
        recurring_monitor_streak < 0 or
        recurring_monitor_streak % 1 ~= 0
    then
        return redis.error_reply("recurring monitor claim streak is corrupt")
    end
end

local tier_order = {0, 1, 2}
if recurring_monitor_streak >= max_recurring_monitor_streak then
    tier_order = {0, 2, 1}
end

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

-- Try strict first-time priority, then the bounded recurring order selected
-- above: normally monitors before scrapes, but one due scrape after at most
-- eight consecutive recurring monitor claims.
for _, tier in ipairs(tier_order) do
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
            local claimed_priority = nil
            local fairness_scrape_first =
                tier == 2 and recurring_monitor_streak >= max_recurring_monitor_streak

            -- 1. First-time monitors (unconditional pop)
            local ft_mon = redis.call("ZPOPMIN", "ft_monitors_" .. wtype .. ":" .. domain, 1)
            if #ft_mon >= 2 then
                task_id = ft_mon[1]
                source_type = "monitor"
                claimed_priority = 0
            end

            -- 2. First-time scrapes (unconditional pop)
            if not task_id then
                local ft_scr = redis.call("ZPOPMIN", "ft_scrapes_" .. wtype .. ":" .. domain, 1)
                if #ft_scr >= 2 then
                    task_id = ft_scr[1]
                    source_type = "scrape"
                    claimed_priority = 0
                end
            end

            -- Once the recurring-monitor budget is exhausted, a tier-2 pass
            -- must prefer the due scrape represented by that marker. Otherwise
            -- a due monitor on the same domain can keep winning inside this
            -- loop and defeat the global eight-claim bound.
            if not task_id and fairness_scrape_first then
                local items = redis.call("ZRANGEBYSCORE", "scrapes_" .. wtype .. ":" .. domain, "-inf", tostring(now), "LIMIT", 0, 1)
                if #items > 0 then
                    redis.call("ZREM", "scrapes_" .. wtype .. ":" .. domain, items[1])
                    task_id = items[1]
                    source_type = "scrape"
                    claimed_priority = 2
                end
            end

            -- 3. Recurring monitors (only if due)
            if not task_id then
                local items = redis.call("ZRANGEBYSCORE", "monitors_" .. wtype .. ":" .. domain, "-inf", tostring(now), "LIMIT", 0, 1)
                if #items > 0 then
                    redis.call("ZREM", "monitors_" .. wtype .. ":" .. domain, items[1])
                    task_id = items[1]
                    source_type = "monitor"
                    claimed_priority = 1
                end
            end

            -- 4. Recurring scrapes (only if due)
            if not task_id and not fairness_scrape_first then
                local items = redis.call("ZRANGEBYSCORE", "scrapes_" .. wtype .. ":" .. domain, "-inf", tostring(now), "LIMIT", 0, 1)
                if #items > 0 then
                    redis.call("ZREM", "scrapes_" .. wtype .. ":" .. domain, items[1])
                    task_id = items[1]
                    source_type = "scrape"
                    claimed_priority = 2
                end
            end

            -- A persistent B0 cutover guard wins against stale/old producers.
            -- Quarantine the popped legacy representation before acquiring a
            -- Chromium lease; activation itself refuses when this script won
            -- first and already wrote an in-flight member.
            if task_id and source_type == "scrape" and
                redis.call("HEXISTS", b0_guard_key, task_id) == 1
            then
                task_id = nil
                claimed_priority = nil
                refresh_ready(domain, 0)
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

                if claimed_priority == 1 then
                    redis.call(
                        "SET",
                        recurring_monitor_streak_key,
                        tostring(math.min(
                            recurring_monitor_streak + 1,
                            max_recurring_monitor_streak
                        ))
                    )
                elseif claimed_priority == 2 then
                    redis.call("SET", recurring_monitor_streak_key, "0")
                end

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
