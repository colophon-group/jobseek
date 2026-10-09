package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestSmallProvidersCurrentRegistryAndTransportAuthority(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	h := map[string]int{}
	for i, k := range rows[0] {
		h[k] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[h["monitor_type"]]
		if !SmallProvider(provider) {
			continue
		}
		md := map[string]any{}
		raw := row[h["monitor_config"]]
		if raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("metadata")
		}
		md["scraper_type"] = row[h["scraper_type"]]
		if raw = row[h["scraper_config"]]; raw != "" {
			var value any
			if json.Unmarshal([]byte(raw), &value) != nil {
				t.Fatal("detail metadata")
			}
			md["scraper_config"] = value
		}
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = provider, row[h["board_url"]], string(body)
		p, e := InspectRichMonitor(profileBoardID, c)
		if e != nil || p.Provider != provider || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) || !SecondaryMonitorResourceMatches(p, c, p.Endpoint) || SecondaryMonitorResourceMatches(p, c, "https://foreign.example/private") {
			t.Fatal("registry binding lost", row[h["board_slug"]], e)
		}
		for _, bad := range []string{"{\"render\":true}", "{\"unknown\":true}", "{\"proxy\":\"true\"}"} {
			c["metadata"] = bad
			if _, e = InspectRichMonitor(profileBoardID, c); e == nil {
				t.Fatal("unsupported config accepted", provider)
			}
		}
		counts[provider]++
	}
	if !reflect.DeepEqual(counts, map[string]int{"jobbank104": 4, "cnstaff": 1, "seamlesshiring": 1, "jarvi": 1, "job51": 2}) {
		t.Fatal("registry count changed", counts)
	}
}

func TestJarviAndJob51ProviderResourceAndIdentityBindings(t *testing.T) {
	jarvi, err := api.SmallProviderOptionsFromMetadata("jarvi", "https://fixture.invalid/careers", `{"public_api_key":"public_fixture_key","currency":"CHF"}`)
	if err != nil || !jarvi.ResourceMatches(api.JarviOffersURL) {
		t.Fatal("public Jarvi SDK binding unavailable", err)
	}
	for _, resource := range []string{api.JarviOffersURL + "&limit=1", "https://foreign.example/offers", "https://fixture.invalid/careers", "https://functions.prod.jarvi.tech/v1/public-api/rest/v2/offers?limit=1"} {
		if jarvi.ResourceMatches(resource) {
			t.Fatal("Jarvi SDK key granted an unbound resource")
		}
	}
	config := map[string]string{"board_url": "https://campus.51job.com/fixture/job.html", "metadata": `{"ctmid":12345,"scraper_type":"skip"}`}
	job51, err := api.SmallProviderOptionsFromMetadata("job51", config["board_url"], config["metadata"])
	if err != nil {
		t.Fatal(err)
	}
	list, _ := api.Job51ListRequest(12345, 1)
	detail, _ := api.Job51DetailRequest("123")
	foreign, _ := api.Job51ListRequest(12346, 1)
	for _, request := range []api.Request{list, detail} {
		if !job51.ResourceMatches(request.URL) {
			t.Fatal("signed public protocol rejected")
		}
	}
	for _, resource := range []string{foreign.URL, list.URL + "&key=1", list.URL + "#fragment", "https://foreign.example/job_detail.php", "https://jobs.51job.com/all/123.html"} {
		if job51.ResourceMatches(resource) {
			t.Fatal("unbound public protocol admitted")
		}
	}
	source := "https://jobs.51job.com/all/123.html"
	if !validJob51SourceIdentity(config, source, "job51:12345:123") {
		t.Fatal("canonical provider identity rejected")
	}
	for _, identity := range []string{"job51:12346:123", "job51:12345:124", "job51:012345:123", "job51:12345:bad", "job51:12345:123:extra", source, ""} {
		if validJob51SourceIdentity(config, source, identity) {
			t.Fatal("foreign or malformed provider identity admitted")
		}
	}
	if validJob51SourceIdentity(config, source+"?tracking=1", "job51:12345:123") {
		t.Fatal("noncanonical source acquired identity")
	}
	for provider, metadata := range map[string][]string{
		"jarvi": {`{}`, `{"public_api_key":true}`, `{"public_api_key":"x","currency":1}`, `{"public_api_key":"x\r\ny"}`},
		"job51": {`{}`, `{"ctmid":true}`, `{"ctmid":0}`, `{"ctmid":1.5}`, `{"ctmid":"-1"}`},
	} {
		for _, raw := range metadata {
			if _, err := api.SmallProviderOptionsFromMetadata(provider, config["board_url"], raw); err == nil {
				t.Fatal("unsupported metadata admitted", provider)
			}
		}
	}
}
