package apisniffer

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

func TestSuccessFactorsRMKActualOriginalPublicFields(t *testing.T) {
	directory := os.Getenv("JOBSEEK_RMK_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires private immutable original RSS public captures")
	}
	for _, slug := range []string{"zhengda-group-careers-my-cpf", "zhengda-group-careers-vn-cpf", "zhengda-group-careers-th-cpf"} {
		t.Run(slug, func(t *testing.T) {
			raw, e := os.ReadFile(filepath.Join(directory, "native1005-rss-group-"+slug+"-original-public-capture1-2026-10-10.json"))
			if e != nil {
				t.Fatal("original public oracle unavailable")
			}
			var c struct {
				Status    string
				Truncated bool
				Board     struct {
					URL      string `json:"board_url"`
					Metadata json.RawMessage
				}
				Jobs      []Job
				Exchanges []struct {
					Method, URL, Body string
					RequestBody       string `json:"request_body"`
					Headers           map[string]string
				}
			}
			d := json.NewDecoder(bytes.NewReader(raw))
			d.UseNumber()
			if d.Decode(&c) != nil || c.Status != "complete" || len(c.Jobs) == 0 || len(c.Exchanges) < 2 {
				t.Fatal("original complete RMK oracle unavailable")
			}
			o, e := SuccessFactorsRMKOptionsFromMetadata(c.Board.URL, string(c.Board.Metadata))
			if e != nil {
				t.Fatal("canonical RMK configuration rejected")
			}
			at := 0
			found, e := DiscoverSuccessFactorsRMK(context.Background(), o, func(_ context.Context, r Request) ([]byte, error) {
				if at >= len(c.Exchanges) {
					t.Fatal("unsolicited original request")
				}
				x := c.Exchanges[at]
				at++
				if r.Method != x.Method || r.URL != x.URL || r.Body != x.RequestBody {
					t.Fatal("original RMK request changed")
				}
				if r.Method == "POST" {
					for _, key := range []string{"Content-Type", "Referer", "X-Csrf-Token"} {
						if r.Headers.Get(key) != x.Headers[map[string]string{"Content-Type": "content-type", "Referer": "referer", "X-Csrf-Token": "x-csrf-token"}[key]] {
							t.Fatal("original authenticated request header changed")
						}
					}
				}
				return []byte(x.Body), nil
			})
			for i := range c.Jobs {
				if c.Jobs[i].Metadata == nil {
					c.Jobs[i].Metadata = map[string]any{}
				}
				if c.Jobs[i].Extras == nil {
					c.Jobs[i].Extras = map[string]any{}
				}
			}
			sort.Slice(c.Jobs, func(i, j int) bool { return c.Jobs[i].URL < c.Jobs[j].URL })
			sort.Slice(found.Jobs, func(i, j int) bool { return found.Jobs[i].URL < found.Jobs[j].URL })
			if e != nil || at != len(c.Exchanges) || found.Truncated != c.Truncated || !reflect.DeepEqual(found.Jobs, c.Jobs) {
				t.Fatal("original complete RMK fields or duplicate truncation changed", "native_count", len(found.Jobs), "original_count", len(c.Jobs), "error", e)
			}
			t.Log("all original fields, requests and duplicate truncation match", "jobs", len(found.Jobs), "truncated", found.Truncated)
		})
	}
}

func TestSuccessFactorsRMKOriginalSyntheticInventory(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_successfactors_rmk.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name      string
		Board     string `json:"board_url"`
		Metadata  json.RawMessage
		Responses []string
		Requests  []struct {
			Method, URL, Body string
			Headers           map[string]string
		}
		Jobs             []Job
		Truncated, Error bool
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if decoder.Decode(&cases) != nil || len(cases) != 16 {
		t.Fatal("original RMK fixture unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			opts, err := SuccessFactorsRMKOptionsFromMetadata(c.Board, string(c.Metadata))
			if err != nil {
				t.Fatal(err)
			}
			at := 0
			found, err := DiscoverSuccessFactorsRMK(context.Background(), opts, func(_ context.Context, r Request) ([]byte, error) {
				if at >= len(c.Requests) {
					t.Fatal("unsolicited request")
				}
				want := c.Requests[at]
				if r.Method != want.Method || r.URL != want.URL || r.Body != want.Body {
					t.Fatal("original request changed")
				}
				for k, v := range want.Headers {
					if r.Headers.Get(k) != v {
						t.Fatal("original request header changed", k)
					}
				}
				body := c.Responses[at]
				at++
				return []byte(body), nil
			})
			if (err != nil) != c.Error || at != len(c.Requests) {
				t.Fatal("original inventory classification/request count changed", err)
			}
			if c.Error {
				if len(found.Jobs) != 0 {
					t.Fatal("failed prefix gained publication authority")
				}
				return
			}
			for i := range c.Jobs {
				if c.Jobs[i].Extras == nil {
					c.Jobs[i].Extras = map[string]any{}
				}
				if c.Jobs[i].Metadata == nil {
					c.Jobs[i].Metadata = map[string]any{}
				}
			}
			if found.Truncated != c.Truncated || !reflect.DeepEqual(found.Jobs, c.Jobs) {
				t.Fatal("original fields or duplicate handling changed")
			}
		})
	}
}
func TestSuccessFactorsRMKRejectsForgedResourceBinding(t *testing.T) {
	opts, err := SuccessFactorsRMKOptionsFromMetadata("https://example.com/Fixture/jobs", `{"preset":"successfactors","variant":"rmk","brand":"Fixture"}`)
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"origin", "endpoint", "brand", "locale", "board"} {
		t.Run(key, func(t *testing.T) {
			forged := opts
			switch key {
			case "origin":
				forged.Origin = "https://other.example"
			case "endpoint":
				forged.Endpoint += "?extra=1"
			case "brand":
				forged.Brand = "Other"
			case "locale":
				forged.Locale = "bad"
			case "board":
				forged.BoardURL = "http://example.com/Fixture/jobs"
			}
			_, err := DiscoverSuccessFactorsRMK(context.Background(), forged, func(context.Context, Request) ([]byte, error) { t.Fatal("forged binding fetched"); return nil, nil })
			if err == nil {
				t.Fatal("forged binding accepted")
			}
		})
	}
}
