package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strconv"
	"sync"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

type jarviJob51InventoryCase struct {
	Provider, Mode string
	Board          struct {
		URL      string `json:"board_url"`
		Metadata json.RawMessage
	}
	Exchanges        []struct{ URL, Body, Key string }
	Jobs             []map[string]any
	Truncated, Error bool
}

func jarviJob51InventoryCases(t *testing.T) []jarviJob51InventoryCase {
	t.Helper()
	body, e := os.ReadFile("../api-sniffer-monitor/testdata/python_jarvi_job51_inventory.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []jarviJob51InventoryCase
	if json.Unmarshal(body, &cases) != nil || len(cases) != 21 {
		t.Fatal("original inventory corpus unavailable")
	}
	return cases
}

func TestJarviAndJob51OriginalPythonInventory(t *testing.T) {
	for _, c := range jarviJob51InventoryCases(t) {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			d, e := api.Decode(c.Board.Metadata)
			if e != nil {
				t.Fatal(e)
			}
			md := d.Value.(map[string]any)
			exchanges := map[string]string{}
			for _, x := range c.Exchanges {
				if _, ok := exchanges[x.URL]; ok {
					t.Fatal("duplicate fixture exchange")
				}
				exchanges[x.URL] = x.Body
			}
			var lock sync.Mutex
			called := map[string]int{}
			fetch := func(ctx context.Context, r api.Request) ([]byte, error) {
				if ctx.Err() != nil {
					return nil, ctx.Err()
				}
				if r.Method != "GET" {
					t.Error("public inventory changed request method")
				}
				lock.Lock()
				called[r.URL]++
				body, ok := exchanges[r.URL]
				lock.Unlock()
				if !ok {
					t.Errorf("unrequested public resource: %s", r.URL)
					return nil, errors.New("unknown public fixture")
				}
				if c.Provider == "jarvi" && r.Headers.Get("X-Api-Key") != "public_fixture_key" {
					t.Error("public SDK header changed")
				}
				return []byte(body), nil
			}
			var jobs []map[string]any
			var truncated bool
			if c.Provider == "jarvi" {
				jobs, truncated, e = api.DiscoverJarvi(context.Background(), c.Board.URL, md["public_api_key"].(string), md["currency"].(string), fetch)
			} else {
				ctmid, _ := strconv.ParseInt(fmt.Sprint(md["ctmid"]), 10, 64)
				jobs, truncated, e = api.DiscoverJob51(context.Background(), c.Board.URL, ctmid, fetch, enrichment.NormalizeDescriptionHTML)
			}
			if c.Error {
				if e == nil || len(jobs) != 0 {
					t.Fatal("failed complete inventory yielded postings", e)
				}
				return
			}
			if e != nil {
				t.Fatal("valid complete original inventory rejected", e)
			}
			encoded, _ := json.Marshal(jobs)
			var normalized []map[string]any
			if json.Unmarshal(encoded, &normalized) != nil {
				t.Fatal("invalid output")
			}
			if !reflect.DeepEqual(normalized, c.Jobs) || truncated != c.Truncated {
				t.Fatalf("complete original inventory differs\ngot:%s truncated:%v\nwant:%s truncated:%v", encoded, truncated, mustProviderJSON(c.Jobs), c.Truncated)
			}
			if len(called) != len(exchanges) {
				t.Fatal("original inventory requests not exhausted")
			}
			for _, n := range called {
				if n != 1 {
					t.Fatal("public inventory repeated a request")
				}
			}
		})
	}
}
