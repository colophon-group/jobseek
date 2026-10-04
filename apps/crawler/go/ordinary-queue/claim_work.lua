-- Claim one task from the tiered domain-based ready queues.
--
-- ARGV[1] = wtype ("simple" or "browser")
-- ARGV[2] = now (float timestamp)
-- ARGV[3] = default_rate_delay (float seconds)
-- ARGV[4] = max_domains_to_check (int)
-- ARGV[5] = lease_ttl (float seconds; lease set on claim — see #3159 / #3173)
-- ARGV[6] = optional 32-character lowercase hex claim token (native callers)
--
-- Returns: {task_id, source_type, domain[, claim_token, leased_until]} or nil
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
local scrape_rotation_key = "ready:rotation:" .. wtype
local max_recurring_monitor_streak = 8
local claim_token = ARGV[6] or ""
local token_key = "inflight_tokens:" .. wtype

-- Planned owners carry immutable DB/installed identities. Compatibility callers
-- cannot bypass an installed projection; planned callers never fall back when
-- the entire Redis database or this projection is lost.
local owner_role = ARGV[7] or ""
local owner_key = "ordinary:ownership:active"
local owner_type = redis.call("TYPE", owner_key)["ok"]
local owner_members = nil
local native_member = nil
local native_snapshot = nil
local owner_cursor_prefix = nil
local function owner_failure()
    return redis.error_reply("ordinary ownership rejected")
end
if owner_role == "" then
    if owner_type ~= "none" then return owner_failure() end
else
    if (owner_role ~= "native" and owner_role ~= "legacy") or
        owner_type ~= "string" or (wtype ~= "simple" and wtype ~= "browser") or
        #(ARGV[8] or "") ~= 64 or string.find(ARGV[8] or "", "[^0-9a-f]") or
        #(ARGV[9] or "") ~= 40 or string.find(ARGV[9] or "", "[^0-9a-f]") or
        #(ARGV[11] or "") ~= 40 or string.find(ARGV[11] or "", "[^0-9a-f]") or
        not string.match(ARGV[10] or "", "^[1-9][0-9]*$") or #(ARGV[10] or "") > 13 or
        max_check < 1 or max_check > 1000 or max_check % 1 ~= 0
    then return owner_failure() end
    local body = redis.call("GET", owner_key)
    if #body > 16777216 or redis.sha1hex(body) ~= ARGV[9] then
        return owner_failure()
    end
    local decoded, plan = pcall(cjson.decode, body)
    if not decoded or type(plan) ~= "table" or
        plan.version ~= "jobseek.ordinary.ownership/v1" or
        plan.routing_epoch ~= tonumber(ARGV[10]) or
        plan.source_revision ~= ARGV[11] or type(plan.members) ~= "table" or
        #plan.members < 1 or #plan.members > 20000
    then return owner_failure() end
    owner_members = {}
    for _, member in ipairs(plan.members) do
        if type(member) ~= "table" or member.kind ~= "monitor" or
            member.worker ~= "simple" or
            (member.profile ~= "greenhouse.token-skip/v1" and member.profile ~= "ashby.token-skip/v1" and member.profile ~= "lever.token-skip/v1" and member.profile ~= "recruitee.api-skip/v1" and member.profile ~= "pinpoint.slug-skip/v1" and member.profile ~= "rss.teamtailor-skip/v1" and member.profile ~= "rss.successfactors-skip/v1") or
            type(member.board_id) ~= "string" or type(member.domain) ~= "string" or
            owner_members[member.board_id] ~= nil
        then return owner_failure() end
        owner_members[member.board_id] = member
    end
    if owner_role == "native" then
        native_member = owner_members[ARGV[12] or ""]
        if claim_token == "" or not native_member or wtype ~= native_member.worker then
            return owner_failure()
        end
        local ok, snapshot = pcall(cjson.decode, ARGV[13] or "")
        if not ok or type(snapshot) ~= "table" or snapshot.domain ~= native_member.domain then
            return owner_failure()
        end
        native_snapshot = snapshot
    elseif claim_token ~= "" or (ARGV[12] or "") ~= "" or (ARGV[13] or "") ~= "" then
        return owner_failure()
    end
    owner_cursor_prefix = "ordinary:claim-cursor:" .. ARGV[8] .. ":" .. wtype .. ":"
end

-- The optional token extends this queue's lease ABI. Validate all new state
-- before popping work: Redis script errors cannot roll back earlier writes.
if claim_token ~= "" and (#claim_token ~= 32 or
    string.find(claim_token, "[^0-9a-f]") ~= nil) then
    return redis.error_reply("ordinary claim token is invalid")
end
if claim_token ~= "" or owner_role ~= "" then
    local clock = redis.call("TIME")
    now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
end
local token_type = redis.call("TYPE", token_key)["ok"]
if token_type ~= "none" and token_type ~= "hash" then
    return redis.error_reply("ordinary claim token index is corrupt")
end
local inflight_type = redis.call("TYPE", "inflight:" .. wtype)["ok"]
if inflight_type ~= "none" and inflight_type ~= "zset" then
    return redis.error_reply("ordinary inflight index is corrupt")
end

-- Fail before popping any task if the persistent cutover guard is corrupt.
-- Redis scripts do not roll back writes after a runtime WRONGTYPE error.
local b0_guard_type = redis.call("TYPE", b0_guard_key)["ok"]
if b0_guard_type ~= "none" and b0_guard_type ~= "hash" then
    return redis.error_reply("lightpanda B0 legacy guard is corrupt")
end
local scrape_rotation_type = redis.call("TYPE", scrape_rotation_key)["ok"]
if scrape_rotation_type ~= "none" and scrape_rotation_type ~= "zset" then
    return redis.error_reply("scrape rotation index is corrupt")
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
local function refresh_ready(domain, not_before, rotate_scrapes)
    -- Rotation is distinct from the tier-2 marker: that marker may instead be
    -- an authoritative future task deadline. Only a successful recurring
    -- scrape claim advances this dedicated per-domain floor.
    local scrape_rotation_floor = tonumber(
        redis.call("ZSCORE", scrape_rotation_key, domain) or "0"
    )
    if rotate_scrapes then
        scrape_rotation_floor = math.max(
            scrape_rotation_floor,
            tonumber(not_before) or 0
        )
        redis.call("ZADD", scrape_rotation_key, scrape_rotation_floor, domain)
    end
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

-- The installed path filters before removal. Native has a canonical-row-locked
-- candidate from the durable cohort; legacy excludes every retained member even
-- if its current config/domain changed. Cursor state advances bounded scans past
-- foreign heads/domains without popping, repairing or throttling their tasks.
if owner_role ~= "" then
    local pending_cursors = {}
    local duplicate_removals = {}
    local function finite(value)
        local number = tonumber(value)
        return number and number == number and number ~= math.huge and
            number ~= -math.huge and number >= 0
    end
    local function typed(key, expected)
        local value = redis.call("TYPE", key)["ok"]
        return value == "none" or value == expected
    end
    local function cursor(key, count)
        if not typed(key, "string") then return nil end
        local raw = redis.call("GET", key)
        local offset = tonumber(raw or "0")
        if not finite(offset) or offset % 1 ~= 0 or offset > 9007199254740991 then return nil end
        if offset >= count then offset = 0 end
        return offset
    end
    local function commit_cursors()
        for key, offset in pairs(pending_cursors) do
            redis.call("SET", key, tostring(offset))
        end
    end
    if not finite(now) or not finite(default_delay) or default_delay > 2147483647 or not finite(lease_ttl) or lease_ttl <= 0 then
        return owner_failure()
    end
    for tier = 0, 2 do
        if not typed("ready:" .. wtype .. ":" .. tier, "zset") then return owner_failure() end
    end
    -- Reject changed/missing native configuration before even cursor mutations.
    if native_member then
        local key = "board:" .. native_member.board_id
        if redis.call("TYPE", key)["ok"] ~= "hash" then return owner_failure() end
        local fields = 0
        for name, value in pairs(native_snapshot) do
            if type(name) ~= "string" or type(value) ~= "string" or
                redis.call("HGET", key, name) ~= value then return owner_failure() end
            fields = fields + 1
        end
        if fields ~= redis.call("HLEN", key) then return owner_failure() end
    end
    for _, tier in ipairs(tier_order) do
        local ready_key = "ready:" .. wtype .. ":" .. tier
        local due_domains = redis.call("ZCOUNT", ready_key, "-inf", tostring(now))
        if due_domains > 0 then
            local candidates = {}
            if native_member then
                local score = redis.call("ZSCORE", ready_key, native_member.domain)
                if score and tonumber(score) <= now then candidates = {native_member.domain} end
            else
                local key = owner_cursor_prefix .. "domains:" .. tier
                local offset = cursor(key, due_domains)
                if not offset then return owner_failure() end
                candidates = redis.call("ZRANGEBYSCORE", ready_key, "-inf", tostring(now), "LIMIT", offset, max_check)
                pending_cursors[key] = (offset + #candidates) % due_domains
            end
            -- Preflight all bounded candidate state before any pop or cursor write.
            for _, domain in ipairs(candidates) do
                for _, prefix in ipairs({"ft_monitors_", "ft_scrapes_", "monitors_", "scrapes_"}) do
                    if not typed(prefix .. wtype .. ":" .. domain, "zset") then return owner_failure() end
                end
                for _, prefix in ipairs({"ratelimit:", "delay:"}) do
                    local key = prefix .. domain
                    if not typed(key, "string") then return owner_failure() end
                    local value = redis.call("GET", key)
                    if value and (not finite(value) or (prefix == "delay:" and tonumber(value) > 2147483647)) then return owner_failure() end
                end
                local rotation = redis.call("ZSCORE", scrape_rotation_key, domain)
                if rotation and not finite(rotation) then return owner_failure() end
            end
            local selected = nil
            local blocked = false
            local function find_task(prefix, domain, kind, priority)
                local queue_key = prefix .. wtype .. ":" .. domain
                if native_member then
                    if kind ~= "monitor" then return nil end
                    local score = redis.call("ZSCORE", queue_key, native_member.board_id)
                    if score and tonumber(score) <= now then
                        if redis.call("ZSCORE", "inflight:" .. wtype, "monitor|" .. domain .. "|" .. native_member.board_id) then
                            duplicate_removals[#duplicate_removals+1] = {queue_key, native_member.board_id, domain}
                        else
                            return {native_member.board_id, kind, domain, priority, queue_key}
                        end
                    end
                    return nil
                end
                local count = redis.call("ZCOUNT", queue_key, "-inf", tostring(now))
                local key = owner_cursor_prefix .. "tasks:" .. queue_key
                local offset = cursor(key, count)
                if not offset then blocked = true; return nil end
                local items = redis.call("ZRANGEBYSCORE", queue_key, "-inf", tostring(now), "LIMIT", offset, 64)
                pending_cursors[key] = count > 0 and (offset + #items) % count or 0
                for _, id in ipairs(items) do
                    local member = kind .. "|" .. domain .. "|" .. id
                    if (kind == "scrape" and redis.call("HEXISTS", b0_guard_key, id) == 1) or
                        (redis.call("HEXISTS", token_key, member) == 1 and redis.call("ZSCORE", "inflight:" .. wtype, member)) then
                        -- Preserve existing B0/native duplicate quarantine. Only
                        -- the extra ready representation is removed, never the
                        -- live lease/token/config or a foreign logical task.
                        duplicate_removals[#duplicate_removals+1] = {queue_key, id, domain}
                    elseif not (kind == "monitor" and owner_members[id]) then
                        return {id, kind, domain, priority, queue_key}
                    end
                end
                return nil
            end
            for _, domain in ipairs(candidates) do
                local rate = redis.call("GET", "ratelimit:" .. domain)
                if not rate or tonumber(rate) <= now then
                    -- First-time work dominates both recurring classes, including
                    -- when an unselected first-time head hides a recurring marker.
                    local first_time = redis.call("ZCARD", "ft_monitors_" .. wtype .. ":" .. domain) +
                        redis.call("ZCARD", "ft_scrapes_" .. wtype .. ":" .. domain) > 0
                    if first_time then
                        selected = find_task("ft_monitors_", domain, "monitor", 0) or
                            find_task("ft_scrapes_", domain, "scrape", 0)
                    elseif tier == 2 and recurring_monitor_streak >= max_recurring_monitor_streak then
                        selected = find_task("scrapes_", domain, "scrape", 2)
                    elseif tier ~= 0 then
                        selected = find_task("monitors_", domain, "monitor", 1)
                        if not selected and tier == 2 then selected = find_task("scrapes_", domain, "scrape", 2) end
                    end
                    if selected then break end
                end
            end
            if blocked then return owner_failure() end
            commit_cursors()
            local repaired_domains = {}
            for _, duplicate in ipairs(duplicate_removals) do
                redis.call("ZREM", duplicate[1], duplicate[2])
                repaired_domains[duplicate[3]] = true
            end
            for domain, _ in pairs(repaired_domains) do refresh_ready(domain, 0, false) end
            if selected then
                local id, kind, domain, priority, queue_key = unpack(selected)
                local rate_delay = tonumber(redis.call("GET", "delay:" .. domain) or default_delay)
                -- All selected state and configuration checks precede this removal.
                redis.call("ZREM", queue_key, id)
                redis.call("SET", "ratelimit:" .. domain, tostring(now + rate_delay), "EX", math.ceil(rate_delay) + 1)
                local member = kind .. "|" .. domain .. "|" .. id
                redis.call("ZADD", "inflight:" .. wtype, now + lease_ttl, member)
                if claim_token ~= "" then redis.call("HSET", token_key, member, claim_token)
                else redis.call("HDEL", token_key, member) end
                if priority == 1 then
                    redis.call("SET", recurring_monitor_streak_key, tostring(math.min(recurring_monitor_streak + 1, max_recurring_monitor_streak)))
                elseif priority == 2 then redis.call("SET", recurring_monitor_streak_key, "0") end
                refresh_ready(domain, now + rate_delay, priority == 2)
                if claim_token ~= "" then
                    return {id, kind, domain, claim_token, redis.call("ZSCORE", "inflight:" .. wtype, member)}
                end
                return {id, kind, domain}
            end
            -- Foreign work at this global priority is processed by its owner;
            -- neither side bypasses first-time or the recurring fairness turn.
            return nil
        end
    end
    return nil
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
            refresh_ready(domain, tonumber(rl_val), false)
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

            -- 3. Recurring monitors (only if due). An armed tier-2 pass is
            -- scrape-only: a stale tier-2 marker must be rebuilt rather than
            -- spending the fairness turn on a ninth monitor from that domain.
            if not task_id and tier ~= 0 and not fairness_scrape_first then
                local items = redis.call("ZRANGEBYSCORE", "monitors_" .. wtype .. ":" .. domain, "-inf", tostring(now), "LIMIT", 0, 1)
                if #items > 0 then
                    redis.call("ZREM", "monitors_" .. wtype .. ":" .. domain, items[1])
                    task_id = items[1]
                    source_type = "monitor"
                    claimed_priority = 1
                end
            end

            -- 4. Recurring scrapes (only if due)
            if not task_id and tier == 2 and not fairness_scrape_first then
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
                refresh_ready(domain, 0, false)
            end

            -- A tokenized claimant cannot replace any inflight attempt. A
            -- legacy claimant cannot replace a tokenized inflight attempt,
            -- including one awaiting normal expiry reaping.
            -- Keep the old lease/config and let its settlement/reaper advertise
            -- the next attempt through the existing scheduler.
            if task_id then
                local member = source_type .. "|" .. domain .. "|" .. task_id
                if (claim_token ~= "" or redis.call("HEXISTS", token_key, member) == 1) and
                    redis.call("ZSCORE", "inflight:" .. wtype, member) ~= false then
                    task_id = nil
                    claimed_priority = nil
                    refresh_ready(domain, 0, false)
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
                if claim_token ~= "" then
                    redis.call("HSET", token_key, inflight_member, claim_token)
                else
                    redis.call("HDEL", token_key, inflight_member)
                end

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

                refresh_ready(domain, now + rate_delay, claimed_priority == 2)

                if claim_token ~= "" then
                    return {task_id, source_type, domain, claim_token,
                        redis.call("ZSCORE", "inflight:" .. wtype, inflight_member)}
                end
                return {task_id, source_type, domain}
            else
                -- A stale marker must not erase a domain that still owns
                -- future work (for example after removing its earliest
                -- board). Rebuild from the authoritative queues instead.
                refresh_ready(domain, 0, false)
            end
        end
    end

    -- If a bounded candidate batch contained only stale/rate-limited markers,
    -- rebuild them and yield rather than crossing the marker's priority. The
    -- next atomic claim continues cleanup. Once this tier is genuinely empty,
    -- a subsequent claim may advance.
    if #candidates > 0 then
        return nil
    end
end

return nil
