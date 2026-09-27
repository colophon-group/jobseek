-- Exact-member recovery envelope. The caller wraps the canonical enqueue
-- script as enqueue(); all guards execute before its first write.
local p = cjson.decode(ARGV[1])
local function check_type(key, expected)
    local actual = redis.call('TYPE', key)['ok']
    if actual ~= 'none' and actual ~= expected then error('deadletter key type changed') end
end
local dead = 'deadletter:' .. p.wtype
check_type(dead, 'zset')
local score = redis.call('ZSCORE', dead, p.member)
if score == false or tonumber(score) ~= p.reaped_at then
    return redis.error_reply('deadletter descriptor changed; inspect again')
end
local schedule = 'not_applicable'
if p.needs_schedule then
    local board = 'board:' .. p.task_id
    check_type(board, 'hash')
    local actual = redis.call('HGETALL', board)
    local expected_count = 0
    for k, v in pairs(p.config) do
        expected_count = expected_count + 1
        if redis.call('HGET', board, k) ~= v then
            return redis.error_reply('deadletter config changed; inspect again')
        end
    end
    if #actual ~= expected_count * 2 or expected_count == 0 then
        return redis.error_reply('deadletter config changed; inspect again')
    end
    local wtype, domain = p.expected_wtype, p.expected_domain
    for _, prefix in ipairs({'ft_monitors_', 'monitors_', 'ft_scrapes_', 'scrapes_'}) do
        check_type(prefix .. wtype .. ':' .. domain, 'zset')
    end
    check_type('inflight:' .. wtype, 'zset')
    check_type('ready:rotation:' .. wtype, 'zset')
    check_type('monitor_repair_due:' .. wtype, 'hash')
    check_type('delay:' .. domain, 'string')
    check_type('ratelimit:' .. domain, 'string')
    local ratelimit = redis.call('GET', 'ratelimit:' .. domain)
    if ratelimit and (not tonumber(ratelimit) or tonumber(ratelimit) ~= tonumber(ratelimit)) then
        return redis.error_reply('deadletter rate limit is corrupt')
    end
    for tier = 0, 2 do check_type('ready:' .. wtype .. ':' .. tier, 'zset') end
end
if p.superseded then
    check_type('inflight:' .. p.wtype, 'zset')
    check_type('ft_monitors_' .. p.wtype .. ':' .. p.domain, 'zset')
    check_type('monitors_' .. p.wtype .. ':' .. p.domain, 'zset')
    check_type('inflight_strikes:' .. p.wtype, 'hash')
    if redis.call('ZSCORE', 'inflight:' .. p.wtype, p.member) ~= false then
        return redis.error_reply('superseded route is currently inflight; inspect again')
    end
end
if p.needs_schedule then
    local wtype, domain = p.expected_wtype, p.expected_domain
    local current = 'monitor|' .. domain .. '|' .. p.task_id
    if redis.call('ZSCORE', 'ft_monitors_' .. wtype .. ':' .. domain, p.task_id) ~= false
       or redis.call('ZSCORE', 'monitors_' .. wtype .. ':' .. domain, p.task_id) ~= false
       or redis.call('ZSCORE', 'inflight:' .. wtype, current) ~= false then
        schedule = 'already_scheduled'
    else
        local now_parts = redis.call('TIME')
        local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000
        local added = enqueue({wtype, domain, p.task_id, tostring(now), 'monitor', '0', tostring(now)})
        if type(added) ~= 'number' then return added end
        if added ~= 1 then return redis.error_reply('deadletter schedule was not created') end
        redis.call('SET', 'delay:' .. domain, p.delay)
        schedule = 'enqueued'
    end
end
if p.superseded then
    redis.call('ZREM', 'ft_monitors_' .. p.wtype .. ':' .. p.domain, p.task_id)
    redis.call('ZREM', 'monitors_' .. p.wtype .. ':' .. p.domain, p.task_id)
    redis.call('HDEL', 'inflight_strikes:' .. p.wtype, p.member)
end
redis.call('ZREM', dead, p.member)
return schedule
