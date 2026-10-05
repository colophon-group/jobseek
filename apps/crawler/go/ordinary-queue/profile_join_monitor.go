package queue

import (
	"encoding/json"
	"net/url"
	"strconv"
	"strings"

	join "github.com/colophon-group/jobseek/apps/crawler/go/join-monitor"
)

func inspectJoinMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	u, parseErr := url.Parse(config["board_url"])
	if parseErr != nil || u.RawPath != "" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	var slug string
	if raw, ok := md["slug"]; ok && string(raw) != "null" && json.Unmarshal(raw, &slug) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if slug == "" {
		slug = strings.TrimPrefix(strings.TrimSuffix(u.Path, "/"), "/companies/")
	}
	if join.ValidateBoard(config["board_url"], slug) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, "join", "join.nextdata-urls/v1", slug, config["board_url"])
}

// Bind publisher evidence to an actual bounded page of this claimed board.
// Redirect targets remain observations from the verified transport.
func JoinMonitorResourceMatches(p GreenhouseMonitorProfile, resource string) bool {
	if p.Profile != "join.nextdata-urls/v1" {
		return false
	}
	if resource == p.Endpoint {
		return true
	}
	u, err := url.Parse(resource)
	if err != nil || u.Fragment != "" || u.RawPath != "" {
		return false
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(q) != 1 || len(q["page"]) != 1 {
		return false
	}
	n, err := strconv.Atoi(q.Get("page"))
	expected, pageErr := join.PageURL(p.Endpoint, n)
	return err == nil && pageErr == nil && expected == resource
}
