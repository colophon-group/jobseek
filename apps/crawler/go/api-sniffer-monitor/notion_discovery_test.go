package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestNotionLogicalAPIRequestsAndInventoriesMatchActualPython(t *testing.T) {
	var corpus []struct {
		Name, Board          string
		Metadata             map[string]any
		Public, Chunk, Query json.RawMessage
		URLs                 []string
		Error                bool
		Calls                []struct {
			URL     string
			Payload map[string]any
		} `json:"logical_calls"`
	}
	raw, err := os.ReadFile("testdata/python_notion_monitor.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus) != 14 {
		t.Fatal("frozen actual Python API corpus missing", err)
	}
	for _, c := range corpus {
		t.Run(c.Name, func(t *testing.T) {
			metadata, _ := json.Marshal(c.Metadata)
			o, err := NotionOptionsFromMetadata(c.Board, string(metadata))
			if err != nil {
				t.Fatal(err)
			}
			step := 0
			urls, err := DiscoverNotion(context.Background(), o, func(_ context.Context, request Request) (*Document, int, error) {
				if step >= len(c.Calls) {
					t.Fatal("unexpected API call", request.URL)
				}
				expected := c.Calls[step]
				step++
				var body map[string]any
				if json.Unmarshal([]byte(request.Body), &body) != nil || request.Method != "POST" || request.Headers.Get("Content-Type") != "application/json" || request.URL != expected.URL || !reflect.DeepEqual(body, expected.Payload) {
					t.Fatal(request, expected, body)
				}
				if strings.HasSuffix(request.URL, "getPublicPageData") {
					if !strings.Contains(request.URL, "www.notion.so") && (c.Name == "root-canonical-fallback" || c.Name == "explicit-no-fallback") {
						return nil, 500, ErrInventory
					}
					if c.Name == "root-403" {
						return nil, 403, ErrInventory
					}
					d, err := Decode(c.Public)
					return d, 200, err
				}
				if strings.HasSuffix(request.URL, "loadPageChunk") {
					if c.Name == "explicit-home-fallback" && asEmbeddedObject(body["page"])["id"] == "22222222-2222-2222-2222-222222222222" {
						d, err := Decode([]byte(`{"recordMap":{"block":{"22222222-2222-2222-2222-222222222222":{"value":{"type":"page","properties":{"title":[["No jobs"]]},"content":[]}}}}}`))
						return d, 200, err
					}
					d, err := Decode(c.Chunk)
					return d, 200, err
				}
				if c.Name == "late-collection-failure" {
					return nil, 503, ErrInventory
				}
				d, err := Decode(c.Query)
				return d, 200, err
			})
			if (err != nil) != c.Error || err != nil && len(urls) != 0 || step != len(c.Calls) {
				t.Fatal(urls, err, step, len(c.Calls))
			}
			if !c.Error && !reflect.DeepEqual(urls, c.URLs) {
				t.Fatal(urls, c.URLs)
			}
		})
	}
}

func TestNotionChunkCursorCompletesRecordsAndRejectsRepeatedOrMissingGraph(t *testing.T) {
	o, _ := NotionOptionsFromMetadata("https://fixture.notion.site/", "{}")
	page := "11111111-1111-1111-1111-111111111111"
	job := "22222222-2222-2222-2222-222222222222"
	for _, repeated := range []bool{false, true} {
		calls := 0
		d, err := LoadNotionChunk(context.Background(), o, page, func(_ context.Context, request Request) (*Document, int, error) {
			calls++
			var payload map[string]any
			if json.Unmarshal([]byte(request.Body), &payload) != nil || payload["chunkNumber"] != float64(calls-1) {
				t.Fatal("cursor chunk did not advance")
			}
			if calls == 1 {
				value, err := Decode([]byte(`{"cursor":{"stack":[{"id":"continuation"}]},"recordMap":{"block":{"` + page + `":{"value":{"type":"page","content":["` + job + `"]}}}}}`))
				return value, 200, err
			}
			cursor := `{"stack":[]}`
			if repeated {
				cursor = `{"stack":[{"id":"continuation"}]}`
			}
			value, err := Decode([]byte(`{"cursor":` + cursor + `,"recordMap":{"block":{"` + job + `":{"value":{"type":"page","properties":{"title":[["Engineer"]]}}}}}}`))
			return value, 200, err
		})
		if repeated {
			if err == nil || d != nil || calls != 2 {
				t.Fatal("repeated cursor accepted", d, err, calls)
			}
			continue
		}
		if err != nil || calls != 2 {
			t.Fatal(err, calls)
		}
		pages, err := NotionChildPages(d, page, false)
		if err != nil || len(pages) != 1 || pages[0].ID != job || pages[0].Title != "Engineer" {
			t.Fatal(pages, err)
		}
	}
}
