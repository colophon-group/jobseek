-- Validate the entire bounded provider inventory before any effect. This
-- operation is allowed only after supported ownership retirement/quiescence.
local board, domain, worker, now = ARGV[1], ARGV[2], ARGV[3], ARGV[4]
local function typed(key, expected)
 local t = redis.call("TYPE", key)["ok"]
 return t == "none" or t == expected
end
for _,key in ipairs({"ordinary:ownership:active", "lightpanda-b0:producer-owner"}) do
 if redis.call("EXISTS",key) ~= 0 then return redis.error_reply("worker route ownership is still active") end
end
if not typed("lightpanda-b0:legacy-guard","hash") or redis.call("HLEN","lightpanda-b0:legacy-guard") ~= 0 then
 return redis.error_reply("worker route legacy guard is still active")
end
if not typed("ratelimit:"..domain,"string") then return redis.error_reply("worker route rate limit is corrupt") end
local rate=redis.call("GET","ratelimit:"..domain)
if rate and not tonumber(rate) then return redis.error_reply("worker route rate limit is invalid") end
for _,lane in ipairs({"simple","browser"}) do
 for _,key in ipairs({"inflight:"..lane,"ready:rotation:"..lane,
  "ft_monitors_"..lane..":"..domain,"monitors_"..lane..":"..domain,
  "ft_scrapes_"..lane..":"..domain,"scrapes_"..lane..":"..domain,
  "ready:"..lane..":0","ready:"..lane..":1","ready:"..lane..":2"}) do
  if not typed(key,"zset") then return redis.error_reply("worker route queue type is corrupt") end
  local head=redis.call("ZRANGE",key,0,0,"WITHSCORES")
  if #head>0 and not tonumber(head[2]) then return redis.error_reply("worker route queue deadline is invalid") end
 end
 for _,key in ipairs({"inflight_tokens:"..lane,"monitor_repair_due:"..lane}) do
  if not typed(key,"hash") then return redis.error_reply("worker route queue token type is corrupt") end
 end
end
local selected={}
for offset=5,#ARGV,5 do
 local id,url,hash,sqlscore,first=ARGV[offset],ARGV[offset+1],ARGV[offset+2],ARGV[offset+3],ARGV[offset+4]=="true"
 local key="scrape:"..id
 if not typed(key,"hash") then return redis.error_reply("worker route scrape type is corrupt") end
 if redis.call("EXISTS",key)==1 then
  if redis.call("HGET",key,"board_id")~=board or redis.call("HGET",key,"source_url")~=url
   or redis.call("HGET",key,"domain")~=domain or redis.call("HGET",key,"scrape_step")~="0"
   or redis.call("HGET",key,"description_r2_hash")~=hash then
   return redis.error_reply("worker route scrape identity or fallback step mismatch")
  end
 end
 local ft,rec=nil,nil
 for _,lane in ipairs({"simple","browser"}) do
  local member="scrape|"..domain.."|"..id
  if redis.call("ZSCORE","inflight:"..lane,member) or redis.call("HEXISTS","inflight_tokens:"..lane,member)==1 then
   return redis.error_reply("worker route scrape is inflight")
  end
  local f=redis.call("ZSCORE","ft_scrapes_"..lane..":"..domain,id)
  local r=redis.call("ZSCORE","scrapes_"..lane..":"..domain,id)
  if f and (not ft or tonumber(f)<tonumber(ft)) then ft=f end
  if r and (not rec or tonumber(r)<tonumber(rec)) then rec=r end
 end
 selected[#selected+1]={id=id,url=url,hash=hash,first=ft~=nil or first,score=ft or (first and "0") or rec or sqlscore}
end
local function repair(enqueue, refresh)
 local changed=0
 for _,row in ipairs(selected) do
  local desired=(row.first and "ft_scrapes_" or "scrapes_")..worker..":"..domain
  local before=redis.call("ZSCORE",desired,row.id)
  local entries=0
  for _,lane in ipairs({"simple","browser"}) do
   for _,prefix in ipairs({"ft_scrapes_","scrapes_"}) do
    entries=entries+redis.call("ZREM",prefix..lane..":"..domain,row.id)
   end
  end
  if entries~=1 or before~=row.score then changed=changed+1 end
  enqueue({worker,domain,row.id,row.score,"scrape",row.first and "1" or "0",now,
   "source_url",row.url,"board_id",board,"description_r2_hash",row.hash,"scrape_step","0"})
 end
 refresh(worker=="simple" and "browser" or "simple")
 return changed
end
