package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var hrManagerCustomer = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)
var hrManagerPositionID = regexp.MustCompile(`^[1-9][0-9]*$`)

type HRManagerConfig struct{ Board, Feed, Customer string }

func HRManagerOptions(config map[string]string) (HRManagerConfig, error) {
	md, err := richProfileMetadata(config)
	if err != nil {
		return HRManagerConfig{}, err
	}
	u, err := url.Parse(config["board_url"])
	if err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Fragment != "" || (u.Port() != "" && u.Port() != "443") || strings.TrimRight(strings.ToLower(u.Hostname()), ".") != "candidate.hr-manager.net" || strings.ToLower(u.Path) != "/vacancies/list.aspx" {
		return HRManagerConfig{}, ErrUnsupportedProfile
	}
	query, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(query) != 1 {
		return HRManagerConfig{}, ErrUnsupportedProfile
	}
	customer := ""
	for key, values := range query {
		if strings.ToLower(key) != "customer" || len(values) != 1 {
			return HRManagerConfig{}, ErrUnsupportedProfile
		}
		customer = strings.ToLower(strings.TrimSpace(values[0]))
	}
	if !hrManagerCustomer.MatchString(customer) {
		return HRManagerConfig{}, ErrUnsupportedProfile
	}
	if raw := md["customer"]; raw != nil {
		var configured string
		if json.Unmarshal(raw, &configured) != nil || configured != customer {
			return HRManagerConfig{}, ErrUnsupportedProfile
		}
	}
	feed := "https://api.hr-manager.net/JobPortal.svc/" + customer + "/PositionList/rss/?protype=RecruitmentProject&incads=true"
	if raw := md["feed_url"]; raw != nil {
		var configured string
		if json.Unmarshal(raw, &configured) != nil || configured != feed {
			return HRManagerConfig{}, ErrUnsupportedProfile
		}
	}
	return HRManagerConfig{config["board_url"], feed, customer}, nil
}

func (c HRManagerConfig) ResourceMatches(raw string) bool { return raw == c.Board || raw == c.Feed }

func validHRManagerRSSIdentity(config map[string]string, identity string) bool {
	c, err := HRManagerOptions(config)
	if err != nil {
		return false
	}
	prefix := "hr_manager:" + c.Customer + ":"
	return strings.HasPrefix(identity, prefix) && hrManagerPositionID.MatchString(strings.TrimPrefix(identity, prefix))
}
