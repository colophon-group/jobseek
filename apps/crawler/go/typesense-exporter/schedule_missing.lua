-- Default-runtime repair: only restore a wholly absent canonical projection.
-- Existing caches, queues, attempts, deadletters and retry state are retained.
-- The caller runs the original canonical rebuild while writers are quiesced.
local repair_mode, repair_delay = KEYS[1], KEYS[2]
local repair_worker, repair_domain, repair_id = ARGV[1], ARGV[2], ARGV[3]
local function repair_finite(value)
    local n = tonumber(value)
    return n and n == n and n ~= math.huge and n ~= -math.huge
end
local function repair_type(key, expected)
    local t = redis.call('TYPE', key)['ok']
    return t == 'none' or t == expected
end
if #KEYS ~= 2 or (repair_mode ~= 'inspect' and repair_mode ~= 'apply') or not repair_finite(repair_delay) or tonumber(repair_delay) < 0
    or (repair_worker ~= 'simple' and repair_worker ~= 'browser') or ARGV[5] ~= 'scrape' then
    return redis.error_reply('invalid missing schedule repair')
end
if redis.call('EXISTS', 'ordinary:ownership:active') ~= 0 or redis.call('EXISTS', 'lightpanda-b0:producer-owner') ~= 0
    or not repair_type('lightpanda-b0:legacy-guard', 'hash') or redis.call('HLEN', 'lightpanda-b0:legacy-guard') ~= 0 then
    return redis.error_reply('missing schedule repair requires default ownership')
end
local cache = 'scrape:' .. repair_id
if not repair_type(cache, 'hash') then return redis.error_reply('invalid canonical schedule cache') end
if redis.call('EXISTS', cache) == 1 then return 0 end
local board
for i = 8, #ARGV, 2 do if ARGV[i] == 'board_id' then board = ARGV[i + 1] end end
if not board or not repair_type('board:' .. board, 'hash')
    or redis.call('HGET', 'board:' .. board, 'scraper_needs_browser') ~= (repair_worker == 'browser' and '1' or '0') then
    return redis.error_reply('canonical schedule board cache changed')
end
local member = 'scrape|' .. repair_domain .. '|' .. repair_id
for _, worker in ipairs({'simple', 'browser'}) do
    for _, prefix in ipairs({'ft_scrapes_', 'scrapes_'}) do
        local key = prefix .. worker .. ':' .. repair_domain
        if not repair_type(key, 'zset') then return redis.error_reply('invalid missing schedule queue') end
        if redis.call('ZSCORE', key, repair_id) then return 0 end
    end
    for _, prefix in ipairs({'inflight:', 'deadletter:'}) do
        local key = prefix .. worker
        if not repair_type(key, 'zset') then return redis.error_reply('invalid missing schedule lifecycle') end
        if redis.call('ZSCORE', key, member) or redis.call('ZSCORE', key, repair_id) then return 0 end
    end
    for _, prefix in ipairs({'inflight_tokens:', 'inflight_strikes:'}) do
        local key = prefix .. worker
        if not repair_type(key, 'hash') then return redis.error_reply('invalid missing schedule attempt state') end
        if prefix == 'inflight_tokens:' and (redis.call('HEXISTS', key, member) == 1 or redis.call('HEXISTS', key, repair_id) == 1) then return 0 end
    end
end
for _, prefix in ipairs({'ft_monitors_', 'monitors_', 'ft_scrapes_', 'scrapes_'}) do
    local key = prefix .. repair_worker .. ':' .. repair_domain
    if not repair_type(key, 'zset') then return redis.error_reply('invalid missing schedule domain') end
    for _, position in ipairs({0, -1}) do
        local head = redis.call('ZRANGE', key, position, position, 'WITHSCORES')
        if #head >= 2 and not repair_finite(head[2]) then return redis.error_reply('invalid missing schedule deadline') end
    end
end
for tier = 0, 2 do
    if not repair_type('ready:' .. repair_worker .. ':' .. tier, 'zset') then return redis.error_reply('invalid missing schedule readiness') end
end
if not repair_type('ready:rotation:' .. repair_worker, 'zset') or not repair_type('monitor_repair_due:' .. repair_worker, 'hash')
    or not repair_type('ratelimit:' .. repair_domain, 'string') or not repair_type('delay:' .. repair_domain, 'string') then
    return redis.error_reply('invalid missing schedule domain state')
end
local rate = redis.call('GET', 'ratelimit:' .. repair_domain)
local rotation = redis.call('ZSCORE', 'ready:rotation:' .. repair_worker, repair_domain)
local existing_delay = redis.call('GET', 'delay:' .. repair_domain)
if (rate and not repair_finite(rate)) or (rotation and not repair_finite(rotation)) or (existing_delay and (not repair_finite(existing_delay) or tonumber(existing_delay) < 0)) then return redis.error_reply('invalid missing schedule floor') end
if repair_mode == 'inspect' then return 1 end
