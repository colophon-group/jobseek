
for index, key in ipairs(KEYS) do
    if redis.call("EXISTS", key) == 1 then
        local offset = (index - 1) * 2
        local expected_board = ARGV[offset + 1]
        local canonical_url = ARGV[offset + 2]
        local actual_board = redis.call("HGET", key, "board_id")
        local actual_url = redis.call("HGET", key, "source_url")
        local prefix = canonical_url .. "/"
        local locale_suffix = actual_url and string.sub(actual_url, #prefix + 1) or ""
        local is_locale_alias = actual_url
            and string.sub(actual_url, 1, #prefix) == prefix
            and string.sub(locale_suffix, 1, 1) ~= "0"
            and string.match(locale_suffix, "^[0-9]+$") ~= nil

        if actual_board ~= expected_board then
            return redis.error_reply("Umantis scrape hash board mismatch at batch index " .. index)
        end
        if actual_url ~= canonical_url and not is_locale_alias then
            return redis.error_reply("Umantis scrape hash URL mismatch at batch index " .. index)
        end
    end
end

local changed = 0
for index, key in ipairs(KEYS) do
    if redis.call("EXISTS", key) == 1 then
        local canonical_url = ARGV[(index - 1) * 2 + 2]
        if redis.call("HGET", key, "source_url") ~= canonical_url then
            redis.call("HSET", key, "source_url", canonical_url)
            changed = changed + 1
        end
    end
end
return changed
