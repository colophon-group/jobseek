-- The original cold SQL session and staged plan bind every row. All checks for
-- a batch precede effects. A partial invocation remains staged and replayable;
-- the existing ownership SAVE persists the repaired projection before SQL grants
-- ownership. Cancelling the stage leaves canonical legacy lanes intact.
local owner = redis.call("GET", "ordinary:ownership:active")
if owner ~= false then return redis.error_reply("detail projection already owned") end
local ok, rows = pcall(cjson.decode, ARGV[1])
if not ok or type(rows) ~= "table" or #rows > 256 then
    return redis.error_reply("invalid cold detail batch")
end
local clock = redis.call("TIME")
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local changes, domains = {}, {}
local function valid_handoff_hash(value)
    if value == "" then return true end
    if type(value) ~= "string" or not string.match(value,"^%-?%d+$") then return false end
    local negative=string.sub(value,1,1)=="-"
    local digits=negative and string.sub(value,2) or value
    if (#digits>1 and string.sub(digits,1,1)=="0") or (negative and digits=="0") then return false end
    local limit=negative and "9223372036854775808" or "9223372036854775807"
    return #digits<#limit or (#digits==#limit and digits<=limit)
end
for _, row in ipairs(rows) do
    if type(row.ID) ~= "string" or #row.ID ~= 36 or type(row.BoardID) ~= "string"
        or (row.Worker ~= "simple" and row.Worker ~= "browser")
        or not retirement_finite(tonumber(row.Due)) or tonumber(row.Due) < 0 then
        return redis.error_reply("invalid canonical detail")
    end
    local key = "scrape:" .. row.ID
    if not retirement_type(key, "hash") then return redis.error_reply("invalid detail hash") end
    local config = redis.call("HMGET", key, "board_id", "source_url", "domain", "scrape_step", "description_r2_hash", "scrape_interval_hours")
    local old_domain = config[3] or row.Domain
    if type(old_domain) ~= "string" or #old_domain < 1 or #old_domain > 253 or string.find(old_domain,"[%c|]") then
        return redis.error_reply("invalid previous detail domain")
    end
    local queued, first, lease, wrong_lane, unsafe = false, nil, false, false, false
    local contexts = {}
    for _, worker in ipairs({"simple", "browser"}) do
        for _, domain in ipairs(old_domain == row.Domain and {row.Domain} or {row.Domain, old_domain}) do
            local task = "scrape|" .. domain .. "|" .. row.ID
            local ft, recurring = "ft_scrapes_" .. worker .. ":" .. domain, "scrapes_" .. worker .. ":" .. domain
            for _, name in ipairs({ft, recurring,"ft_monitors_"..worker..":"..domain,"monitors_"..worker..":"..domain,"inflight:"..worker,"ready:rotation:"..worker}) do
                if not retirement_type(name,"zset") then return redis.error_reply("invalid detail queue type") end
                for _, position in ipairs({0,-1}) do
                    local edge=redis.call("ZRANGE",name,position,position,"WITHSCORES")
                    if #edge>=2 and not retirement_finite(tonumber(edge[2])) then return redis.error_reply("invalid detail queue score") end
                end
            end
            for _, prefix in ipairs({"inflight_tokens:","inflight_strikes:"}) do
                if not retirement_type(prefix..worker,"hash") then return redis.error_reply("invalid detail lease type") end
            end
            for tier=0,2 do
                if not retirement_type("ready:"..worker..":"..tier,"zset") then return redis.error_reply("invalid ready type") end
            end
            if not retirement_type("ratelimit:"..domain,"string") then return redis.error_reply("invalid rate type") end
            local rate=redis.call("GET","ratelimit:"..domain)
            local rotation=redis.call("ZSCORE","ready:rotation:"..worker,domain)
            if rate~=false and not retirement_finite(tonumber(rate)) or rotation~=false and not retirement_finite(tonumber(rotation)) then return redis.error_reply("invalid ready floor") end
            local f,r,l=redis.call("ZSCORE",ft,row.ID),redis.call("ZSCORE",recurring,row.ID),redis.call("ZSCORE","inflight:"..worker,task)
            local token=redis.call("HGET","inflight_tokens:"..worker,task)
            if token~=false or l~=false and (not retirement_finite(tonumber(l)) or tonumber(l)>now) then unsafe=true end
            if f~=false then first=math.min(first or math.huge,tonumber(f)) end
            if f~=false or r~=false or l~=false then
                queued=true
                if worker~=row.Worker or domain~=row.Domain then wrong_lane=true end
            end
            lease=lease or l~=false
            contexts[#contexts+1]={worker=worker,domain=domain,task=task,ft=ft,recurring=recurring,rate=tonumber(rate) or 0,rotation=tonumber(rotation) or 0}
        end
    end
    local identity=config[1]==row.BoardID and config[2]==row.Source and config[3]==row.Domain
    local step=config[4] and config[4]~="" and config[4] or "0"
    local interval=config[6] and config[6]~="" and config[6] or row.Interval
    if not identity then step="0";interval=row.Interval end
    local n=tonumber(step)
    if queued and (not n or n~=math.floor(n) or tostring(n)~=step or n<0 or n>=row.Steps) then return redis.error_reply("invalid current detail step") end
    local hash=row.Hash
    if queued and n>0 then
        -- A pending fallback carries its earlier attempt's hash. R2 drain can
        -- advance the SQL hash after enqueue; that does not invalidate the
        -- independently queued stage. Preserve its exact historical value.
        hash=config[5] or ""
        if not valid_handoff_hash(hash) then return redis.error_reply("invalid detail handoff hash") end
    end
    if queued and (not retirement_finite(tonumber(interval)) or tonumber(interval)<1 or tonumber(interval)>2562047) then return redis.error_reply("invalid detail interval") end
    if queued and (not identity or wrong_lane) then
        if unsafe then return redis.error_reply("live or native detail lease retained") end
        if redis.call("HEXISTS","lightpanda-b0:legacy-guard",row.ID)==1 then return redis.error_reply("B0 detail overlap") end
        if lease and first==nil and row.Hash=="" then first=0 end
        changes[#changes+1]={row=row,contexts=contexts,first=first,step=step,hash=hash,interval=interval,identity_changed=not identity}
    end
end
if ARGV[2]=="inspect" then
    local ids={}
    for _,change in ipairs(changes) do ids[#ids+1]=change.row.ID end
    if #ids==0 then return "[]" end
    return cjson.encode(ids)
end
if ARGV[2]~="apply" then return redis.error_reply("invalid detail repair phase") end
-- No canonical row or receipt changes. Existing first-time priority wins;
-- recurring deadlines are always restored from authoritative SQL.
for _, change in ipairs(changes) do
    local row=change.row
    for _, item in ipairs(change.contexts) do
        redis.call("ZREM",item.ft,row.ID)
        redis.call("ZREM",item.recurring,row.ID)
        redis.call("ZREM","inflight:"..item.worker,item.task)
        domains[item.worker.."|"..item.domain]=item
    end
    redis.call("HSET","scrape:"..row.ID,"board_id",row.BoardID,"source_url",row.Source,"domain",row.Domain,"description_r2_hash",change.hash,"scrape_step",change.step,"scrape_interval_hours",change.interval)
    if change.identity_changed then redis.call("HDEL","scrape:"..row.ID,"egress_host") end
    if change.first~=nil then
        redis.call("ZADD","ft_scrapes_"..row.Worker..":"..row.Domain,change.first,row.ID)
    else
        redis.call("ZADD","scrapes_"..row.Worker..":"..row.Domain,tonumber(row.Due),row.ID)
    end
end
apply_first_retirement({},domains)
return "ok"
