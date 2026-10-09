package worker

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestSharedServiceAnnotationsActualOriginalCurrentMonitorOutputs(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_service_annotations.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []sharedServiceOriginalCase
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 7 {
		t.Fatal("original seven-board oracle unavailable")
	}
	runServiceAnnotationOriginalCases(t, cases)
}

type sharedServiceOriginalCase struct {
	Name, Response string
	Browser        bool
	Board          struct {
		Provider string
		BoardURL string `json:"board_url"`
		Metadata map[string]any
	}
	Expected  []map[string]any
	Exchanges []struct {
		Method, URL, Body string
		Response          struct {
			Status  int
			Body    string
			Headers map[string]string
		}
	}
}

func runServiceAnnotationOriginalCases(t *testing.T, cases []sharedServiceOriginalCase) {
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Board.Metadata)
			config := map[string]string{"crawler_type": c.Board.Provider, "board_url": c.Board.BoardURL, "metadata": string(md), "monitor_needs_browser": "0"}
			if c.Browser {
				config["monitor_needs_browser"] = "1"
			}
			p := queue.GreenhouseMonitorProfile{Provider: c.Board.Provider, Endpoint: c.Board.BoardURL}
			calls := 0
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				if calls >= len(c.Exchanges) {
					t.Fatal("unexpected request")
				}
				x := c.Exchanges[calls]
				calls++
				var body []byte
				if r.Body != nil {
					body, _ = io.ReadAll(r.Body)
				}
				if r.Method != x.Method || !portalURLEqual(r.URL.String(), x.URL) || string(body) != x.Body && !finalHTTPRequestBodyEqual("application/json", string(body), x.Body) {
					t.Fatal("original request differs")
				}
				reply := c.Response
				status := 200
				headers := http.Header{}
				if x.Response.Status != 0 {
					reply = x.Response.Body
					status = x.Response.Status
					for k, v := range x.Response.Headers {
						headers.Set(k, v)
					}
				}
				return &http.Response{StatusCode: status, Header: headers, Body: io.NopCloser(strings.NewReader(reply)), Request: r}, nil
			})}
			var got RichDiscovery
			var e error
			switch c.Board.Provider {
			case "dom":
				p.Profile = "dom.direct-urls/v1"
				got, e = discoverDOMInventory(context.Background(), client, p, config)
			case "sitemap":
				p.Profile = "sitemap.explicit-urls/v1"
				p.Endpoint = c.Board.Metadata["sitemap_url"].(string)
				got, e = discoverSitemapInventory(context.Background(), client, p, config)
			case "api_sniffer":
				if c.Browser {
					o, z := api.BrowserReplayOptionsFromMetadata(c.Board.BoardURL, string(md))
					if z != nil {
						t.Fatal(z)
					}
					inventory, z := api.DiscoverBrowserReplay(context.Background(), o, func(_ context.Context, r api.Request) (*api.Document, error) {
						calls++
						if calls == 1 {
							return api.Decode([]byte(c.Response))
						}
						return api.Decode([]byte(`{"data":{"results":[]}}`))
					}, pythonJoinURL, false)
					e = z
					got.Truncated = inventory.Truncated
					for _, job := range inventory.Jobs {
						got.Jobs = append(got.Jobs, RichMonitorJob{URL: job.URL, URLOnly: inventory.URLOnly})
					}
				} else {
					o, z := queue.APISnifferMonitorOptions(config)
					if z != nil {
						t.Fatal(z)
					}
					p.Endpoint, p.Profile = o.Endpoint, "api_sniffer.http-items/v1"
					got, e = discoverAPISnifferInventory(context.Background(), client, p, config)
				}
			}
			if e != nil || got.Truncated || len(got.Jobs) != len(c.Expected) || calls == 0 {
				t.Fatal("original inventory differs", e)
			}
			sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
			sort.Slice(c.Expected, func(i, j int) bool { return c.Expected[i]["url"].(string) < c.Expected[j]["url"].(string) })
			for i, fields := range c.Expected {
				want := RichMonitorJob{URL: fields["url"].(string), URLOnly: c.Browser || c.Board.Provider == "dom"}
				if _, rich := fields["title"]; rich {
					title, z := executor.CoerceText(fields["title"])
					if z != nil {
						t.Fatal(z)
					}
					description, z := executor.CoerceText(fields["description"])
					if z != nil {
						t.Fatal(z)
					}
					metadata, _ := fields["metadata"].(map[string]any)
					extras, _ := fields["extras"].(map[string]any)
					want = RichMonitorJob{URL: fields["url"].(string), Title: title, Description: description, Metadata: metadata, Extras: extras, DatePosted: fields["date_posted"], Language: fields["language"], EmploymentType: fields["employment_type"], JobLocationType: fields["job_location_type"]}
					if loc, ok := fields["locations"].([]any); ok {
						for _, v := range loc {
							want.Locations = append(want.Locations, v.(string))
						}
					}
				}
				if len(got.Jobs[i].Metadata) == 0 {
					got.Jobs[i].Metadata = nil
				}
				if len(got.Jobs[i].Extras) == 0 {
					got.Jobs[i].Extras = nil
				}
				if !reflect.DeepEqual(jsonNormalizedRichJob(t, got.Jobs[i]), jsonNormalizedRichJob(t, want)) {
					t.Fatalf("original complete field differs: got %+v want %+v", got.Jobs[i], want)
				}
			}
		})
	}
}

// Private same-capture public evidence is opt-in; CI uses the committed original oracle.
func TestSharedServiceAnnotationsSameCapturedPublicOutputs(t *testing.T) {
	directory := os.Getenv("JOBSEEK_SHARED_ANNOTATIONS_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires private original public captures")
	}
	cases := []sharedServiceOriginalCase{}
	for _, slug := range []string{"citadel-securities-global", "cyberbit-rangeforce-bamboohr", "molecubes-careers", "mutable-tactics-careers-ef", "sophia-genetics-careers"} {
		raw, e := os.ReadFile(filepath.Join(directory, "native988-shared-annotations-public-"+slug+"-initial1-2026-10-09.json"))
		if e != nil {
			t.Fatal(e)
		}
		var status struct{ Status string }
		var c sharedServiceOriginalCase
		if json.Unmarshal(raw, &status) != nil || json.Unmarshal(raw, &c) != nil || status.Status != "complete" || len(c.Expected) == 0 {
			t.Fatal("healthy original capture unavailable", slug)
		}
		cases = append(cases, c)
	}
	runServiceAnnotationOriginalCases(t, cases)
}
