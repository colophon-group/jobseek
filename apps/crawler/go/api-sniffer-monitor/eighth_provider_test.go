package apisniffer

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestEighthProvidersMatchActualPythonFields(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_eighth_provider_core.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Provider, Name, Source string
			Input                  json.RawMessage
			Expected               []map[string]any
			Error                  bool
		}
	}
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.Cases) != 27 {
		t.Fatal("actual Python fixture unavailable")
	}
	keys := []string{"url", "title", "description", "locations", "employment_type", "job_location_type", "date_posted", "language", "extras", "metadata", "source_identity"}
	for _, c := range fixture.Cases {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			var got []map[string]any
			var failure error
			switch c.Provider {
			case "earcu", "cvwarehouse":
				var body string
				if json.Unmarshal(c.Input, &body) != nil {
					t.Fatal("fixture body")
				}
				if c.Provider == "earcu" {
					got, failure = ParseEArcuFeed([]byte(body), c.Source)
				} else {
					got, failure = ParseCVWarehousePage([]byte(body), c.Source)
				}
			case "woowa":
				d, e := Decode(c.Input)
				if e != nil {
					t.Fatal(e)
				}
				input := d.Value.(map[string]any)
				o, e := EighthProviderOptionsFromMetadata("woowa", c.Source, "{}")
				if e != nil {
					t.Fatal(e)
				}
				fields, e := WoowaJobFields(o, input["listing"].(map[string]any), input["detail"].(map[string]any))
				failure = e
				if e == nil {
					got = []map[string]any{fields}
				}
			}
			if c.Error {
				if failure == nil {
					t.Fatal("Python-rejected inventory accepted")
				}
				return
			}
			if failure != nil || len(got) != len(c.Expected) {
				t.Fatal("Python inventory changed", failure, len(got), len(c.Expected))
			}
			for i, fields := range got {
				normalized := map[string]any{}
				for _, key := range keys {
					normalized[key] = fields[key]
				}
				actual, _ := json.Marshal(normalized)
				expected, _ := json.Marshal(c.Expected[i])
				if !bytes.Equal(actual, expected) {
					t.Fatalf("Python field parity changed:\n%s\n%s", actual, expected)
				}
			}
		})
	}
}

func TestEighthProvidersPreserveAllCurrentRegistryRoutes(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	head := map[string]int{}
	for i, key := range rows[0] {
		head[key] = i
	}
	counts := map[string]int{}
	proxies := 0
	for _, row := range rows[1:] {
		provider := row[head["monitor_type"]]
		if provider != "earcu" && provider != "cvwarehouse" && provider != "woowa" {
			continue
		}
		o, err := EighthProviderOptionsFromMetadata(provider, row[head["board_url"]], row[head["monitor_config"]])
		if err != nil || !o.ResourceMatches(o.ListingURL()) {
			t.Fatal("existing registry route rejected", row[head["board_slug"]], err)
		}
		if o.ResourceMatches("https://other.example/allvacancies/") {
			t.Fatal("cross-portal resource admitted")
		}
		if o.Proxy {
			proxies++
			if o.Profile() != "earcu.proxy-feed-items/v1" {
				t.Fatal("proxy requirement lost")
			}
		}
		if provider == "woowa" && (!o.ResourceMatches(o.WoowaPageURL(0)) || !o.ResourceMatches(o.ListingURL()+"/R-1")) {
			t.Fatal("provider page/detail route rejected")
		}
		counts[provider]++
	}
	if counts["earcu"] != 3 || counts["cvwarehouse"] != 2 || counts["woowa"] != 3 || proxies != 2 {
		t.Fatal("registry coverage changed", counts, proxies)
	}
}

func TestEighthProviderResourceAndControlBoundaries(t *testing.T) {
	for _, raw := range []string{`{"proxy":true}`, `{"render":true}`, `{"actions":[{"action":"remove","selector":"a"}]}`, `{"skip_ssl":true}`, `{"ssl_verify":false}`} {
		if _, err := EighthProviderOptionsFromMetadata("woowa", "https://career.woowahan.com/", raw); err == nil {
			t.Fatal("unsupported transport admitted", raw)
		}
	}
	if _, err := EighthProviderOptionsFromMetadata("earcu", "https://example.com/jobs/vacancy/find/results/", `{"feed_url":"https://example.com/other/allvacancies/"}`); err == nil {
		t.Fatal("configured feed escaped portal candidates")
	}
	o, err := EighthProviderOptionsFromMetadata("woowa", "https://career.woowahan.com/", "{}")
	if err != nil {
		t.Fatal(err)
	}
	for _, source := range []string{o.ListingURL() + "/../private", o.ListingURL() + "/R-1?private=1", "https://user@career.woowahan.com/w1/recruits", "http://career.woowahan.com/w1/recruits"} {
		if o.ResourceMatches(source) {
			t.Fatal("unsafe resource admitted", source)
		}
	}
}

func TestEighthProviderCanonicalIdentitiesStayBounded(t *testing.T) {
	for _, source := range []string{"https://tenant.cvw.io/?job=12", "https://tenant.cvw.io/?job=0"} {
		if !CVWarehousePostingURL(source) {
			t.Fatal("numeric hosted job dropped")
		}
	}
	for _, source := range []string{"https://tenant.cvw.io/", "https://tenant.cvw.io/?job=12&job=13", "https://tenant.cvw.io/?job=12&lang=en", "https://other.example/?job=12", "https://tenant.cvw.io/?job=word", "http://tenant.cvw.io/?job=12", "https://user@tenant.cvw.io/?job=12"} {
		if CVWarehousePostingURL(source) {
			t.Fatal("unproven root URL accepted", source)
		}
	}
	for _, host := range []string{"career.woowahan.com", "career.woowayouths.com", "bmart-career.woowayouths.com"} {
		o, err := EighthProviderOptionsFromMetadata("woowa", "https://"+host+"/", "{}")
		if err != nil {
			t.Fatal(err)
		}
		source := o.Origin + "/recruitment/R-1/detail"
		if o.Variant == "bmart" {
			source = o.Origin + "/recruitment/detail/R-1"
		}
		identity := "woowa:" + o.Variant + ":R-1"
		if !o.WoowaIdentityMatches(source, identity) {
			t.Fatal("provider identity rejected")
		}
		for _, bad := range []string{"woowa:other:R-1", identity + "/extra", "other:" + o.Variant + ":R-1", ""} {
			if o.WoowaIdentityMatches(source, bad) {
				t.Fatal("foreign or ambiguous identity accepted")
			}
		}
		if o.WoowaIdentityMatches(source+"?tracking=1", identity) || o.WoowaIdentityMatches("https://other.example/recruitment/R-1/detail", identity) {
			t.Fatal("identity escaped canonical route")
		}
	}
	o, err := EighthProviderOptionsFromMetadata("cvwarehouse", "https://tenant.cvw.io/", "{}")
	if err != nil || !o.ResourceMatches("https://tenant.cvw.io/english?section=12345678-1234-1234") {
		t.Fatal("public locale route rejected", err)
	}
}
