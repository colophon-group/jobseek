package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"

	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

// Workday ownership covers URL-only monitors. Separately scheduled details
// retain their configured simple/browser queue and canonical posting owner.
// This profile grants no detail claims, even when their scraper is Workday.
func inspectWorkdayMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["scraper_needs_browser"] != "0" && config["scraper_needs_browser"] != "1" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	var metadata map[string]any
	if json.Unmarshal([]byte(config["metadata"]), &metadata) != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	c, err := workday.ParseInventoryConfig(config["board_url"], metadata)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if raw, ok := md["ssl_verify"]; ok && string(raw) != "true" && string(raw) != "null" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	// Validate the common monitor authority/interval/transport contract using
	// a detached copy. Detail flags/options remain in the immutable binding;
	// they do not require a browser for Workday's CXS list request.
	validation := cloneConfig(config)
	validation["crawler_type"] = "greenhouse"
	validation["scraper_needs_browser"] = "0"
	validationMD := map[string]json.RawMessage{"token": json.RawMessage(`"workday"`), "scraper_type": json.RawMessage(`"skip"`)}
	for key, value := range md {
		if monitorRuntimeFields[key] || key == "_monitor_config_fingerprint" {
			validationMD[key] = value
		}
	}
	body, err := json.Marshal(validationMD)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	validation["metadata"] = string(body)
	profile, err := InspectGreenhouseMonitor(boardID, validation)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	stable, err := stableGreenhouseConfig(config, md)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	body, err = json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	digest := sha256.Sum256(body)
	profile.EffectiveConfigSHA256 = hex.EncodeToString(digest[:])
	profile.SnapshotSHA256 = configDigest(config)
	profile.Provider = "workday"
	profile.Profile = "workday.cxs-urls/v1"
	profile.Token = c.Site.Company
	profile.Endpoint = fmt.Sprintf("https://%s.%s.myworkdayjobs.com/wday/cxs/%s/%s/jobs", c.Site.Company, c.Site.Instance, c.Site.Company, c.Site.Name)
	return profile, nil
}

func workdayDelistThreshold(raw any) (int, error) {
	value := float64(4)
	switch v := raw.(type) {
	case json.Number:
		n, err := v.Float64()
		if err != nil {
			return 4, nil
		}
		value = math.Trunc(n)
	case string:
		n, err := strconv.ParseInt(strings.TrimSpace(v), 10, 64)
		if err != nil {
			return 4, nil
		}
		value = float64(n)
	}
	if value < 1 {
		return 4, nil
	}
	if math.IsNaN(value) || math.IsInf(value, 0) || value > math.MaxInt32 {
		return 0, ErrConfiguration
	}
	return int(value), nil
}

func workdayLifecycleSetting(raw any, fallback float64) (float64, error) {
	if raw == nil {
		return fallback, nil
	}
	var n float64
	var err error
	switch v := raw.(type) {
	case json.Number:
		n, err = v.Float64()
	case string:
		n, err = strconv.ParseFloat(strings.TrimSpace(v), 64)
	case bool:
		if v {
			n = 1
		}
	default:
		return 0, ErrConfiguration
	}
	if err != nil || math.IsNaN(n) || math.IsInf(n, 0) {
		return 0, ErrConfiguration
	}
	return n, nil
}
