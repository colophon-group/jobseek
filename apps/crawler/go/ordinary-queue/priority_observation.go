package queue

import "context"

// Observe the same due tier and recurring fairness turn as claim_work.lua.
// This is only a selection hint: the original atomic pop rechecks all current
// priority, ownership, configuration, throttle and lease conditions.
const readyPriorityObservation = `
local raw = redis.call('GET', KEYS[4])
local streak = tonumber(raw or '0')
if not streak or streak ~= streak or streak == math.huge or
    streak == -math.huge or streak < 0 or streak % 1 ~= 0 then
    return redis.error_reply('ordinary priority observation rejected')
end
for i = 1, 3 do
    local kind = redis.call('TYPE', KEYS[i])['ok']
    if kind ~= 'none' and kind ~= 'zset' then
        return redis.error_reply('ordinary priority observation rejected')
    end
end
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local order = {1, 2, 3}
if streak >= 8 then order = {1, 3, 2} end
for _, index in ipairs(order) do
    if redis.call('ZCOUNT', KEYS[index], '-inf', tostring(now)) > 0 then
        return index - 1
    end
end
return -1
`

func (c *Client) readyPriority(ctx context.Context, worker WorkerType) (int, error) {
	if !validWorker(worker) {
		return 0, ErrConfiguration
	}
	key := "ready:" + string(worker)
	priority, err := c.redis.EvalRO(ctx, readyPriorityObservation, []string{
		key + ":0", key + ":1", key + ":2", "claim:recurring-monitor-streak:" + string(worker),
	}).Int64()
	if err != nil || priority < -1 || priority > 2 {
		return 0, ErrObservation
	}
	return int(priority), nil
}
