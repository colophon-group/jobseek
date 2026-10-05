package queue

import (
	"encoding/json"
	"regexp"
)

var jobylonID = regexp.MustCompile(`^[0-9]{1,20}$`)
var jobylonEmbedURL = regexp.MustCompile(`(?i)cdn\.jobylon\.com/jobs/(companies|company-groups)/([0-9]+)/embed`)

// Jobylon gives an explicit company group precedence over a company. The
// original metadata stays in the ownership hash, including ignored aliases.
func inspectJobylonMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	var company, group string
	for key, target := range map[string]*string{"company_id": &company, "company_group_id": &group} {
		raw := md[key]
		if len(raw) == 0 || string(raw) == "null" || string(raw) == `""` || string(raw) == "0" || string(raw) == "false" {
			continue
		}
		if json.Unmarshal(raw, target) != nil {
			*target = string(raw)
		}
		if !jobylonID.MatchString(*target) {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
	}
	kind, id := "companies", company
	if group != "" {
		kind, id = "company-groups", group
	} else if company == "" {
		if m := jobylonEmbedURL.FindStringSubmatch(config["board_url"]); m != nil {
			id = m[2]
			if m[1] == "company-groups" {
				kind = m[1]
			}
		}
	}
	if !jobylonID.MatchString(id) {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := oracleMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return inspectURLOnlyMonitor(boardID, config, md, "jobylon", "jobylon.embed-items/v1", kind+"/"+id, "https://cdn.jobylon.com/jobs/"+kind+"/"+id+"/embed/v2/")
}
