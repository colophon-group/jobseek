package worker

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestOriginalPythonOnclickInventoryParity(t *testing.T) {
	raw, err := os.ReadFile("../dom-detail/testdata/python_onclick_listings.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Source, Base, Selector, Include string
		Error                                 bool
		URLs                                  []string
	}
	if json.Unmarshal(raw, &cases) != nil {
		t.Fatal("invalid original fixtures")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			got, err := dom.ParseOnclickLinks(context.Background(), c.Source, c.Base, c.Selector, c.Include, true, pythonJoinURL)
			if (err != nil) != c.Error {
				t.Fatalf("failure %v want %v", err, c.Error)
			}
			if c.Error {
				return
			}
			sort.Strings(got)
			if !reflect.DeepEqual(got, c.URLs) {
				t.Fatalf("got %q want %q", got, c.URLs)
			}
		})
	}
}

func TestStaticInlineDiscoveryRetainsOriginalFieldsAndFailureAuthority(t *testing.T) {
	cases := []struct {
		Name, Metadata, Source string
		Count                  int
		Failure, Rich          bool
	}{
		{"onclick", `{"onclick_selector":"tr.item"}`, `<table><tr class="item" onclick="window.location='/jobs/工程師';"><td>Role</td></tr></table>`, 1, false, false},
		{"onclick-drift", `{"onclick_selector":"tr.item"}`, `<table><tr class="item" onclick="window.open('/jobs/one')"><td>Role</td></tr></table>`, 0, true, false},
		{"onclick-unproved-zero", `{"onclick_selector":"tr.item"}`, `<table></table>`, 0, true, false},
		{"onclick-proved-zero", `{"onclick_selector":"tr.item","empty_selector":".empty","empty_text":"No jobs"}`, `<table></table><p class="empty">No jobs</p>`, 0, false, false},
		{"script-zero", `{"script_json_links":{"variable":"jobs","url_field":"link","url_template":"{value}"}}`, `<script>const jobs=[];</script>`, 0, false, false},
		{"script-rich", `{"script_json_links":{"function":"grid","argument_index":2,"url_field":"link","url_template":"{value}","title_field":"title","locations_field":"locations"}}`, `<script>grid(1,2,[{"link":"https://example.com/jobs/one","title":" R&amp;D Engineer ","locations":[" Geneva ","Geneva"]}]);</script>`, 1, false, true},
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"crawler_type": "dom", "board_url": "https://example.com/careers", "metadata": c.Metadata}
			profile := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: config["board_url"]}
			if c.Rich {
				profile.Profile = "dom.direct-rows/v1"
			}
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(c.Source)), Request: r}, nil
			})}
			got, err := discoverDOMInventory(context.Background(), client, profile, config)
			if (err != nil) != c.Failure || len(got.Jobs) != c.Count || got.Truncated {
				t.Fatal(got, err)
			}
			if c.Rich && (*got.Jobs[0].Title != "R&D Engineer" || !reflect.DeepEqual(got.Jobs[0].Locations, []string{"Geneva"}) || got.Jobs[0].URLOnly) {
				t.Fatal("rich script fields changed", got)
			}
			if c.Name == "onclick" && (got.Jobs[0].URL != "https://example.com/jobs/工程師" || !got.Jobs[0].URLOnly || got.Jobs[0].Title != nil) {
				t.Fatal("Python URL-only identity changed", got)
			}
		})
	}
}

func TestPublicStaticInlineOriginalInventoriesThroughVerifiedTransport(t *testing.T) {
	directory := os.Getenv("JOBSEEK_INLINE_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("private complete public capture not supplied")
	}
	for _, slug := range []string{"pwc-careers-za-school", "pwc-careers-za-graduate", "covenant-health-physician-opportunities", "city-of-new-york-nyc-city-council"} {
		t.Run(slug, func(t *testing.T) {
			raw, err := os.ReadFile(directory + "/native1001-shared-" + slug + "-public-capture2-2026-10-10.json")
			if err != nil {
				t.Fatal("private capture unavailable")
			}
			var c struct {
				Provider, Status string
				Board            map[string]json.RawMessage
				Exchanges        []struct {
					Method, URL, Body string
					Status            int
					ContentType       string `json:"content_type"`
				}
				Jobs []struct {
					URL       string
					Title     *string
					Locations []string
				}
				Truncated bool
			}
			if json.Unmarshal(raw, &c) != nil || c.Provider != "dom" || c.Status != "complete" || len(c.Exchanges) != 1 || c.Truncated {
				t.Fatal("capture qualification differs")
			}
			config := map[string]string{}
			for k, v := range c.Board {
				if k == "metadata" {
					config[k] = string(v)
				} else {
					var s string
					if json.Unmarshal(v, &s) == nil {
						config[k] = s
					}
				}
			}
			options, err := queue.DOMMonitorOptions(config)
			if err != nil {
				t.Fatal("public configuration unsupported", err)
			}
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				x := c.Exchanges[0]
				if calls != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.RequestURI() != x.URL {
					t.Error("original public request identity changed")
				}
				w.Header().Set("Content-Type", x.ContentType)
				w.WriteHeader(x.Status)
				io.WriteString(w, x.Body)
			}))
			profile := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: config["board_url"]}
			if options.ScriptLinks.Rich() {
				profile.Profile = "dom.direct-rows/v1"
			}
			got, err := discoverDOMInventory(context.Background(), client.client, profile, config)
			if err != nil || got.Truncated || len(got.Jobs) != len(c.Jobs) || calls != 1 {
				t.Fatal("full original inventory changed", len(got.Jobs), len(c.Jobs), err)
			}
			sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
			for i, j := range got.Jobs {
				want := c.Jobs[i]
				if j.URL != want.URL || !reflect.DeepEqual(j.Title, want.Title) || !reflect.DeepEqual(j.Locations, want.Locations) || j.Description != nil {
					t.Fatal("original public canonical fields changed at index", i)
				}
			}
		})
	}
}
