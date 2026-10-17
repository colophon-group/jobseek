package queue

import (
	"encoding/json"
)

// Rich fields, actions, proxy settings and custom request headers keep their
// existing owner until the same held traversal explicitly supports them.
func domHeldPaginationSupported(md map[string]json.RawMessage) bool {
	for _, key := range []string{"actions", "request_headers", "rich_rows", "user_agent", "channel", "persistent_context", "stealth", "proxy", "skip_ssl", "ssl_verify", "headless"} {
		if v := md[key]; v != nil && string(v) != "null" {
			return false
		}
	}
	return true
}

func DOMMonitorUsesHeldPagination(config map[string]string) bool {
	if config["crawler_type"] != "dom" || config["monitor_needs_browser"] != "1" {
		return false
	}
	var md struct {
		Pagination struct {
			Browser bool `json:"browser"`
		} `json:"pagination"`
	}
	return json.Unmarshal([]byte(config["metadata"]), &md) == nil && md.Pagination.Browser
}
