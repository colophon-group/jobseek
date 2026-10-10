package queue

import (
	"encoding/csv"
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"

	"os"
	"testing"
)

func TestSecondaryProvidersCurrentRegistryConfigurationCoverage(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil || len(rows) < 2 {
		t.Fatal("registry unavailable")
	}
	headers := map[string]int{}
	for i, key := range rows[0] {
		headers[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[headers["monitor_type"]]
		if RemainingHTTPProvider(provider) || provider == "seek" || provider == "avature" || provider == "unifr" || provider == "notion" || provider == "umantis" || TenthProvider(provider) || SmallProvider(provider) || FinalHTTPProvider(provider) || provider == "recruiterbox" || provider == "jobs_ch" || provider == "dayforce" || provider == "adp" || provider == "cornerstone" || provider == "paylocity" || !SecondaryProvider(provider) || provider == "comeet" || provider == "jobvite" || provider == "paycom" || provider == "rippling" {
			continue
		}
		metadata := map[string]any{}
		if raw := row[headers["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &metadata) != nil {
			t.Fatal("invalid provider configuration", row[headers["board_slug"]])
		}
		if scraper := row[headers["scraper_type"]]; scraper != "" {
			metadata["scraper_type"] = scraper
		}
		if scraper := row[headers["scraper_config"]]; scraper != "" {
			var value any
			if json.Unmarshal([]byte(scraper), &value) != nil {
				t.Fatal("invalid detail configuration")
			}
			metadata["scraper_config"] = value
		}
		encoded, e := json.Marshal(metadata)
		if e != nil {
			t.Fatal("configuration cannot be serialized")
		}
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["metadata"] = provider, row[headers["board_url"]], string(encoded)
		expectedWorker := Simple
		if NativeBrowserProvider(provider) {
			config["monitor_needs_browser"] = "1"
			expectedWorker = Browser
		}
		profile, e := InspectRichMonitor(profileBoardID, config)
		if metadata["proxy"] == true {
			if provider == "earcu" || provider == "computrabajo" || provider == "practicematch" {
				want := "earcu.proxy-feed-items/v1"
				if provider == "computrabajo" {
					want = "computrabajo.proxy-listing-urls/v1"
				}
				if provider == "practicematch" {
					want = "practicematch.proxy-listing-urls/v1"
				}
				if e != nil || profile.Profile != want || !ProfileRequiresProxy(profile.Profile) {
					t.Fatal("required provider proxy authority lost", provider)
				}
			} else if e == nil {
				t.Fatal("configured proxy acquired direct monitor authority")
			}
			counts[provider+"_proxy_preserved"]++
			continue
		}
		if e != nil || profile.Provider != provider || MonitorWorker(profile) != expectedWorker {
			t.Fatal("configured direct provider unsupported", row[headers["board_slug"]])
		}
		counts[provider]++
		scraper, _ := metadata["scraper_type"].(string)
		if scraper == "skip" {
			continue
		}
		if provider == "linkedin" {
			// Guest listings deliberately retain the existing scheduled detail
			// scraper until its independent ownership profile is qualified.
			if scraper != "linkedin" {
				t.Fatal("LinkedIn scheduled detail delegation changed", scraper)
			}
			if _, err := secondaryMonitorEnrichment(config); err != nil {
				t.Fatal("LinkedIn scheduled field delegation unsupported", err)
			}
			counts[provider+"_delegated_detail"]++
			continue
		}
		var detail WorkdayDetailProfile
		switch provider {
		case "jobstreet":
			detail, e = inspectDetailOwnership(profileBoardID, config)
		case "pageup", "infoniqa":
			o, err := api.PortalHTTPProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			source := o.PageUp.ListingURL() + "/job/123/fixture"
			if provider == "infoniqa" {
				source = o.Origin + "/hcm/jobexchange/showJobOfferDetail.do?jobOfferId=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&j=jobexchange&organizationUnitId="
			}
			detail, e = InspectDOMDetail(profileBoardID, config, source, Simple)
		case "taleo":
			o, err := api.TaleoOptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			if scraper == "dom" {
				detail, e = InspectDOMDetail(profileBoardID, config, o.Board.JobURL(123), Simple)
			} else {
				detail, e = InspectJSONLDDetail(profileBoardID, config, o.Board.JobURL(123), Simple)
			}
		case "talentbrew":
			detail, e = inspectDetailOwnership(profileBoardID, config)
		case "computrabajo", "ycombinator":
			o, err := api.NinthProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			source := o.Origin + "/Detail/123"
			if provider == "ycombinator" {
				source = o.ListingURL() + "/A1-engineer"
			} else if o.Variant == "employer" {
				source = o.Origin + "/ofertas-de-trabajo/oferta-de-trabajo-de-engineer-11111111111111111111111111111111"
			}
			if scraper == "dom" {
				detail, e = InspectDOMDetail(profileBoardID, config, source, Simple)
			} else {
				detail, e = InspectJSONLDDetail(profileBoardID, config, source, Simple)
			}
		case "hrmos":
			o, err := api.HRMOSOptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			detail, e = InspectJSONLDDetail(profileBoardID, config, o.ListingURL(1)+"/123", Simple)
		case "softgarden":
			o, err := api.SoftgardenOptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			detail, e = InspectJSONLDDetail(profileBoardID, config, o.JobURL("123"), Simple)
		case "ukg":
			o, err := api.UKGOptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			detail, e = InspectEmbeddedDetail(profileBoardID, config, o.JobURL("22222222-2222-2222-2222-222222222222"), Simple)
		case "bamboohr":
			o, err := api.BambooHROptionsFromMetadata(config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			detail, e = InspectHTTPAPIDetail(profileBoardID, config, o.JobURL("123"), Simple)
		}
		if e != nil || detail.EffectiveBoardSHA256 != profile.EffectiveConfigSHA256 {
			t.Fatal("required detail binding unsupported", row[headers["board_slug"]], e)
		}
		counts[provider+"_detail"]++
	}
	if counts["softgarden"] == 0 || counts["ukg"] == 0 || counts["bamboohr"] == 0 || counts["recruiter_co_kr"] == 0 {
		t.Fatal("registry coverage fixture empty")
	}
	t.Logf("configuration eligibility only (no production authority): %v", counts)
}
