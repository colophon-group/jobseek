-- Compare the freshly derived, approved observation before actual rollback.
-- This wrapper invokes the unchanged pinned lifecycle Lua only after comparison.
local offset = 23 + tonumber(ARGV[22])
local ok, expected = pcall(cjson.decode, ARGV[offset + 1])
if not ok or type(expected) ~= "table" then return redis.error_reply("cold B0 restoration rejected") end
local operation = ARGV[1]
local cas_argument = ARGV[offset + 1]
ARGV[offset + 1] = nil
ARGV[1] = "audit"
local actual = observed_b0()
ARGV[1] = operation
ARGV[offset + 1] = cas_argument
local function same_pairs(raw, object)
    if type(raw) ~= "table" or type(object) ~= "table" or #raw % 2 ~= 0 then return false end
    local count = 0
    for _, _ in pairs(object) do count = count + 1 end
    if count * 2 ~= #raw then return false end
    for index = 1, #raw, 2 do
        if object[raw[index]] ~= raw[index + 1] then return false end
    end
    return true
end
if not same_pairs(actual[1], expected.records) or not same_pairs(actual[2], expected.guards)
    or type(expected.legacy_authority) ~= "table" or #expected.legacy_authority ~= 4 then
    return redis.error_reply("cold B0 restoration rejected")
end
for index = 1, 4 do
    if #actual[3][index] ~= #expected.legacy_authority[index] then
        return redis.error_reply("cold B0 restoration rejected")
    end
    local members = {}
    for _, member in ipairs(expected.legacy_authority[index]) do members[member] = true end
    for _, member in ipairs(actual[3][index]) do
        if not members[member] then return redis.error_reply("cold B0 restoration rejected") end
    end
end
ARGV[offset + 1] = nil
return audited_b0()
