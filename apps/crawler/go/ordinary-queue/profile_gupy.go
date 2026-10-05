package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var gupyPath = regexp.MustCompile(`^(?:/jobs/[1-9]\p{Nd}{0,19}/?)?/?$`)

func inspectGupyMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	// Gupy selects a valid explicit tenant first, then falls back to its URL.
	// Unlike JazzHR, its existing adapter permits a differing configured tenant.
	var text string
	_ = json.Unmarshal(md["tenant"], &text)
	tenant := normalizeJazzHRTenant(text)
	if tenant == "" {
		u, err := url.Parse(config["board_url"])
		if err == nil && u.Scheme == "https" && u.User == nil && (u.RawPath == "" || u.RawPath == u.Path) && (u.Port() == "" || u.Port() == "443") && gupyPath.MatchString(u.Path) {
			host := strings.TrimRight(strings.ToLower(u.Hostname()), ".")
			if strings.HasSuffix(host, ".gupy.io") {
				queryOK := u.RawQuery == ""
				if u.RawQuery != "" && strings.HasPrefix(u.Path, "/jobs/") {
					q, err := url.ParseQuery(u.RawQuery)
					queryOK = err == nil && len(q) == 1 && len(q["jobBoardSource"]) == 1 && q.Get("jobBoardSource") == "gupy_public_page"
				}
				if queryOK {
					tenant = normalizeJazzHRTenant(strings.TrimSuffix(host, ".gupy.io"))
				}
			}
		}
	}
	if tenant == "" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, "gupy", "gupy.nextdata-urls/v1", tenant, "https://"+tenant+".gupy.io/")
}
