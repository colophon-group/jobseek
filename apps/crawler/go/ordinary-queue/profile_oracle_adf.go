package queue

import "encoding/json"

// The existing rendered DOM assignment remains the owner. The implementation
// reads ADF's public form protocol instead of executing the two browser actions.
type OracleADFOptions struct {
	MaxItems, MaxScan, TimeoutMS int
}

func OracleADFMonitorOptions(config map[string]string) (OracleADFOptions, error) {
	o := OracleADFOptions{MaxItems: 100, TimeoutMS: 30000}
	fail := func() (OracleADFOptions, error) { return OracleADFOptions{}, ErrUnsupportedProfile }
	if config["crawler_type"] != "dom" || config["monitor_needs_browser"] != "1" {
		return fail()
	}
	md, err := richProfileMetadata(config)
	if err != nil || string(md["render"]) != "true" {
		return fail()
	}
	allowed := map[string]bool{"render": true, "wait": true, "timeout": true, "oracle_adf_job_ids": true, "url_filter": true, "url_allowlist": true, "url_transform": true, "scraper_type": true, "scraper_config": true, "_monitor_config_fingerprint": true, "delist_threshold": true, "drop_threshold": true, "blast_radius_floor": true}
	for k := range md {
		if !allowed[k] && !monitorRuntimeFields[k] {
			return fail()
		}
	}
	if raw := md["wait"]; raw != nil {
		var wait string
		if json.Unmarshal(raw, &wait) != nil || wait != "networkidle" && wait != "domcontentloaded" && wait != "load" {
			return fail()
		}
	}
	if raw := md["timeout"]; raw != nil && (json.Unmarshal(raw, &o.TimeoutMS) != nil || o.TimeoutMS < 1 || o.TimeoutMS > 120000) {
		return fail()
	}
	ids, err := profileMetadataFields(string(md["oracle_adf_job_ids"]), map[string]bool{"max_items": true, "max_scan": true})
	if err != nil || ids == nil {
		return fail()
	}
	if raw := ids["max_items"]; raw != nil && (string(raw) == "null" || json.Unmarshal(raw, &o.MaxItems) != nil) {
		return fail()
	}
	if o.MaxItems < 1 || o.MaxItems > 500 {
		return fail()
	}
	o.MaxScan = o.MaxItems * 10
	if raw := ids["max_scan"]; raw != nil && (string(raw) == "null" || json.Unmarshal(raw, &o.MaxScan) != nil) {
		return fail()
	}
	if o.MaxScan < o.MaxItems || o.MaxScan > 5000 {
		return fail()
	}
	return o, nil
}
