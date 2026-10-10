package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
)

// Opt-in replay of verified original public captures. No network calls and no
// saved public session material are part of ordinary CI or repository fixtures.
func TestPublicLastHTTPOriginalCompleteFieldsAndAtomicFailures(t *testing.T) {
	dir := os.Getenv("JOBSEEK_LAST_HTTP_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("private verified original public captures not selected")
	}
	for _, slug := range []string{"mount-sinai-health-system-south-nassau", "honeywell-aerospace-sandia", "unisante-emploi", "papa-johns-jobs"} {
		t.Run(slug, func(t *testing.T) {
			var c struct {
				Provider, Status, Today string
				Board                   struct {
					BoardURL string `json:"board_url"`
					Metadata map[string]any
				}
				Jobs      []map[string]any
				Exchanges []struct {
					Method, URL, Body string
					Status            int
					RequestBody       string `json:"request_body"`
					Headers           map[string]string
					ResponseHeaders   map[string]string `json:"response_headers"`
					SetCookies        []string          `json:"set_cookies"`
				}
			}
			b, e := os.ReadFile(filepath.Join(dir, "native-next-four-"+slug+"-original-public-capture1-2026-10-10.json"))
			if e != nil || json.Unmarshal(b, &c) != nil {
				t.Fatal("verified public capture unavailable")
			}
			md, _ := json.Marshal(c.Board.Metadata)
			o, e := LastHTTPOptionsFromMetadata(c.Provider, c.Board.BoardURL, string(md))
			if e != nil {
				t.Fatal(e)
			}
			buckets := map[string][]int{}
			for i, x := range c.Exchanges {
				buckets[x.Method+" "+x.URL] = append(buckets[x.Method+" "+x.URL], i)
			}
			var mu sync.Mutex
			fetch := func(_ context.Context, r Request) ([]byte, http.Header, error) {
				mu.Lock()
				defer mu.Unlock()
				key := r.Method + " " + r.URL
				indices := buckets[key]
				if len(indices) == 0 {
					// Original HTTP clients may choose a different query encoding,
					// while the ordered form body and every query pair must agree.
					u, _ := url.Parse(r.URL)
					for candidate, ids := range buckets {
						if len(ids) == 0 {
							continue
						}
						x := c.Exchanges[ids[0]]
						v, _ := url.Parse(x.URL)
						if x.Method == r.Method && u.Scheme == v.Scheme && u.Host == v.Host && u.Path == v.Path && reflect.DeepEqual(u.Query(), v.Query()) {
							key = candidate
							indices = ids
							break
						}
					}
				}
				if len(indices) == 0 {
					return nil, nil, fmt.Errorf("unmatched recorded public request")
				}
				x := c.Exchanges[indices[0]]
				buckets[key] = indices[1:]
				if r.Body != x.RequestBody {
					return nil, nil, fmt.Errorf("original public form body changed")
				}
				h := http.Header{}
				for k, v := range x.ResponseHeaders {
					h.Set(k, v)
				}
				for _, v := range x.SetCookies {
					h.Add("Set-Cookie", v)
				}
				if c.Provider == "infor" && r.URL == o.BoardURL {
					// The worker owns redirects; this SDK replay supplies the complete
					// original bootstrap cookie history, preserving first-seen order.
					for _, later := range c.Exchanges[1:] {
						if later.Status == 200 && later.URL != o.BoardURL {
							break
						}
						for _, v := range later.SetCookies {
							h.Add("Set-Cookie", v)
						}
					}
					return []byte(x.Body), h, nil
				}
				if x.Status != 200 {
					return nil, h, fmt.Errorf("original public HTTP failure %d", x.Status)
				}
				return []byte(x.Body), h, nil
			}
			plain := func(ctx context.Context, r Request) ([]byte, error) { b, _, e := fetch(ctx, r); return b, e }
			var jobs []Job
			switch c.Provider {
			case "infor":
				jobs, e = DiscoverInfor(context.Background(), o, fetch)
			case "peoplesoft":
				jobs, e = DiscoverPeopleSoft(context.Background(), o, plain)
			case "papa_johns":
				var urls []string
				urls, e = DiscoverPapaJohns(context.Background(), o, plain)
				for _, u := range urls {
					jobs = append(jobs, Job{URL: u})
				}
			case "unisante":
				var normalizations struct {
					Normalizations []struct {
						Source      string
						Description *string
					}
				}
				b, readErr := os.ReadFile(filepath.Join(dir, "native-next-four-unisante-original-public-replay1-2026-10-10.json"))
				if readErr != nil || json.Unmarshal(b, &normalizations) != nil {
					t.Fatal("original public normalization inputs unavailable")
				}
				normalize := func(source string) (*string, error) {
					for _, x := range normalizations.Normalizations {
						if source == x.Source {
							return x.Description, nil
						}
					}
					return nil, fmt.Errorf("original visible normalization input changed")
				}
				jobs, e = DiscoverUnisante(context.Background(), o, c.Today, plain, normalize)
			default:
				t.Fatal("unknown public provider")
			}
			if c.Status == "failed" {
				if e == nil || len(jobs) != 0 {
					t.Fatal("original failed public inventory yielded a successful prefix")
				}
				return
			}
			if e != nil || len(jobs) != len(c.Jobs) {
				t.Fatal("original complete public inventory changed", e, len(jobs), len(c.Jobs))
			}
			byURL := map[string]Job{}
			for _, j := range jobs {
				byURL[j.URL] = j
			}
			for _, want := range c.Jobs {
				raw, _ := want["url"].(string)
				j, ok := byURL[raw]
				if !ok {
					t.Fatal("original public URL identity missing")
				}
				b, _ := json.Marshal(j)
				actual := map[string]any{}
				json.Unmarshal(b, &actual)
				if j.Extras != nil {
					actual["language"] = j.Extras["language"]
				}
				for k, v := range want {
					if !reflect.DeepEqual(actual[k], v) {
						t.Fatal("original public field changed", k)
					}
				}
			}
		})
	}
}
