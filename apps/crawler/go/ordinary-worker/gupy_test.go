package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestGupyMatchesActualPythonTenantInventoryAndRequests(t *testing.T) {
	data, err := os.ReadFile("testdata/python_gupy.json")
	var corpus struct {
		Cases []struct {
			Name, Body string
			Headers    map[string]string
			Requests   []struct{ Method, URL string }
			Expected   struct {
				Error, Truncated bool
				URLs             []string
			}
		}
	}
	if err != nil || json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) != 10 {
		t.Fatal("actual Python Gupy corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := []struct{ Method, URL string }{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests = append(requests, struct{ Method, URL string }{r.Method, "https://" + r.Host + r.URL.String()})
				for k, v := range c.Headers {
					w.Header().Set(k, v)
				}
				fmt.Fprint(w, c.Body)
			}))
			p := queue.GreenhouseMonitorProfile{Provider: "gupy", Profile: "gupy.nextdata-urls/v1", Token: "fixture", Endpoint: "https://fixture.gupy.io/"}
			result, err := discoverGupyInventory(context.Background(), client.client, p)
			if (err != nil) != c.Expected.Error || !reflect.DeepEqual(requests, c.Requests) {
				t.Fatal("inventory failure or requests differ", err)
			}
			if !c.Expected.Error {
				urls := []string{}
				for _, j := range result.Jobs {
					urls = append(urls, j.URL)
				}
				if !reflect.DeepEqual(urls, c.Expected.URLs) || result.Truncated != c.Expected.Truncated {
					t.Fatal("identity/incomplete inventory differs", urls, result.Truncated, c.Expected)
				}
			}
		})
	}
}

func TestGupyPreservesProviderIncompleteAndDecodedHTMLThresholds(t *testing.T) {
	p := queue.GreenhouseMonitorProfile{Token: "fixture", Endpoint: "https://fixture.gupy.io/"}
	var b strings.Builder
	b.WriteString(`<script id="__NEXT_DATA__">{"props":{"pageProps":{"subdomain":"fixture","careerPage":{},"jobs":[`)
	for i := 1; i <= 50_001; i++ {
		if i > 1 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, `{"id":%d}`, i)
	}
	b.WriteString(`]}}}</script>`)
	result, err := parseGupyInventory(context.Background(), RichDiscovery{Jobs: []RichMonitorJob{}}, p, b.String())
	if err != nil || !result.Truncated || len(result.Jobs) != 50_001 {
		t.Fatal("Gupy inventory threshold dropped URLs or became complete", err)
	}
	source := `<script id="__NEXT_DATA__">{"props":{"pageProps":{"subdomain":"fixture","careerPage":{},"jobs":[]}}}</script>`
	source += strings.Repeat("界", 5_000_000-len(source))
	result, err = parseGupyInventory(context.Background(), RichDiscovery{Jobs: []RichMonitorJob{}}, p, source)
	if err != nil || !result.Truncated || len(result.Jobs) != 0 {
		t.Fatal("decoded HTML threshold lost truncation", err)
	}
}
