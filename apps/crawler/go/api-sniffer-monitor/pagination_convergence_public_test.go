package apisniffer

import (
	"bytes"
	"context"
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

func TestAPIConvergenceOriginalPublicInventoryAndRequests(t *testing.T) {
	directory := os.Getenv("JOBSEEK_API_CONVERGENCE_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires private original complete HTTP convergence captures")
	}
	for _, slug := range []string{"bupa-saudi-arabia", "publicis-main"} {
		t.Run(slug, func(t *testing.T) {
			body, err := os.ReadFile(filepath.Join(directory, "native1008-api-convergence-"+slug+"-original-public-capture2-2026-10-10.json"))
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
					Status            int
				}
			}
			decoder := json.NewDecoder(bytes.NewReader(body))
			decoder.UseNumber()
			if err != nil || decoder.Decode(&c) != nil || c.Status != "complete" {
				t.Fatal("original capture missing or incomplete")
			}
			o, err := OptionsFromMetadata(c.Board.URL, string(c.Board.Metadata))
			if err != nil {
				t.Fatal("configured convergence rejected", err)
			}
			cursor := 0
			fetch := func(ctx context.Context, r Request) (*Document, error) {
				if cursor >= len(c.Exchanges) {
					t.Fatal("native exceeded original complete request sequence")
				}
				x := c.Exchanges[cursor]
				cursor++
				u, e := url.Parse(r.URL)
				v, f := url.Parse(x.URL)
				if e != nil || f != nil || u.Scheme != v.Scheme || u.Host != v.Host || u.EscapedPath() != v.EscapedPath() || !reflect.DeepEqual(u.Query(), v.Query()) || r.Method != x.Method || r.Body != x.RequestBody || x.Status != 200 {
					t.Fatalf("original request differs at index %d", cursor-1)
				}
				return Decode([]byte(x.Body))
			}
			join := func(base, ref string) (string, error) {
				u, e := url.Parse(base)
				if e != nil {
					return "", e
				}
				v, e := url.Parse(ref)
				if e != nil {
					return "", e
				}
				return u.ResolveReference(v).String(), nil
			}
			actual, err := Discover(context.Background(), o, fetch, join)
			if err != nil || cursor != len(c.Exchanges) || actual.Truncated != c.Truncated || len(actual.Jobs) != len(c.Jobs) {
				t.Fatalf("inventory/count/truncation/requests differ: error=%v jobs=%d/%d requests=%d/%d truncated=%v/%v", err, len(actual.Jobs), len(c.Jobs), cursor, len(c.Exchanges), actual.Truncated, c.Truncated)
			}
			sort.Slice(actual.Jobs, func(i, j int) bool { return actual.Jobs[i].URL < actual.Jobs[j].URL })
			for i := range c.Jobs {
				if c.Jobs[i].Metadata == nil {
					c.Jobs[i].Metadata = map[string]any{}
				}
				if c.Jobs[i].Extras == nil {
					c.Jobs[i].Extras = map[string]any{}
				}
				if !reflect.DeepEqual(actual.Jobs[i], c.Jobs[i]) {
					t.Fatalf("original fields differ at job index %d", i)
				}
			}
		})
	}
}
