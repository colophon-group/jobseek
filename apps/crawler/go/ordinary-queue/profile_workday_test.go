package queue

import (
	"encoding/json"
	"testing"
)

func TestWorkdayMonitorProfileBindsAllInventoryOptionsAndIndependentDetailFlags(t *testing.T) {
	config := profileConfig()
	config["crawler_type"] = "workday"
	config["board_url"] = "https://fixture.wd1.myworkdayjobs.com/en-US/Careers"
	config["metadata"] = `{"all_sites":false,"scraper_type":"workday","search_text":"Brand"}`
	config["scraper_needs_browser"] = "1"
	p, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || p.Provider != "workday" || p.Profile != "workday.cxs-urls/v1" || p.Endpoint != "https://fixture.wd1.myworkdayjobs.com/wday/cxs/fixture/Careers/jobs" {
		t.Fatalf("URL-only profile: %+v / %v", p, err)
	}
	config["metadata"] = `{"all_sites":false,"scraper_type":"workday","search_text":"Changed"}`
	changed, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || changed.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("inventory filter escaped immutable binding", err)
	}
	config["metadata"] = `{"search_text":"Brand"}`
	if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
		t.Fatal("search over default multi-site admitted")
	}
	config["metadata"] = `{"ssl_verify":false}`
	if _, err := InspectRichMonitor(profileBoardID, config); err != nil {
		t.Fatal("explicit TLS setting rejected by monitor", err)
	}
	if skip, err := MonitorSkipsSSL(config); err != nil || !skip {
		t.Fatal("explicit Workday monitor exception not selected", err)
	}
	config["metadata"] = `{}`
	config["monitor_needs_browser"] = "1"
	if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
		t.Fatal("browser monitor admitted to CXS HTTP profile")
	}
}

func TestWorkdayPythonDelistThresholdAndDropSettingSemantics(t *testing.T) {
	for _, tc := range []struct {
		raw  any
		want int
	}{{nil, 4}, {true, 4}, {json.Number("0.9"), 4}, {json.Number("4.7"), 4}, {json.Number("2"), 2}, {"3", 3}, {"3.5", 4}, {"bad", 4}, {json.Number("-2"), 4}} {
		got, err := workdayDelistThreshold(tc.raw)
		if err != nil || got != tc.want {
			t.Fatalf("threshold %v: %d / %v", tc.raw, got, err)
		}
	}
	for _, raw := range []any{json.Number("0"), "0", false} {
		if v, err := workdayLifecycleSetting(raw, 0.3); err != nil || v != 0 {
			t.Fatal("explicit zero drop setting lost", err)
		}
	}
}

func TestWorkdayCanonicalURLTransformDoesNotChangeFetchAuthority(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["board_url"] = "workday", "https://fixture.wd1.myworkdayjobs.com/Careers"
	c["metadata"] = `{"url_transform":{"find":"/Engineer_R([0-9]+)$","replace":"/role/\\1"},"scraper_type":"embedded","scraper_config":{"variable":"job","fields":{"title":"title"}}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	rules, ruleErr := FeedMonitorURLRules(c)
	if err != nil || ruleErr != nil || p.Endpoint != "https://fixture.wd1.myworkdayjobs.com/wday/cxs/fixture/Careers/jobs" || rules.Rewrite("https://fixture.wd1.myworkdayjobs.com/Careers/job/Engineer_R123") != "https://fixture.wd1.myworkdayjobs.com/Careers/job/role/123" {
		t.Fatal("canonical rewrite changed request authority or replacement semantics", err, ruleErr)
	}
	c["metadata"] = `{"scraper_config":{"fields":{"title":"title"},"variable":"job"},"scraper_type":"embedded","url_transform":{"replace":"/role/\\1","find":"/Engineer_R([0-9]+)$"}}`
	reordered, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || reordered.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("nested SQL/cache key order changed transformed Workday authority", err)
	}
	c["metadata"] = `{"url_transform":{"find":"/Engineer_R([0-9]+)$","replace":"/changed/\\1"},"scraper_type":"embedded"}`
	changed, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || changed.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("URL transform escaped immutable binding", err)
	}
	c["metadata"] = `{"url_transform":{"find":"[","replace":"bad"}}`
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("invalid transform admitted before fetch")
	}
}

func TestWorkdayURLFilterAdmissionAndBinding(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["board_url"] = "workday", "https://melcoresorts.wd3.myworkdayjobs.com/en-US/career"
	c["metadata"] = `{"company":"melcoresorts","wd_instance":"wd3","site":"career","scraper_type":"workday","url_filter":{"exclude":"_REQ(?:11520|15020)(?:$|[?#])"}}`
	profile, err := InspectRichMonitor(profileBoardID, c)
	rules, ruleErr := FeedMonitorURLRules(c)
	if err != nil || ruleErr != nil || profile.Endpoint != "https://melcoresorts.wd3.myworkdayjobs.com/wday/cxs/melcoresorts/career/jobs" {
		t.Fatal("valid Workday URL exclusion lost native monitor admission", err, ruleErr)
	}
	if err := ValidateNativeBoard(profileBoardID, c); err != nil {
		t.Fatal("valid Workday URL exclusion failed complete native admission", err)
	}
	for _, tc := range []struct {
		url  string
		keep bool
	}{
		{"https://melcoresorts.wd3.myworkdayjobs.com/career/job/Driver_REQ11520", false},
		{"https://melcoresorts.wd3.myworkdayjobs.com/career/job/Host_REQ15020?source=site", false},
		{"https://melcoresorts.wd3.myworkdayjobs.com/career/job/Manager_REQ15142", true},
	} {
		keep, err := rules.FilterURL(tc.url)
		if err != nil || keep != tc.keep {
			t.Fatalf("Workday URL exclusion for %s: keep=%t err=%v", tc.url, keep, err)
		}
	}
	c["metadata"] = `{"company":"melcoresorts","wd_instance":"wd3","site":"career","scraper_type":"workday","url_filter":{"exclude":"_REQ15020$"}}`
	changed, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || changed.EffectiveConfigSHA256 == profile.EffectiveConfigSHA256 || changed.Endpoint != profile.Endpoint {
		t.Fatal("Workday URL exclusion escaped immutable binding or changed fetch authority", err)
	}
	for _, metadata := range []string{
		`{"company":"melcoresorts","wd_instance":"wd3","site":"career","scraper_type":"workday","url_filter":{"exclude":"["}}`,
		`{"company":"melcoresorts","wd_instance":"wd3","site":"career","scraper_type":"workday","unrelated_option":true}`,
	} {
		c["metadata"] = metadata
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("invalid Workday metadata admitted", metadata)
		}
	}
}
