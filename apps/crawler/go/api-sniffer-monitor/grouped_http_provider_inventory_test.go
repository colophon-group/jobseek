package apisniffer

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestCuratelyInploiJobConvoOriginalCompleteInventories(t *testing.T) {
	body, err := os.ReadFile("testdata/python_curately_inploi_jobconvo_inventory.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Provider, Mode string
		Board          struct {
			URL      string `json:"board_url"`
			Metadata json.RawMessage
		}
		Exchanges []struct {
			URL, Method, Body string
			Status            int
			RequestBody       string `json:"request_body"`
			Headers           map[string]string
		}
		Jobs             json.RawMessage
		Truncated, Error bool
	}
	if json.Unmarshal(body, &cases) != nil || len(cases) != 36 {
		t.Fatal("original inventory corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			o, err := FinalHTTPProviderOptionsFromMetadata(c.Provider, c.Board.URL, string(c.Board.Metadata))
			if err != nil {
				t.Fatal("original options unavailable", err)
			}
			consumed := make([]bool, len(c.Exchanges))
			var mu sync.Mutex
			fetch := func(ctx context.Context, r Request) ([]byte, error) {
				if err := ctx.Err(); err != nil {
					return nil, err
				}
				mu.Lock()
				defer mu.Unlock()
				for i, x := range c.Exchanges {
					if consumed[i] || r.Method != x.Method || r.URL != x.URL {
						continue
					}
					var got, want any
					if strings.HasPrefix(x.Headers["content-type"], "application/json") {
						if json.Unmarshal([]byte(r.Body), &got) != nil || json.Unmarshal([]byte(x.RequestBody), &want) != nil {
							continue
						}
					} else if strings.HasPrefix(x.Headers["content-type"], "application/x-www-form-urlencoded") {
						got, _ = url.ParseQuery(r.Body)
						want, _ = url.ParseQuery(x.RequestBody)
					} else {
						got, want = r.Body, x.RequestBody
					}
					if !reflect.DeepEqual(got, want) {
						continue
					}
					for k, v := range x.Headers {
						if k == "accept" && v == "*/*" {
							continue
						}
						if r.Headers.Get(k) != v {
							return nil, fmt.Errorf("original %s header differs", k)
						}
					}
					consumed[i] = true
					if x.Status != 200 {
						return nil, ErrInventory
					}
					return []byte(x.Body), nil
				}
				return nil, fmt.Errorf("unmatched original request %s %s", r.Method, r.URL)
			}
			var jobs any
			var truncated bool
			wait := func(context.Context, time.Duration) error { return nil }
			switch c.Provider {
			case "curately":
				jobs, truncated, err = DiscoverCurately(context.Background(), o, fetch, wait)
			case "inploi":
				jobs, truncated, err = DiscoverInploi(context.Background(), o, fetch)
			case "jobconvo":
				jobs, err = DiscoverJobConvo(context.Background(), o, fetch)
			default:
				t.Fatal("unknown original provider")
			}
			if c.Error {
				if err == nil {
					t.Fatal("original failure accepted")
				}
				return
			}
			if err != nil {
				t.Fatal("complete original inventory rejected", err)
			}
			for _, used := range consumed {
				if !used {
					t.Fatal("complete original inventory omitted a request")
				}
			}
			decode := func(value any) any {
				b, e := json.Marshal(value)
				if e != nil {
					t.Fatal(e)
				}
				var v any
				d := json.NewDecoder(bytes.NewReader(b))
				d.UseNumber()
				if d.Decode(&v) != nil {
					t.Fatal("invalid result")
				}
				return v
			}
			if truncated != c.Truncated || !reflect.DeepEqual(decode(jobs), decode(json.RawMessage(c.Jobs))) {
				t.Fatalf("complete original fields/truncation differ: got %s want %s", mustJSON(jobs), mustJSON(c.Jobs))
			}
		})
	}
}
