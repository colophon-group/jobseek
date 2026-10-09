package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"
)

func TestBrassRingStableSnapshotAndLocationHydration(t *testing.T) {
	var corpus struct {
		Fields []struct {
			Provider string
			Row      json.RawMessage
		}
	}
	b, e := os.ReadFile("testdata/python_accenture_brassring.json")
	if e != nil || json.Unmarshal(b, &corpus) != nil {
		t.Fatal("original fixture unavailable")
	}
	var seed json.RawMessage
	for _, c := range corpus.Fields {
		if c.Provider == "brassring" {
			seed = c.Row
			break
		}
	}
	row := func(id string, missing bool) map[string]any {
		var r map[string]any
		json.Unmarshal(seed, &r)
		r["Link"] = "https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25416&siteid=5998&PageType=JobDetails&jobid=" + id
		q := r["Questions"].([]any)
		for _, v := range q {
			m := v.(map[string]any)
			if m["QuestionName"] == "reqid" {
				m["Value"] = id
			}
			if missing && (m["QuestionName"] == "formtext8" || m["QuestionName"] == "formtext9") {
				m["Value"] = ""
			}
		}
		return r
	}
	for _, mode := range []string{"complete", "duplicate", "changed-count", "short", "hydrate-fails", "zero-sort"} {
		t.Run(mode, func(t *testing.T) {
			calls := []string{}
			hydrated := 0
			inv, e := DiscoverBrassRing(context.Background(), BrassRingBoard{"25416", "5998"}, func(_ context.Context, page int, sorted bool) (*Document, error) {
				call := "next"
				if page == 1 {
					call = "initial"
					if sorted {
						call = "sorted"
					}
				}
				calls = append(calls, call)
				total := 2
				rows := []any{row("1", true)}
				if sorted && mode == "zero-sort" {
					total = 0
					rows = []any{}
				}
				if page == 2 {
					rows = []any{row("2", false)}
					if mode == "duplicate" {
						rows = []any{row("1", false)}
					}
					if mode == "changed-count" {
						total = 3
					}
					if mode == "short" {
						rows = []any{}
					}
				}
				b, _ := json.Marshal(map[string]any{"JobsCount": total, "Jobs": map[string]any{"Job": rows}})
				return Decode(b)
			}, func(_ context.Context, j Job) ([]string, error) {
				hydrated++
				if j.Metadata["requisition_id"] != "1" {
					t.Fatal("unnecessary location hydration")
				}
				if mode == "hydrate-fails" {
					return nil, ErrInventory
				}
				return []string{"Evansville, IN"}, nil
			}, func(s string) (*string, error) { return &s, nil })
			if mode == "complete" {
				if e != nil || len(inv.Jobs) != 2 || inv.Truncated || hydrated != 1 || !reflect.DeepEqual(calls, []string{"initial", "sorted", "next"}) {
					t.Fatal("original stable sort/page/hydration contract", e, calls)
				}
			} else if e == nil || len(inv.Jobs) != 0 {
				t.Fatal("incomplete snapshot published")
			}
			if mode == "changed-count" && !errors.Is(e, ErrBrassRingSnapshot) {
				t.Fatal("snapshot retry class lost", e)
			}
			if mode != "complete" && mode != "hydrate-fails" && hydrated != 0 {
				t.Fatal("invalid snapshot performed detail hydration before all identities were validated")
			}
		})
	}
}
