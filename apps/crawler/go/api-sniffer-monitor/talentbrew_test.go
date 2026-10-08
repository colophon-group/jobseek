package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestTalentBrewPagesMatchActualPython(t *testing.T) {
	data, err := os.ReadFile("testdata/python_talentbrew.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Body            string
		URLs                  []string
		Total, Pages, Current *int
		PerPage               *int    `json:"per_page"`
		AjaxURL               *string `json:"ajax_url"`
		Attributes            map[string]string
		AjaxParams            map[string]string `json:"ajax_params"`
	}
	if json.Unmarshal(data, &cases) != nil || len(cases) != 11 {
		t.Fatal("actual Python TalentBrew corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			p, err := ParseTalentBrewPage(c.Body, "https://example.com/search-jobs")
			if err != nil {
				t.Fatal(err)
			}
			ajax := ""
			if c.AjaxURL != nil {
				ajax = *c.AjaxURL
			}
			if !reflect.DeepEqual(p.URLs, c.URLs) || !reflect.DeepEqual(p.Total, c.Total) || !reflect.DeepEqual(p.Pages, c.Pages) || !reflect.DeepEqual(p.Current, c.Current) || !reflect.DeepEqual(p.PerPage, c.PerPage) || p.AjaxURL != ajax || !reflect.DeepEqual(p.Attributes, c.Attributes) {
				t.Fatal("listing counters or scoped URLs changed", p, c)
			}
			p.AjaxURL = "/search-results"
			o := TalentBrewOptions{BoardURL: "https://example.com/search-jobs?fl=a,b&fl=c", AjaxSize: 1000}
			endpoint, err := o.AjaxURL(p, 2)
			if err != nil {
				t.Fatal(err)
			}
			u, _ := url.Parse(endpoint)
			actual := map[string]string{}
			for k, v := range u.Query() {
				if len(v) != 1 {
					t.Fatal("unexpected parameter duplication")
				}
				actual[k] = v[0]
			}
			if !reflect.DeepEqual(actual, c.AjaxParams) {
				t.Fatal("tenant facets or AJAX defaults changed", actual, c.AjaxParams)
			}
		})
	}
}

func TestTalentBrewWholeInventoryRejectsMissingPagesAndFallsBack(t *testing.T) {
	for _, mode := range []string{"html", "ajax", "ajax-fallback", "late-missing", "late-error", "cross-origin"} {
		t.Run(mode, func(t *testing.T) {
			o, err := TalentBrewOptionsFromMetadata("https://example.com/search-jobs", `{}`)
			if err != nil {
				t.Fatal(err)
			}
			o.AjaxSize = 1
			calls := []string{}
			fetch := func(ctx context.Context, endpoint string, limit int) (string, bool, error) {
				calls = append(calls, endpoint)
				if limit < 1 || !o.ResourceMatches(endpoint) {
					t.Fatal("unbound provider resource", endpoint)
				}
				if endpoint == o.BoardURL {
					ajax := ""
					if mode != "html" {
						ajax = ` data-ajax-url="/search-results"`
					}
					if mode == "cross-origin" {
						ajax = ` data-ajax-url="https://other.example/search-results"`
					}
					return `<div id="search-results" data-total-job-results="2" data-total-pages="2" data-records-per-page="1"` + ajax + `></div><div id="search-results-list"><a href="/job/one">One</a></div>`, true, nil
				}
				if strings.Contains(endpoint, "CurrentPage=1") {
					if mode == "ajax-fallback" {
						return `not json`, true, nil
					}
					return `{"results":"<a data-job-id=1 href='/job/one'>One</a>"}`, true, nil
				}
				if mode == "late-error" {
					return "", false, ErrInventory
				}
				if mode == "late-missing" {
					return "", false, nil
				}
				if strings.Contains(endpoint, "CurrentPage=2") {
					return `{"results":"<a data-job-id=2 href='/job/two'>Two</a>"}`, true, nil
				}
				return `<div id="search-results-list"><a href="/job/two">Two</a></div>`, true, nil
			}
			rows, err := DiscoverTalentBrew(context.Background(), o, fetch)
			failure := mode == "late-missing" || mode == "late-error" || mode == "cross-origin"
			if failure {
				if err == nil || len(rows) != 0 {
					t.Fatal("failed provider accepted a partial inventory", rows, err)
				}
				return
			}
			if err != nil || !reflect.DeepEqual(rows, []string{"https://example.com/job/one", "https://example.com/job/two"}) {
				t.Fatal("complete inventory/fallback lost", rows, err, calls)
			}
		})
	}
}

func TestTalentBrewOversizedValidCounterCannotLoseInventoryProof(t *testing.T) {
	_, err := ParseTalentBrewPage(`<div id="search-results" data-total-results="999999999999999999999999999999"></div><div id="search-results-list"><a href="/job/one">One</a></div>`, "https://example.com/search-jobs")
	if err != ErrInventory {
		t.Fatal("oversized total silently lost", err)
	}
}
