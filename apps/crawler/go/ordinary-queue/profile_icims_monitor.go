package queue

import (
	"encoding/json"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"net/url"
	"strconv"
	"strings"
)

func ICIMSMonitorOptions(config map[string]string) (dom.ICIMSOptions, error) {
	if config["crawler_type"] != "icims" || config["monitor_needs_browser"] != "0" {
		return dom.ICIMSOptions{}, ErrUnsupportedProfile
	}
	mdFields, err := richProfileMetadata(config)
	if err != nil {
		return dom.ICIMSOptions{}, err
	}
	if raw, present := mdFields["cross_locale_dedupe"]; present && string(raw) != "null" {
		fields, err := profileMetadataFields(string(raw), nil)
		if err != nil {
			return dom.ICIMSOptions{}, err
		}
		if aliases, present := fields["title_aliases"]; present {
			if _, err := profileMetadataFields(string(aliases), nil); err != nil {
				return dom.ICIMSOptions{}, err
			}
		}
	}
	var md dom.Object
	d := json.NewDecoder(strings.NewReader(config["metadata"]))
	d.UseNumber()
	if d.Decode(&md) != nil {
		return dom.ICIMSOptions{}, ErrUnsupportedProfile
	}
	o, err := dom.ICIMSOptionsFromMetadata(config["board_url"], md)
	if err != nil {
		return dom.ICIMSOptions{}, ErrUnsupportedProfile
	}
	return o, nil
}
func inspectICIMSMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	o, err := ICIMSMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	return inspectURLOnlyMonitor(boardID, config, md, "icims", "icims.listing-urls/v1", o.Host, dom.ICIMSListingURL(o.Host, 0))
}

// Bind observations to the exact inventory resources derived from this claim.
// A posting URL or unconfigured peer cannot supply reservation/gone evidence.
func ICIMSMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	o, err := ICIMSMonitorOptions(config)
	if err != nil || p.Provider != "icims" || p.Profile != "icims.listing-urls/v1" || p.Endpoint != dom.ICIMSListingURL(o.Host, 0) {
		return false
	}
	if resource == p.Endpoint {
		return true
	}
	u, err := url.Parse(resource)
	if err != nil {
		return false
	}
	if o.JibeURL != "" {
		j, err := url.Parse(o.JibeURL)
		if err != nil {
			return false
		}
		page, err := strconv.Atoi(u.Query().Get("page"))
		return err == nil && page >= 1 && page <= 1000 && resource == j.Scheme+"://"+j.Host+"/api/jobs?page="+strconv.Itoa(page)+"&limit=100"
	}
	hosts := append([]string{o.Host}, o.JobHosts...)
	hosts = append(hosts, o.IDDedupeHosts...)
	if o.PeerHost != "" {
		hosts = append(hosts, o.PeerHost)
	}
	page := 0
	if u.Query().Get("pr") != "" {
		page, err = strconv.Atoi(u.Query().Get("pr"))
		if err != nil || page < 1 || page >= 1000 {
			return false
		}
	}
	for _, host := range hosts {
		if resource == dom.ICIMSListingURL(host, page) {
			return true
		}
	}
	return false
}
func ICIMSMonitorPrimaryGone(config map[string]string, resource string, status int) bool {
	o, err := ICIMSMonitorOptions(config)
	return err == nil && o.JibeURL == "" && (status == 404 || status == 410) && resource == dom.ICIMSListingURL(o.Host, 0)
}
