package dom

import (
	"encoding/json"
	"os"
	"testing"
)

func TestListingEmptyMatchesActualPythonLegacyEvidence(t *testing.T) {
	var cases []struct {
		Name, Source, Selector string
		Text                   *string
		JobCount               int `json:"job_count"`
		Accepted               bool
	}
	body, e := os.ReadFile("testdata/python_listing_empty.json")
	if e != nil || json.Unmarshal(body, &cases) != nil {
		t.Fatal("Python reference unavailable", e)
	}
	if len(cases) != 16 {
		t.Fatal("reference cases missing")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o := ListingConfig{EmptySelector: c.Selector}
			if c.Text != nil {
				o.EmptyText = *c.Text
			}
			if (ValidateListingEmpty(c.Source, o, c.JobCount) == nil) != c.Accepted {
				t.Fatal("Python explicit zero evidence differs")
			}
		})
	}
}

func TestListingEmptyConfigurationRefusesAmbiguousInventories(t *testing.T) {
	if _, e := ListingOptions(Object{"empty_selector": ".empty", "empty_text": "No jobs", "link_selector": "a.job"}, "https://example.com/careers"); e != nil {
		t.Fatal("valid empty contract refused", e)
	}
	for _, o := range []Object{
		{"empty_text": "No jobs", "link_selector": "a.job"},
		{"empty_selector": "[", "link_selector": "a.job"},
		{"empty_selector": ".empty", "link_selector": "a.job", "empty_text": false},
		{"empty_selector": ".empty"},
		{"empty_selector": ".empty", "link_selector": "a.job", "encoding": "utf-8"},
	} {
		if _, e := ListingOptions(o, "https://example.com/careers"); e == nil {
			t.Fatal("unsupported empty contract admitted", o)
		}
	}
}

func TestListingEmptySelectorListsKeepFirstDocumentMatch(t *testing.T) {
	for _, tc := range []struct {
		name, selector, source string
		accepted               bool
	}{
		{"later-selector-matches", ".missing, .empty", `<div class="empty">No jobs</div>`, true},
		{"document-order-before-selector-order", ".later, .first", `<div class="first">Open roles below</div><div class="later">No jobs</div>`, false},
		{"overlapping-selectors", ".empty, #empty", `<div class="empty" id="empty">No jobs</div>`, true},
		{"quoted-comma", `[data-state="none, available"], .empty`, `<div data-state="none, available">No jobs</div>`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			options, err := ListingOptions(Object{"empty_selector": tc.selector, "empty_text": "No jobs", "link_selector": "a.job"}, "https://example.com/careers")
			if err != nil {
				t.Fatal("valid selector group refused", err)
			}
			if (ValidateListingEmpty(tc.source, options, 0) == nil) != tc.accepted {
				t.Fatal("selector group changed first-match empty proof")
			}
			if err := ValidateListingEmpty(tc.source, options, 1); err != nil {
				t.Fatal("empty marker overrode a nonempty inventory", err)
			}
		})
	}
}
