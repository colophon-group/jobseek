package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestDocumentInteractionsPublicOriginalInventories(t *testing.T) {
	directory := os.Getenv("JOBSEEK_INTERACTION_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires private original and physical Lightpanda captures")
	}
	raw, err := os.ReadFile(filepath.Join(directory, "native1005-interaction-original-selected26-2026-10-10.json"))
	var rows []struct {
		ID        string
		Canonical map[string]string
	}
	if err != nil || json.Unmarshal(raw, &rows) != nil || len(rows) != 26 {
		t.Fatal("canonical cohort unavailable")
	}
	for _, row := range rows {
		slug := row.Canonical["board_slug"]
		t.Run(slug, func(t *testing.T) {
			body, err := os.ReadFile(filepath.Join(directory, "native1005-interaction-"+slug+"-original-public-capture1-2026-10-10.json"))
			var original struct {
				Status    string
				Jobs      []struct{ URL string }
				Exchanges []struct {
					Method, URL, Body string
					Status            int
					ResponseHeaders   map[string]string `json:"response_headers"`
					SetCookies        []string          `json:"set_cookies"`
				}
			}
			if err != nil || json.Unmarshal(body, &original) != nil {
				t.Fatal("original case unavailable")
			}
			body, err = os.ReadFile(filepath.Join(directory, "native1005-interaction-"+slug+"-lightpanda-public-capture1-2026-10-10.pb"))
			if err != nil {
				t.Fatal("physical typed document unavailable")
			}
			value, err := executor.DecodeResult(body)
			if err != nil {
				t.Fatal("physical typed document malformed")
			}
			profile, err := queue.InspectRichMonitor(row.ID, row.Canonical)
			if err != nil {
				t.Fatal("canonical profile unavailable")
			}
			var mu sync.Mutex
			used := make([]bool, len(original.Exchanges))
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				mu.Lock()
				defer mu.Unlock()
				for i, x := range original.Exchanges {
					if !used[i] && r.Method == x.Method && sharedOriginalRequestURLEqual(r.URL.String(), x.URL) {
						used[i] = true
						header := http.Header{}
						for k, v := range x.ResponseHeaders {
							header.Set(k, v)
						}
						for _, v := range x.SetCookies {
							header.Add("Set-Cookie", v)
						}
						return &http.Response{StatusCode: x.Status, Header: header, Body: io.NopCloser(strings.NewReader(x.Body)), Request: r}, nil
					}
				}
				return nil, fmt.Errorf("verification request absent from original trace")
			})}
			calls := 0
			found, failure := collectRenderedDOMPages(context.Background(), profile, row.Canonical, client, func(int) (*runtimev1.BrowserResult, error) { calls++; return value, nil })
			if original.Status != "complete" {
				if failure == nil && (found.Response == nil || !found.Response.reserved) {
					t.Fatal("original failed inventory gained completion authority")
				}
				return
			}
			if failure != nil || found.Truncated || calls != 1 {
				t.Fatal("original complete inventory did not reproduce", "error", failure, "navigation_calls", calls)
			}
			want, got := []string{}, []string{}
			for _, job := range original.Jobs {
				want = append(want, job.URL)
			}
			for _, job := range found.Jobs {
				if !job.URLOnly {
					t.Fatal("URL inventory changed representation")
				}
				got = append(got, job.URL)
			}
			sort.Strings(want)
			sort.Strings(got)
			if !reflect.DeepEqual(got, want) {
				t.Fatal("original full inventory differs", "native_count", len(got), "original_count", len(want))
			}
			for _, done := range used {
				if !done {
					t.Fatal("original verification request skipped")
				}
			}
			t.Logf("jobs=%d complete_original_URL_inventory_matches=true", len(got))
		})
	}
}
