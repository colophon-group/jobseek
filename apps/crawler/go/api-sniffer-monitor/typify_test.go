package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"testing"
)

func TestTypifyMatchesActualPythonPartitionsCountersAndFields(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_typify.json")
	if err != nil {
		t.Fatal(err)
	}
	d, err := Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	corpus := d.Value.(map[string]any)
	for _, value := range corpus["pages"].([]any) {
		c := value.(map[string]any)
		o, err := TenthProviderOptionsFromMetadata("typify", c["board"].(string), "{}")
		if err != nil {
			t.Fatal(err)
		}
		got, err := ParseTypifyPageConfig(c["body"].(string), o)
		if c["value"] == nil {
			if err == nil {
				t.Fatal("missing live partitions accepted")
			}
			continue
		}
		want := c["value"].(map[string]any)
		ids := []string{}
		for _, v := range want["function_ids"].([]any) {
			ids = append(ids, v.(string))
		}
		if err != nil || got.APIURL != want["api_url"] || !reflect.DeepEqual(got.Functions, ids) {
			t.Fatal("live API route or function partitions changed", c["name"], got, err)
		}
	}
	for _, value := range corpus["counts"].([]any) {
		c := value.(map[string]any)
		body, _ := json.Marshal(c["payload"])
		doc, _ := Decode(body)
		rows, total, err := TypifyPartitionRows(doc)
		if c["error"] != nil {
			if err == nil {
				t.Fatal("unproved partition accepted", c["name"])
			}
			continue
		}
		want, _ := tenthNonnegative(c["total"])
		if err != nil || total != want || !reflect.DeepEqual(rows, c["rows"]) {
			t.Fatal("partition total or rows changed", c["name"], total, err)
		}
	}
	for _, value := range corpus["jobs"].([]any) {
		c := value.(map[string]any)
		got, err := TypifyJobFields(c["row"], "https://example.com/jobs")
		if c["value"] == nil {
			if err == nil {
				t.Fatal("invalid job accepted")
			}
			continue
		}
		want := c["value"].(map[string]any)
		var locations []string
		if values, ok := want["locations"].([]any); ok {
			for _, value := range values {
				locations = append(locations, value.(string))
			}
		}
		want["locations"] = locations
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatal("public job fields or URL identity changed", got, want, err)
		}
	}
}

func TestTypifyPartitionsPreserveWholeUnionAndRejectConflictingOrPartialData(t *testing.T) {
	for _, mode := range []string{"complete", "gap", "conflict", "over-cap-single"} {
		t.Run(mode, func(t *testing.T) {
			o, _ := TenthProviderOptionsFromMetadata("typify", "https://example.com/jobs", "{}")
			calls := 0
			jobs, truncated, err := DiscoverTypify(context.Background(), o, func(ctx context.Context, r Request) ([]byte, error) {
				calls++
				if !o.ResourceMatches(r.URL) {
					t.Fatal("unbound public resource")
				}
				if r.Method == "GET" {
					return []byte(`window.typify={};<input class='cb-function' data-id='1'><input class='cb-function' data-id='2'>`), nil
				}
				v, _ := url.ParseQuery(r.Body)
				if v.Get("map") == "0" {
					return []byte(`{"pagination":{"total":2002}}`), nil
				}
				ids := v["field_function[]"]
				if len(ids) != 1 {
					t.Fatal("partition protocol lost live IDs")
				}
				rows := []any{}
				for n := 0; n < 1001; n++ {
					id := ids[0]
					if mode == "conflict" {
						id = "same"
					}
					rows = append(rows, map[string]any{"url": fmt.Sprintf("/job/%s-%d", id, n), "title": "Engineer " + ids[0], "location": map[string]any{"label": "Rotterdam"}})
				}
				total, pages := 1001, 1
				if mode == "gap" && ids[0] == "2" {
					total = 1000
					rows = rows[:1000]
				}
				if mode == "over-cap-single" {
					pages = 2
				}
				body, _ := json.Marshal(map[string]any{"pagination": map[string]any{"total": total, "total_pages": pages}, "results": rows})
				return body, nil
			})
			if mode == "complete" {
				if err != nil || truncated || len(jobs) != 2002 || calls != 4 {
					t.Fatal("partition union lost", len(jobs), calls, err)
				}
			} else if err == nil || len(jobs) != 0 {
				t.Fatal("partial or conflicting partition succeeded", len(jobs), err)
			}
		})
	}
}
