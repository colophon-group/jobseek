package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestLastStaticActualOriginalParsersAndIdentities(t *testing.T) {
	var c struct {
		Cases []struct {
			Provider, Name, Body string
			Config, Content      map[string]any
		}
		URLs []struct {
			Source, Candidate, Listing, Handle string
			Language                           *string
			Accepted                           bool
		} `json:"onlyfy_urls"`
		Dates []struct {
			Source   string
			Expected any
		}
		Identities []struct {
			Provider, Source string
			Expected         any
		}
		Listing struct {
			Body, Handle string
			Expected     []string
		} `json:"onlyfy_listing"`
	}
	body, err := os.ReadFile("testdata/python_last_static_detail.json")
	if err != nil || json.Unmarshal(body, &c) != nil || len(c.Cases) < 50 || len(c.URLs) != 30 || len(c.Identities) != 14 {
		t.Fatal("actual original corpus unavailable", err, len(c.Cases))
	}
	for _, v := range c.Cases {
		t.Run(v.Provider+"/"+v.Name, func(t *testing.T) {
			var out map[string]any
			var err error
			switch v.Provider {
			case "onlyfy":
				out, err = ParseOnlyfyDetail(v.Body, v.Config)
			case "phuketall":
				out, err = ParsePhuketDetail(v.Body)
			case "tupu360":
				out, err = ParseTupuDetail(v.Body)
			}
			if err != nil {
				t.Fatal(err)
			}
			for key, expected := range v.Content {
				a, _ := json.Marshal(out[key])
				b, _ := json.Marshal(expected)
				if string(a) != string(b) {
					t.Errorf("%s differs: %s / %s", key, a, b)
				}
			}
		})
	}
	for _, v := range c.URLs {
		language := ""
		if v.Language != nil {
			language = *v.Language
		}
		candidate, listing, handle, err := OnlyfyDetailURLs(v.Source, language)
		if (err == nil) != v.Accepted || err == nil && (candidate != v.Candidate || listing != v.Listing || handle != v.Handle) {
			t.Errorf("original Onlyfy URL differs: %s, %q/%q, %q/%q (%v)", v.Source, candidate, v.Candidate, listing, v.Listing, err)
		}
	}
	for _, v := range c.Dates {
		if !reflect.DeepEqual(lastPhuketDate(v.Source), v.Expected) {
			t.Errorf("original date differs: %q %v/%v", v.Source, lastPhuketDate(v.Source), v.Expected)
		}
	}
	for _, v := range c.Identities {
		var got any
		if v.Provider == "tupu360" {
			if id := TupuDetailIdentity(v.Source); id != "" {
				got = id
			}
		} else {
			if employer, job := PhuketDetailIdentity(v.Source); employer != "" {
				got = []any{employer, job}
			}
		}
		if !reflect.DeepEqual(got, v.Expected) {
			t.Errorf("original identity differs: %s %v/%v", v.Source, got, v.Expected)
		}
	}
	if got := OnlyfyListingLocation(c.Listing.Body, c.Listing.Handle); !reflect.DeepEqual(got, c.Listing.Expected) {
		t.Errorf("original listing location differs: %v/%v", got, c.Listing.Expected)
	}
}
