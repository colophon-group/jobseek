package apisniffer

import (
	"encoding/csv"
	"os"
	"testing"
)

func TestUnifrCurrentRegistryFixedSourcesAndBoundedResources(t *testing.T) {
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
	count := 0
	for _, row := range rows[1:] {
		if row[head["monitor_type"]] != "unifr" {
			continue
		}
		o, err := UnifrOptionsFromMetadata(row[head["board_url"]], row[head["monitor_config"]])
		if err != nil || !o.ResourceMatches(o.URL) {
			t.Fatal(row[head["board_slug"]], err, o)
		}
		for _, foreign := range []string{"https://example.com/", "https://www.unifr.ch/other", o.URL + "?changed=1", UnifrDetailRoot + "/fr/0", UnifrDetailRoot + "/fr/../1", UnifrDetailRoot + "/fr/1?changed=1", UnifrDetailRoot + "/en/1"} {
			if o.ResourceMatches(foreign) {
				t.Fatal("foreign resource admitted", row[head["board_slug"]], foreign)
			}
		}
		if _, err := UnifrOptionsFromMetadata(o.URL+"?changed=1", row[head["monitor_config"]]); err == nil {
			t.Fatal("fixed source rewritten")
		}
		if o.Kind == "central" && (!o.ResourceMatches(UnifrCentralDE) || !o.ResourceMatches(UnifrDetailRoot+"/fr/1911") || !o.ResourceMatches(UnifrDetailRoot+"/de/1911")) {
			t.Fatal("locale union scope lost", o)
		}
		if o.Kind != "central" && o.ResourceMatches(UnifrDetailRoot+"/fr/1911") {
			t.Fatal("department scope expanded into central details", o)
		}
		count++
	}
	if count != 9 {
		t.Fatal(count)
	}
}

func TestUnifrSourceCannotSelectProxyBrowserOrUnknownContracts(t *testing.T) {
	for _, raw := range []string{`{}`, `{"source":"unknown"}`, `{"source":true}`, `{"source":"central","proxy":true}`, `{"source":"central","render":true}`, `{"source":"central","url":"https://example.com"}`, `{"source":"central","source":"law"}`} {
		if _, err := UnifrOptionsFromMetadata(UnifrCentralFR, raw); err == nil {
			t.Fatal("unsupported source accepted", raw)
		}
	}
}

func TestUnifrLifecycleOptionsDoNotChangeFixedResourceScope(t *testing.T) {
	o, err := UnifrOptionsFromMetadata(UnifrCentralFR, `{"source":"central","delist_threshold":2,"drop_threshold":0.3,"blast_radius_floor":0.2}`)
	if err != nil || o.URL != UnifrCentralFR || !o.ResourceMatches(UnifrCentralDE) || o.ResourceMatches("https://other.example/jobs") {
		t.Fatal(err, o)
	}
}
