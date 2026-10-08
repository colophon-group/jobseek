package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"strconv"
	"testing"
)

func TestAutomaticAPIArraySelectionMatchesActualPython(t *testing.T) {
	var cases []struct {
		Name, Endpoint string
		Payload        json.RawMessage
		Candidates     []struct {
			Path  string
			Items []map[string]any
			Score int
		}
		Selected *string
	}
	raw, err := os.ReadFile("testdata/python_auto_path.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 58 {
		t.Fatal("actual Python automatic selection corpus missing", err, len(cases))
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			d, err := Decode(c.Payload)
			if err != nil {
				t.Fatal(err)
			}
			actual, err := d.FindAPIArrayCandidates()
			if err != nil || len(actual) != len(c.Candidates) {
				t.Fatal("candidate traversal differs", err, len(actual), len(c.Candidates))
			}
			for index, candidate := range actual {
				want := c.Candidates[index]
				encoded, _ := json.Marshal(candidate.Items)
				var items []map[string]any
				json.Unmarshal(encoded, &items)
				if candidate.Path != want.Path || !reflect.DeepEqual(items, want.Items) || d.ScoreAPIArray(candidate, c.Endpoint) != want.Score {
					t.Fatal("ordered candidate/score differs", index, candidate.Path, want.Path, d.ScoreAPIArray(candidate, c.Endpoint), want.Score)
				}
			}
			selected, err := d.SelectAPIArray(c.Endpoint)
			if err != nil || (selected == nil) != (c.Selected == nil) || selected != nil && selected.Path != *c.Selected {
				t.Fatal("stable selected path differs", selected, c.Selected, err)
			}
		})
	}
}

func TestAutomaticAPIArrayTraversalLimitsDiscardAllCandidates(t *testing.T) {
	for _, mode := range []string{"depth", "candidates"} {
		t.Run(mode, func(t *testing.T) {
			var body any = []any{map[string]any{"id": "1"}, map[string]any{"id": "2"}, map[string]any{"id": "3"}}
			if mode == "depth" {
				for index := 0; index < 65; index++ {
					body = map[string]any{"wrapped": body}
				}
			} else {
				root := map[string]any{}
				for index := 0; index < 1001; index++ {
					root["jobs"+strconv.Itoa(index)] = body
				}
				body = root
			}
			raw, _ := json.Marshal(body)
			d, err := Decode(raw)
			if err != nil {
				t.Fatal(err)
			}
			candidates, err := d.FindAPIArrayCandidates()
			if err == nil || candidates != nil {
				t.Fatal("resource limit exposed a candidate prefix", len(candidates), err)
			}
		})
	}
}
