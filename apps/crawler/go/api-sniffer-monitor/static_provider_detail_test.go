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
