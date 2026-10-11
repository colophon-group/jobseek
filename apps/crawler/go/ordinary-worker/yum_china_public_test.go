package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestYumChinaOriginalPublicBrowserInventoryThroughGoHTTP(t *testing.T) {
	dir := os.Getenv("JOBSEEK_AMAZON_YUM_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected complete original public browser capture")
	}
	body, err := os.ReadFile(filepath.Join(dir, "native1009-yum-china-campus-original-public-capture1-2026-10-10.json"))
	var reference struct {
		Status    string
		Truncated bool
		Board     map[string]any
		Jobs      []struct {
			URL, Title, Description string
			Locations               []string
			EmploymentType          string `json:"employment_type"`
			JobLocationType         string `json:"job_location_type"`
		}
		Exchanges []struct {
			URL     string
			Body    string            `json:"body_base64"`
			Headers map[string]string `json:"response_headers"`
			Status  int
		}
	}
	if err != nil || json.Unmarshal(body, &reference) != nil || reference.Status != "completed-original-stream" || reference.Truncated || len(reference.Jobs) != 9 {
		t.Fatal("complete original nine-job inventory unavailable")
	}
	config := map[string]string{}
	for k, v := range reference.Board {
		if k == "metadata" {
			b, _ := json.Marshal(v)
			config[k] = string(b)
		} else {
			config[k] = fmt.Sprint(v)
		}
	}
	profile, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if err != nil || queue.MonitorWorker(profile) != queue.Browser {
		t.Fatal("original queue namespace or profile lost", err)
	}
	assets := map[string]struct {
		body    []byte
		headers map[string]string
	}{}
	for _, x := range reference.Exchanges {
		if !(api.YumChinaResourceScope{}).ResourceMatches(x.URL) {
			continue
		}
		decoded, err := base64.StdEncoding.DecodeString(x.Body)
		if err != nil || x.Status != 200 {
			t.Fatal("original asset unavailable")
		}
		assets[x.URL] = struct {
			body    []byte
			headers map[string]string
		}{decoded, x.Headers}
	}
	var browserReference struct {
		BrowserResult json.RawMessage `json:"browser_result"`
	}
	if json.Unmarshal(body, &browserReference) != nil {
		t.Fatal("original browser variable unavailable")
	}
	actualLiteral, literalErr := api.ParseYumChinaJobs(string(assets[api.YumChinaJobsURL].body))
	wantLiteral, referenceErr := api.Decode(browserReference.BrowserResult)
	if literalErr != nil || referenceErr != nil || !reflect.DeepEqual(actualLiteral.Value, wantLiteral.Value) {
		t.Fatal("all raw browser fields differ", literalErr)
	}
	calls := 0
	client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		endpoint := "https://" + r.Host + r.URL.String()
		x, ok := assets[endpoint]
		if !ok || r.Method != "GET" {
			t.Error("unbound public request")
			w.WriteHeader(400)
			return
		}
		calls++
		for k, v := range x.headers {
			w.Header().Set(k, v)
		}
		w.Write(x.body)
	}))
	check := func(client *http.Client) {
		jobs := []RichMonitorJob{}
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		result, err := discoverNextdataWithPages(ctx, client, profile, config, func(batch []RichMonitorJob) error { jobs = append(jobs, batch...); return nil }, nil)
		if err != nil || result.Truncated || len(jobs) != len(reference.Jobs) {
			t.Fatal("Go HTTP inventory differs", err, len(jobs))
		}
		for i, j := range jobs {
			want := reference.Jobs[i]
			if j.URL != want.URL || j.Title == nil || *j.Title != strings.TrimSpace(want.Title) || j.Description == nil || *j.Description != strings.TrimSpace(want.Description) || !reflect.DeepEqual(j.Locations, want.Locations) || j.EmploymentType != want.EmploymentType || j.JobLocationType != want.JobLocationType {
				t.Fatalf("original job fields differ index=%d url=%t title=%t description=%t locations=%t employment=%t location_type=%t", i, j.URL == want.URL, j.Title != nil && *j.Title == want.Title, j.Description != nil && *j.Description == want.Description, reflect.DeepEqual(j.Locations, want.Locations), j.EmploymentType == want.EmploymentType, j.JobLocationType == want.JobLocationType)
			}
		}
	}
	check(client)
	if calls != 2 {
		t.Fatal("typed HTTP route did not use exactly document and asset", calls)
	}
	if os.Getenv("JOBSEEK_AMAZON_YUM_LIVE_PUBLIC") == "1" {
		bundle, err := os.ReadFile(os.Getenv("JOBSEEK_AMAZON_YUM_CA_FILE"))
		if err != nil {
			t.Fatal(err)
		}
		live, err := NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: bundle})
		if err != nil {
			t.Fatal(err)
		}
		defer live.CloseIdleConnections()
		check(live.client)
	}
}
