package apisniffer

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestNinthProviderOptionsPreserveCurrentRegistry(t *testing.T) {
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
		switch provider {
		case "beehire", "hirehive", "welcometothejungle", "computrabajo", "ycombinator":
		default:
			continue
		}
		raw := row[head["monitor_config"]]
		o, err := NinthProviderOptionsFromMetadata(provider, row[head["board_url"]], raw)
		if err != nil || !o.ResourceMatches(o.ListingURL()) {
			t.Fatal("current public provider route rejected", provider, err)
		}
		if o.ResourceMatches("https://other.example/private") {
			t.Fatal("foreign resource admitted")
		}
		if o.Proxy {
			proxies++
		}
		counts[provider]++
		if provider == "hirehive" && (!o.ResourceMatches(o.PageURL(1)) || o.DefaultLocationType != "hybrid") {
			t.Fatal("default/page contract lost")
		}
		if provider == "computrabajo" && !o.ResourceMatches(o.PageURL(2)) {
			t.Fatal("provider pagination rejected")
		}
	}
	if counts["beehire"] != 1 || counts["hirehive"] != 1 || counts["welcometothejungle"] != 3 || counts["computrabajo"] != 11 || counts["ycombinator"] != 4 || proxies != 4 {
		b, _ := json.Marshal(counts)
		t.Fatal("registry routes changed", string(b), proxies)
	}
}

func TestNinthProviderListingsMatchActualPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_ninth_provider_core.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Listings []struct {
			Provider, Name, Source, Body string
			Page                         int
			Expected                     *struct {
				URLs  []string
				Total int
			}
			Error bool
		}
	}
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.Listings) != 18 {
		t.Fatal("actual Python fixture unavailable")
	}
	for _, c := range fixture.Listings {
		t.Run(c.Name, func(t *testing.T) {
			o, err := NinthProviderOptionsFromMetadata(c.Provider, c.Source, "{}")
			if err != nil {
				t.Fatal(err)
			}
			var urls []string
			total := 0
			if c.Provider == "ycombinator" {
				urls, err = YCombinatorListingURLs(c.Body, o)
			} else {
				urls, total, err = ParseComputrabajoListing([]byte(c.Body), o, c.Page)
			}
			if c.Error {
				if err == nil {
					t.Fatal("Python rejected listing accepted")
				}
				return
			}
			if err != nil || c.Expected == nil || total != c.Expected.Total || !reflect.DeepEqual(urls, c.Expected.URLs) {
				t.Fatal("Python inventory changed", err, urls, total, c.Expected)
			}
		})
	}
}
