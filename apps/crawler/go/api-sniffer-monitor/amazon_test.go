package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"sync"
	"testing"
)

func TestAmazonOriginalStreamingPartitionsAndFields(t *testing.T) {
	body, err := os.ReadFile("testdata/python_amazon_stream.json")
	var corpus struct {
		Cases []struct {
			Name      string
			Metadata  map[string]any
			Exchanges []struct {
				URL, Body string
				Status    int
			}
			Chunks          [][]map[string]any
			NativeError     bool `json:"native_error"`
			NativeTruncated bool `json:"native_truncated"`
		}
		Parsing []struct{ Raw, Job map[string]any }
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 6 || len(corpus.Parsing) != 6 {
		t.Fatal("original stream corpus missing")
	}
	canonical := func(raw string) string {
		u, err := url.Parse(raw)
		if err != nil {
			t.Fatal(err)
		}
		u.RawQuery = u.Query().Encode()
		return u.String()
	}
	normal := func(m map[string]any) map[string]any {
		for k, v := range m {
			if v == nil {
				delete(m, k)
			}
		}
		body, _ := json.Marshal(m)
		var out map[string]any
		json.Unmarshal(body, &out)
		return out
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			metadata, _ := json.Marshal(c.Metadata)
			o, err := AmazonOptionsFromMetadata("https://www.amazon.jobs/en/search", string(metadata))
			if err != nil {
				t.Fatal(err)
			}
			responses := map[string]struct {
				body   string
				status int
			}{}
			remaining := map[string]int{}
			for _, x := range c.Exchanges {
				key := canonical(x.URL)
				responses[key] = struct {
					body   string
					status int
				}{x.Body, x.Status}
				remaining[key]++
			}
			var mu sync.Mutex
			fetch := func(ctx context.Context, r Request) ([]byte, error) {
				mu.Lock()
				defer mu.Unlock()
				key := canonical(r.URL)
				x, ok := responses[key]
				if !ok || remaining[key] == 0 || r.Method != "GET" || r.Body != "" {
					return nil, fmt.Errorf("unbound original request")
				}
				remaining[key]--
				if x.status != 200 {
					return nil, fmt.Errorf("original status %d", x.status)
				}
				return []byte(x.body), nil
			}
			got := []map[string]any{}
			truncated, err := DiscoverAmazon(context.Background(), o, fetch, func(batch []map[string]any) error { got = append(got, batch...); return nil })
			if (err != nil) != c.NativeError || truncated != c.NativeTruncated {
				t.Fatal("stream terminal contract differs", err, truncated)
			}
			want := []map[string]any{}
			for _, batch := range c.Chunks {
				want = append(want, batch...)
			}
			if len(got) != len(want) {
				t.Fatal("verified original prefix differs", len(got), len(want))
			}
			for i := range got {
				if !reflect.DeepEqual(normal(got[i]), normal(want[i])) {
					t.Fatal("all original fields differ", i)
				}
			}
			for _, n := range remaining {
				if n != 0 {
					t.Fatal("original query partition or offset omitted")
				}
			}
		})
	}
	for i, c := range corpus.Parsing {
		got, err := AmazonJobFields(c.Raw)
		if err != nil || !reflect.DeepEqual(normal(got), normal(c.Job)) {
			t.Fatal("original field/date/salary mapping differs", i, err)
		}
	}
}

func TestAmazonResourceAndTransportBoundaries(t *testing.T) {
	o, err := AmazonOptionsFromMetadata("https://www.amazon.jobs/en/search", `{"country":"USA","category":"software-development"}`)
	if err != nil || !o.ResourceMatches(o.InitialURL()) {
		t.Fatal("configured initial query not bound", err)
	}
	for _, raw := range []string{"http://www.amazon.jobs/en/search.json?offset=0&result_limit=100&sort=recent", "https://www.amazon.jobs/en/search.json?offset=10000&result_limit=100&sort=recent", "https://www.amazon.jobs/en/search.json?offset=0&result_limit=100&sort=recent&country=CHE&category%5B%5D=software-development", "https://www.amazon.jobs/en/search.json?offset=0&offset=0&result_limit=100&sort=recent", "https://foreign.example.test/en/search.json", "https://www.amazon.jobs/en/search.json?offset=0&result_limit=100&sort=recent&callback=arbitrary"} {
		if o.ResourceMatches(raw) {
			t.Fatal("foreign or unbounded resource accepted")
		}
	}
	for _, metadata := range []string{`{"proxy":true}`, `{"country":"US"}`, `{"category":"../secret"}`, `{"business_category":42}`} {
		if _, err := AmazonOptionsFromMetadata("https://www.amazon.jobs/en/search", metadata); err == nil {
			t.Fatal("unsupported options accepted")
		}
	}
}
