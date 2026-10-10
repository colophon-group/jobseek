package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"reflect"
	"sort"
	"testing"
)

func TestPublicPostfinanceOriginalRSSInventory(t *testing.T) {
	directory := os.Getenv("JOBSEEK_RSS_IDENTITY_CAPTURE_DIR")
	if directory == "" {
		t.Skip("private complete public feed not supplied")
	}
	raw, err := os.ReadFile(directory + "/native1008-rss-identity-postfinance-careers-original-public-capture1-2026-10-10.json")
	if err != nil {
		t.Fatal("public capture unavailable")
	}
	var c struct {
		Provider, Status string
		Board            map[string]json.RawMessage
		Jobs             []map[string]any
		Exchanges        []struct {
			Method, URL, Body string
			Status            int
			ResponseHeaders   map[string]string `json:"response_headers"`
		}
		Truncated bool
	}
	if json.Unmarshal(raw, &c) != nil || c.Provider != "rss" || c.Status != "complete" || c.Truncated || len(c.Exchanges) != 1 || len(c.Jobs) != 277 {
		t.Fatal("original complete capture invalid")
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
	p, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if err != nil {
		t.Fatal("original PostFinance RSS migration profile unsupported", err)
	}
	requests := 0
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		x := c.Exchanges[0]
		if requests != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.RequestURI() != x.URL {
			t.Error("original feed request changed")
		}
		w.Header().Set("Content-Type", x.ResponseHeaders["content-type"])
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	got, err := DiscoverRichMonitor(context.Background(), client.client, p)
	if err != nil || got.Truncated || len(got.Jobs) != len(c.Jobs) || requests != 1 {
		t.Fatal("original complete feed changed", err)
	}
	sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
	for n, j := range got.Jobs {
		fields := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "metadata": j.Metadata, "employment_type": j.EmploymentType, "date_posted": j.DatePosted, "source_identity": nullableNextdataIdentity(j.SourceIdentity)}
		b, _ := json.Marshal(fields)
		var actual map[string]any
		json.Unmarshal(b, &actual)
		for k, want := range c.Jobs[n] {
			if !reflect.DeepEqual(actual[k], want) {
				t.Fatal("original feed field changed", n, k)
			}
		}
	}
	policyRaw, err := os.ReadFile(directory + "/native1008-postfinance-original-postprocessed-policy1-2026-10-10.json")
	var policy struct {
		CanonicalRecords int `json:"canonical_records"`
		SecurityFiltered int `json:"security_filtered_count"`
		Truncated        bool
		Jobs             []map[string]any
	}
	if err != nil || json.Unmarshal(policyRaw, &policy) != nil || policy.CanonicalRecords != 24 || len(policy.Jobs) != 24 || policy.SecurityFiltered != 0 || policy.Truncated {
		t.Fatal("original complete postprocessed policy reference unavailable")
	}
	canonical, err := applyFeedMonitorURLs(context.Background(), config, got.Jobs)
	if err != nil || len(canonical) != len(policy.Jobs) {
		t.Fatal("published PostFinance filter/collision inventory changed", err, len(canonical))
	}
	sort.Slice(canonical, func(i, j int) bool { return canonical[i].URL < canonical[j].URL })
	for n, j := range canonical {
		fields := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "metadata": j.Metadata, "employment_type": j.EmploymentType, "date_posted": j.DatePosted, "job_location_type": j.JobLocationType, "source_identity": nullableNextdataIdentity(j.SourceIdentity), "extras": j.Extras}
		raw, _ := json.Marshal(fields)
		var actual map[string]any
		json.Unmarshal(raw, &actual)
		for k, want := range policy.Jobs[n] {
			if !reflect.DeepEqual(actual[k], want) {
				t.Fatal("original postprocessed canonical field changed", n, k)
			}
		}
	}
}
