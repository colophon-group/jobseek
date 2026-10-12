package worker

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"sync"
	"testing"
	"time"

	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestDOMGroupedStaticOriginalPublicInventories(t *testing.T) {
	dir := os.Getenv("JOBSEEK_DOM_RANGES_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original public pagination inventory")
	}
	for _, slug := range []string{"university-of-geneva-careers", "fressnapf-maxizoo-luxembourg"} {
		t.Run(slug, func(t *testing.T) {
			var c struct {
				Canonical map[string]string
				Status    string
				Truncated bool
				Jobs      []struct{ URL string }
				Captures  []struct {
					URL, File string
					Status    int
					Headers   map[string]string
				}
			}
			f, err := os.Open(filepath.Join(dir, slug+"-original.json"))
			if err != nil {
				t.Fatal(err)
			}
			defer f.Close()
			if json.NewDecoder(f).Decode(&c) != nil || c.Status != "succeeded" || c.Truncated || len(c.Captures) == 0 {
				t.Fatal("complete original inventory unavailable")
			}
			responses := map[string]int{}
			for i, x := range c.Captures {
				if _, exists := responses[x.URL]; exists {
					t.Fatal("duplicate reference request identity")
				}
				responses[x.URL] = i
			}
			calls := 0
			var mu sync.Mutex
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				defer mu.Unlock()
				if calls >= len(c.Captures) {
					t.Error("unadvertised page fetched")
					w.WriteHeader(400)
					return
				}
				requestURL := "https://" + r.Host + r.URL.String()
				i, ok := responses[requestURL]
				if !ok {
					t.Error("unobserved request")
					w.WriteHeader(400)
					return
				}
				x := c.Captures[i]
				delete(responses, requestURL)
				calls++
				if r.Method != "GET" || "https://"+r.Host+r.URL.String() != x.URL || filepath.Base(x.File) != x.File {
					t.Error("original page identity changed")
					w.WriteHeader(400)
					return
				}
				body, err := os.Open(filepath.Join(dir, x.File))
				if err != nil {
					t.Error(err)
					return
				}
				defer body.Close()
				gz, err := gzip.NewReader(body)
				if err != nil {
					t.Error(err)
					return
				}
				defer gz.Close()
				for k, v := range x.Headers {
					w.Header().Set(k, v)
				}
				w.WriteHeader(x.Status)
				if _, err := io.Copy(w, gz); err != nil {
					t.Error(err)
				}
			}))
			p, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", c.Canonical)
			if err != nil {
				t.Fatal("canonical advertised-range monitor refused", err)
			}
			result, err := discoverDOMInventory(context.Background(), client, p, c.Canonical)
			if err != nil || result.Truncated || calls != len(c.Captures) {
				t.Fatal("complete original pagination changed", err, calls)
			}
			got, want := []string{}, []string{}
			for _, j := range result.Jobs {
				if !j.URLOnly {
					t.Fatal("URL inventory acquired unsolicited fields")
				}
				got = append(got, j.URL)
			}
			for _, j := range c.Jobs {
				want = append(want, j.URL)
			}
			sort.Strings(got)
			sort.Strings(want)
			if !reflect.DeepEqual(got, want) {
				t.Fatal("original public inventory identities changed", len(got), len(want))
			}
			t.Log("complete original public inventory and request sequence match", len(got), calls)
			if os.Getenv("JOBSEEK_DOM_STATIC_PUBLIC_LIVE") == "1" {
				ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
				defer cancel()
				live, err := discoverDOMInventory(ctx, jsonld.NewPublicClient(), p, c.Canonical)
				if err != nil || live.Truncated {
					t.Fatal("verified public Go transport did not produce a complete inventory", err)
				}
				urls := []string{}
				for _, job := range live.Jobs {
					urls = append(urls, job.URL)
					if !job.URLOnly {
						t.Fatal("public URL inventory changed field ownership")
					}
				}
				sort.Strings(urls)
				if !reflect.DeepEqual(urls, want) {
					t.Fatal("current public Go inventory differs from the fresh original reference", len(urls), len(want))
				}
				t.Log("actual verified-TLS Go public inventory matches original", len(urls))
			}
		})
	}
}
