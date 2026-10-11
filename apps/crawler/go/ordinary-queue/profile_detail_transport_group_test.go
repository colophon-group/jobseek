package queue

import (
	"encoding/json"
	"testing"
)

func TestWorkdayProxyDetailTransportIsSeparateAndBound(t *testing.T) {
	c := workdayDetailConfig()
	source := "https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001"
	direct, err := InspectWorkdayDetail(profileBoardID, c, source, Simple)
	if err != nil {
		t.Fatal(err)
	}
	c["metadata"] = `{"scraper_type":"workday","scraper_config":{"proxy":true}}`
	proxied, err := InspectWorkdayDetail(profileBoardID, c, source, Simple)
	if err != nil || proxied.Profile != "workday.proxy-cxs-detail/v1" || !ProfileRequiresProxy(proxied.Profile) || proxied.SkipSSL || proxied.EffectiveBoardSHA256 == direct.EffectiveBoardSHA256 {
		t.Fatal("proxy authority or canonical binding lost", err)
	}
	monitor, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || ProfileRequiresProxy(monitor.Profile) {
		t.Fatal("detail option changed monitor transport", err)
	}
	for _, metadata := range []string{
		`{"scraper_type":"workday","ssl_verify":false,"scraper_config":{"proxy":true}}`,
		`{"scraper_type":"workday","scraper_config":{"proxy":true,"render":true}}`,
		`{"scraper_type":"workday","scraper_config":{"proxy":true,"proxy":false}}`,
		`{"scraper_type":"workday","scraper_config":{"proxy":1}}`,
	} {
		c["metadata"] = metadata
		if _, err := InspectWorkdayDetail(profileBoardID, c, source, Simple); err == nil {
			t.Fatal("unqualified transport mixture admitted")
		}
	}
}

func TestDOMAndJSONLDExplicitDetailTLSExceptionIsBound(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld"} {
		t.Run(scraper, func(t *testing.T) {
			c := domDetailConfig()
			options := map[string]any{}
			if scraper == "dom" {
				options["steps"] = []any{map[string]any{"tag": "h1", "field": "title"}}
			}
			inspect := InspectDOMDetail
			if scraper == "json-ld" {
				inspect = InspectJSONLDDetail
			}
			set := func() {
				body, err := json.Marshal(map[string]any{"scraper_type": scraper, "scraper_config": options})
				if err != nil {
					t.Fatal(err)
				}
				c["metadata"] = string(body)
			}
			set()
			direct, err := inspect(jsonldBoardID, c, "https://jobs.example.net/42", Simple)
			if err != nil || direct.SkipSSL {
				t.Fatal("verified TLS default changed", err)
			}
			options["skip_ssl"] = true
			set()
			exception, err := inspect(jsonldBoardID, c, direct.SourceURL, Simple)
			if err != nil || !exception.SkipSSL || exception.EffectiveBoardSHA256 == direct.EffectiveBoardSHA256 || exception.Profile != direct.Profile {
				t.Fatal("explicit exception escaped canonical transport binding", err)
			}
			if exception.DOMConfig["skip_ssl"] != nil || exception.JSONLDConfig["skip_ssl"] != nil {
				t.Fatal("transport flag leaked into content parser")
			}
			for _, value := range []any{"true", 1, []any{true}} {
				options["skip_ssl"] = value
				set()
				if _, err := inspect(jsonldBoardID, c, direct.SourceURL, Simple); err == nil {
					t.Fatal("malformed exception admitted")
				}
			}
			options["skip_ssl"], options["proxy"] = true, true
			set()
			if _, err := inspect(jsonldBoardID, c, direct.SourceURL, Simple); err == nil {
				t.Fatal("unqualified proxy TLS exception admitted")
			}
			delete(options, "proxy")
			for _, value := range []any{false, nil} {
				options["skip_ssl"] = value
				set()
				p, err := inspect(jsonldBoardID, c, direct.SourceURL, Simple)
				if err != nil || p.SkipSSL {
					t.Fatal("disabled TLS exception changed transport", err)
				}
			}
		})
	}
}
