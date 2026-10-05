-- Cold retirement restores only the selected ordinary monitors. The caller
-- holds the original host lock and exclusive SQL barriers, with all writers
-- stopped. SQL deadlines/receipts are observations; this script never invents
-- a successful attempt. Preflight the whole cohort before any Redis mutation.
local function retirement_finite(value)
    return value ~= nil and value == value and value ~= math.huge and value ~= -math.huge
end

local function retirement_type(key, expected)
    local kind = redis.call("TYPE", key)["ok"]
    return kind == "none" or kind == expected
end

local function prepare_first_retirement(plan, raw, exists, adopting)
    local ok, rows = pcall(cjson.decode, raw)
    if not ok or type(rows) ~= "table" or #rows < #plan.members or #rows == 0 then return nil end
    for _, worker in ipairs({"simple", "browser"}) do
        for _, item in ipairs({{"inflight:", "zset"}, {"inflight_tokens:", "hash"},
            {"inflight_strikes:", "hash"}, {"monitor_repair_due:", "hash"},
            {"ready:rotation:", "zset"}}) do
            if not retirement_type(item[1] .. worker, item[2]) then return nil end
        end
        for tier = 0, 2 do
            if not retirement_type("ready:" .. worker .. ":" .. tier, "zset") then return nil end
        end
    end
    local now = nil
    if adopting then
        local clock = redis.call("TIME")
        now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
    end
    local detail_members = {}
    for _, member in ipairs(plan.details or {}) do detail_members[member.board_id] = member end
    local changes, domains, seen = {}, {}, {}
    for index, row in ipairs(rows) do
        if type(row) ~= "table" then return nil end
        local member, kind, id = plan.members[index], "monitor", row.board_id
        if index > #plan.members then
            member, kind, id = detail_members[row.board_id], "scrape", row.task_id
            if row.kind ~= "scrape" or type(id) ~= "string" or #id ~= 36 or string.find(id,"[^0-9a-f%-]") then return nil end
        elseif row.kind ~= nil or row.task_id ~= nil then return nil end
        if not member or seen[kind.."|"..id] or row.board_id ~= member.board_id or (row.domain ~= member.domain and not (kind == "scrape" and member.domain == "*"))
            or type(row.domain) ~= "string" or #row.domain < 1 or #row.domain > 253 or string.find(row.domain,"[%c|]")
            or type(row.completed) ~= "boolean" or type(row.config) ~= "table"
            or type(row.learned_host) ~= "string" or #row.learned_host > 253
            or string.find(row.learned_host, "[%c|]") ~= nil
            or (row.learned_host ~= "" and not row.completed) then return nil end
        local worker = member.worker
        if worker ~= "simple" and worker ~= "browser" then return nil end
        -- Adoption grants no current attempt receipt. A monitor in the wrong
        -- queue namespace or a completed/current native attempt is a refusal.
        if adopting then
            if row.completed or row.learned_host ~= "" then return nil end
            local other = worker == "simple" and "browser" or "simple"
            local task = kind .. "|" .. row.domain .. "|" .. id
            if redis.call("ZSCORE", "inflight:" .. other, task)
                or redis.call("HEXISTS", "inflight_tokens:" .. other, task) == 1 then return nil end
        end
        seen[kind.."|"..id] = true
        local due = nil
        if row.due ~= cjson.null then
            if type(row.due) ~= "string" then return nil end
            due = tonumber(row.due)
            if not retirement_finite(due) or due < 0 then return nil end
        end
        local board_key = kind == "scrape" and ("scrape:"..id) or ("board:" .. row.board_id)
        if redis.call("TYPE", board_key)["ok"] ~= "hash" then return nil end
        local fields = 0
        for field, value in pairs(row.config) do
            if type(field) ~= "string" or type(value) ~= "string"
                or redis.call("HGET", board_key, field) ~= value then return nil end
            fields = fields + 1
        end
        if fields == 0 or redis.call("HLEN", board_key) ~= fields then return nil end
        local domain = row.domain
        local keys = {"ft_monitors_" .. worker .. ":" .. domain, "ft_scrapes_" .. worker .. ":" .. domain,
            "monitors_" .. worker .. ":" .. domain, "scrapes_" .. worker .. ":" .. domain}
        for _, key in ipairs(keys) do
            if not retirement_type(key, "zset") then return nil end
            -- Both extremes must be finite: removing an owned head must not
            -- expose an invalid foreign deadline after writes have started.
            for _, position in ipairs({0, -1}) do
                local edge = redis.call("ZRANGE", key, position, position, "WITHSCORES")
                if #edge >= 2 and not retirement_finite(tonumber(edge[2])) then return nil end
            end
        end
        if not retirement_type("ratelimit:" .. domain, "string") then return nil end
        local rate = redis.call("GET", "ratelimit:" .. domain)
        local rotation = redis.call("ZSCORE", "ready:rotation:" .. worker, domain)
        local task = kind.."|" .. domain .. "|" .. id
        local repair = kind == "monitor" and redis.call("HGET", "monitor_repair_due:" .. worker, task) or false
        if (rate ~= false and not retirement_finite(tonumber(rate)))
            or (rotation ~= false and not retirement_finite(tonumber(rotation)))
            or (repair ~= false and not retirement_finite(tonumber(repair))) then return nil end
        local lease = redis.call("ZSCORE", "inflight:" .. worker, task)
        local token = redis.call("HGET", "inflight_tokens:" .. worker, task)
        if lease ~= false then
            if not retirement_finite(tonumber(lease)) then return nil end
            if adopting then
                -- Redis's own clock and the caller's live cold SQL barriers
                -- must both prove expiry. Preserve canonical deadlines and
                -- strikes; do not manufacture a successful fetch or extend
                -- the normal lease budget.
                if tonumber(lease) > now or (token ~= false and (type(token) ~= "string"
                    or #token ~= 32 or string.find(token, "[^0-9a-f]") ~= nil)) then return nil end
            elseif not exists or type(token) ~= "string" or #token ~= 32
                or string.find(token, "[^0-9a-f]") ~= nil then return nil end
        elseif token ~= false then
            return nil
        end
        local first_key, recurring_key = keys[1], keys[3]
        if kind == "scrape" then first_key, recurring_key = keys[2], keys[4] end
        local first = redis.call("ZSCORE", first_key, id)
        local recurring = redis.call("ZSCORE", recurring_key, id)
        -- A completed SQL receipt can precede ACK or reaping. Repair its queued
        -- deadline from SQL as well; do not resurrect dead letters/orphans.
        if lease ~= false or (row.completed and (first ~= false or recurring ~= false)) then
            if due and repair ~= false then due = math.min(due, tonumber(repair)) end
            local changed = lease ~= false or first ~= false or row.learned_host ~= ""
                or (due == nil and recurring ~= false)
                or (due ~= nil and (recurring == false or tonumber(recurring) ~= due))
                or repair ~= false
            if changed then
                changes[#changes + 1] = {row = row, task = task, due = due, kind = kind, id = id, first_key = first_key, recurring_key = recurring_key, config_key = board_key, worker = worker}
                domains[worker .. "|" .. domain] = {worker = worker, domain = domain, rate = tonumber(rate) or 0, rotation = tonumber(rotation) or 0}
            end
        end
    end
    return changes, domains
end

local function apply_first_retirement(changes, domains)
    for _, change in ipairs(changes) do
        local row, task, worker = change.row, change.task, change.worker
        redis.call("ZREM", change.first_key, change.id)
        redis.call("ZREM", change.recurring_key, change.id)
        if change.due ~= nil then
            redis.call("ZADD", change.recurring_key, change.due, change.id)
        end
        redis.call("ZREM", "inflight:" .. worker, task)
        redis.call("HDEL", "inflight_tokens:" .. worker, task)
        if change.kind == "monitor" then redis.call("HDEL", "monitor_repair_due:" .. worker, task) end
        if row.completed then redis.call("HDEL", "inflight_strikes:" .. worker, task) end
        if row.learned_host ~= "" then redis.call("HSET", change.config_key, "egress_host", row.learned_host) end
    end
    for _, floors in pairs(domains) do
        local domain, worker = floors.domain, floors.worker
        for tier = 0, 2 do redis.call("ZREM", "ready:" .. worker .. ":" .. tier, domain) end
        local function minimum(prefix)
            local first = redis.call("ZRANGE", prefix .. domain, 0, 0, "WITHSCORES")
            if #first >= 2 then return tonumber(first[2]) end
            return nil
        end
        local ft_monitor, ft_scrape = minimum("ft_monitors_" .. worker .. ":"), minimum("ft_scrapes_" .. worker .. ":")
        local monitor, scrape = minimum("monitors_" .. worker .. ":"), minimum("scrapes_" .. worker .. ":")
        if ft_monitor ~= nil or ft_scrape ~= nil then
            local first = math.min(ft_monitor or math.huge, ft_scrape or math.huge)
            redis.call("ZADD", "ready:" .. worker .. ":0", math.max(floors.rate, first), domain)
        else
            if monitor ~= nil then redis.call("ZADD", "ready:" .. worker .. ":1", math.max(floors.rate, monitor), domain) end
            if scrape ~= nil then redis.call("ZADD", "ready:" .. worker .. ":2", math.max(floors.rate, floors.rotation, scrape), domain) end
        end
        if scrape == nil then redis.call("ZREM", "ready:rotation:" .. worker, domain) end
    end
end
