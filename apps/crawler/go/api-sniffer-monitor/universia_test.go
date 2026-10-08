package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestUniversiaMatchesActualPythonIdentityPaginationAndRichFieldComponents(t *testing.T) {
	body, err := os.ReadFile("testdata/python_universia.json")
	if err != nil {
		t.Fatal(err)
	}
	d, err := Decode(body)
	if err != nil {
		t.Fatal(err)
	}
	corpus := d.Value.(map[string]any)
	board := "11111111-1111-4111-8111-111111111111"
	scope := UniversiaScope{BoardID: board, Language: "es"}
	for _, raw := range corpus["scopes"].([]any) {
		c := raw.(map[string]any)
		b, _ := json.Marshal(c["payload"])
		dd, _ := Decode(b)
		got, err := ParseUniversiaScope(dd, TenthProviderOptions{Slug: "sample"})
		if c["error"] != nil {
			if err == nil {
				t.Fatal("invalid board binding accepted", c["name"])
			}
			continue
		}
		want := c["value"].(map[string]any)
		language, _ := want["language"].(string)
		if err != nil || got.BoardID != want["board_id"] || got.Language != language {
			t.Fatal("identity differs", c["name"], got, err)
		}
	}
	for _, raw := range corpus["pages"].([]any) {
		c := raw.(map[string]any)
		b, _ := json.Marshal(c["payload"])
		dd, _ := Decode(b)
		rows, total, err := ParseUniversiaPage(dd, scope, 0)
		if c["error"] != nil {
			if err == nil {
				t.Fatal("invalid inventory accepted", c["name"])
			}
			continue
		}
		n, _ := tenthNonnegative(c["total"])
		b, _ = json.Marshal(rows)
		dd, _ = Decode(b)
		if err != nil || total != n || !reflect.DeepEqual(dd.Value, c["rows"]) {
			t.Fatal("page differs", c["name"], err)
		}
	}
	for _, raw := range corpus["jobs"].([]any) {
		c := raw.(map[string]any)
		row := c["row"].(map[string]any)
		got, err := UniversiaJobFields(row, scope)
		if c["error"] != nil {
			if err == nil {
				t.Fatal("invalid fields accepted", c["name"])
			}
			continue
		}
		want := c["value"].(map[string]any)
		// Pure API extraction preserves these raw signals; the existing rich
		// processor owns canonical enums and internship seniority in worker tests.
		for source, target := range map[string]string{"employmentType": "employment_type", "jobLocationType": "job_location_type"} {
			v, _ := row[source].(string)
			v = strings.Join(strings.Fields(v), " ")
			if v == "" {
				delete(want, target)
			} else {
				want[target] = v
			}
		}
		for key, value := range want {
			if value == nil {
				delete(want, key)
			}
		}
		b, _ := json.Marshal(got)
		dd, _ := Decode(b)
		if err != nil || !reflect.DeepEqual(dd.Value, want) {
			t.Fatal("rich components differ", c["name"], dd.Value, want, err)
		}
	}
}

func TestUniversiaCompleteInventoryRejectsChangedCountsRepeatedPagesAndForeignBoard(t *testing.T) {
	for _, mode := range []string{"complete", "changed-total", "repeated-id", "gap", "foreign-board", "empty", "configured-mismatch"} {
		t.Run(mode, func(t *testing.T) {
			board := "11111111-1111-4111-8111-111111111111"
			o, _ := TenthProviderOptionsFromMetadata("universia", "https://jobboard.universia.net/sample", "{}")
			if mode == "configured-mismatch" {
				o.BoardID = "22222222-2222-4222-8222-222222222222"
			}
			jobs, truncated, err := DiscoverUniversia(context.Background(), o, func(ctx context.Context, r Request) ([]byte, error) {
				if !o.ResourceMatches(r.URL) {
					t.Fatal("foreign API resource")
				}
				u, _ := url.Parse(r.URL)
				if strings.Contains(u.Path, "/config/") {
					return json.Marshal(map[string]any{"slug": "sample", "entity": map[string]any{"id": board, "entityType": "company"}, "languages": []string{"es-CO"}})
				}
				q := u.Query()
				if q.Get("boards") != board || len(q["postingType"]) != 2 {
					t.Fatal("request lost board/posting scope")
				}
				offset, _ := strconv.Atoi(q.Get("offset"))
				total := 101
				if mode == "empty" {
					total = 0
				}
				if mode == "changed-total" && offset == 100 {
					total = 102
				}
				rows := []any{}
				count := total - offset
				if count > 100 {
					count = 100
				}
				for n := 0; n < count; n++ {
					number := offset + n
					if mode == "repeated-id" && offset == 100 {
						number = 0
					}
					id := fmt.Sprintf("22222222-2222-4222-8222-%012d", number)
					scope := board
					if mode == "foreign-board" {
						scope = id
					}
					rows = append(rows, map[string]any{"identifier": id, "boards": []string{scope}, "status": "published", "title": "Engineer", "description": "<p>Build Go.</p>", "url": "https://www.universia.net/es/empleo/" + id + "/engineer.html?referer=" + board})
				}
				if mode == "gap" && offset == 100 {
					rows = []any{}
				}
				return json.Marshal(map[string]any{"offset": offset, "limit": 100, "size": len(rows), "total": total, "totalPages": (total + 99) / 100, "results": rows})
			})
			if mode != "complete" && mode != "empty" {
				if err == nil {
					t.Fatal("partial inventory accepted")
				}
				return
			}
			want := 101
			if mode == "empty" {
				want = 0
			}
			if err != nil || truncated || len(jobs) != want {
				t.Fatal("whole inventory differs", len(jobs), truncated, err)
			}
		})
	}
}
