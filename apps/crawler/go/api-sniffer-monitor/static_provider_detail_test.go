package apisniffer

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestStaticProvidersActualPythonDetailFieldsAndIdentity(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Provider, Name, Body string
			Content              map[string]any
			Failed               bool
		}
		Identities []struct {
			Provider, Source, Endpoint string
			Valid                      bool
		}
	}
	raw, err := os.ReadFile("testdata/python_static_provider_detail.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Cases) != 51 || len(corpus.Identities) != 16 {
		t.Fatal("actual Python static detail corpus absent", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			out, err := ParseStaticProviderDetail(c.Provider, c.Body)
			if (err != nil) != c.Failed {
				t.Fatal("failure differs", err, c.Failed)
			}
			if err != nil {
				return
			}
			for key, want := range c.Content {
				got := out[key]
				a, _ := json.Marshal(got)
				b, _ := json.Marshal(want)
				if string(a) != string(b) {
					t.Errorf("%s differs\n%s\n%s", key, a, b)
				}
			}
		})
	}
	for _, c := range corpus.Identities {
		t.Run("identity/"+c.Provider+"/"+c.Source, func(t *testing.T) {
			o, err := StaticProviderDetailOptionsForSource(c.Provider, c.Source)
			if (err == nil) != c.Valid || c.Valid && (o.Endpoint != c.Endpoint || !o.ResourceMatches(c.Endpoint) || o.ResourceMatches("https://foreign.example/private")) {
				t.Fatal(o, err, c.Valid, c.Endpoint)
			}
		})
	}
}

func TestStaticProviderDetailSafetyBoundsAndIsolatedSurrogate(t *testing.T) {
	for _, body := range []string{strings.Repeat("漢", 2_000_001), `api.fillList('requisitionDescriptionInterface','descRequisition',['\uD800'])`, `api.fillList('requisitionDescriptionInterface','descRequisition',['` + strings.Repeat("x", 1_000_001) + `'])`} {
		if _, err := ParseEnterpriseFillList(body); err == nil {
			t.Fatal("unrepresentable or oversized payload admitted")
		}
	}
}

func TestLiveShiftedTaleoEnterpriseDetailFixtures(t *testing.T) {
	for _, c := range []struct {
		name, location, employment, posted string
	}{
		{"hyatt", "US-TX-San Antonio", "Full-time", ""},
		{"jumeirah", "Oman-Muscat", "", "Oct 11, 2026, 5:31:35 AM"},
		{"jumeirah_bahrain", "Bahrain", "", "Oct 7, 2026, 1:58:18 PM"},
	} {
		t.Run(c.name, func(t *testing.T) {
			body, err := os.ReadFile("../../tests/fixtures/taleo_enterprise/" + c.name + "_detail.html")
			if err != nil {
				t.Fatal(err)
			}
			out, err := ParseTaleoEnterpriseDetail(string(body))
			if err != nil {
				t.Fatal(err)
			}
			locations, ok := out["locations"].([]string)
			if !ok || len(locations) != 1 || locations[0] != c.location {
				t.Fatal("geographic location lost", out["locations"])
			}
			if got, _ := out["employment_type"].(string); got != c.employment {
				t.Fatal("employment type differs", got)
			}
			if got, _ := out["date_posted"].(string); got != c.posted {
				t.Fatal("posting date differs", got)
			}
			if extras, ok := out["extras"].(map[string]any); ok && extras["valid_through"] != nil {
				t.Fatal("non-date expiry was admitted", extras)
			}
		})
	}
}
