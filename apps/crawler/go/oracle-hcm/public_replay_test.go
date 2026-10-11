package oraclehcm

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

// Original complete captures are protected operator evidence, never repository
// fixtures. Compare every field and request, including the original retry.
func TestOracleRemainingOriginalPublicInventories(t *testing.T) {
	directory := os.Getenv("JOBSEEK_FINAL_TYPES_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires protected complete original Oracle captures")
	}
	for _, slug := range []string{"inova-careers", "conduent-careers"} {
		t.Run(slug, func(t *testing.T) {
			body, e := os.ReadFile(filepath.Join(directory, "native1010-final-"+slug+"-original-public-capture1-2026-10-11.json"))
			var c struct {
				Status    string
				Truncated bool
				Board     struct {
					URL      string `json:"board_url"`
					Metadata map[string]any
				}
				Jobs []struct {
					URL            string
					Title          any
					Locations      []string
					DatePosted     any `json:"date_posted"`
					EmploymentType any `json:"employment_type"`
				}
				Exchanges []struct {
					URL, Method string
					Status      int
					Body        string `json:"body_base64"`
				}
			}
			if e != nil || json.Unmarshal(body, &c) != nil || c.Status != "completed-original-stream" || c.Truncated {
				t.Fatal("original complete inventory required")
			}
			o, e := OptionsFromMetadata(c.Board.URL, c.Board.Metadata)
			if e != nil {
				t.Fatal(e)
			}
			cursor := 0
			fetch := func(ctx context.Context, endpoint string) ([]byte, error) {
				for {
					if cursor >= len(c.Exchanges) {
						t.Fatal("request beyond original complete sequence")
					}
					x := c.Exchanges[cursor]
					cursor++
					u, e := url.Parse(endpoint)
					v, f := url.Parse(x.URL)
					if e != nil || f != nil || u.Scheme != v.Scheme || u.Host != v.Host || u.Path != v.Path || !reflect.DeepEqual(u.Query(), v.Query()) || x.Method != "GET" {
						t.Fatalf("original request differs at index%d", cursor-1)
					}
					// The worker retries transient403; the SDK receives its successful page.
					if x.Status == 403 {
						continue
					}
					if x.Status != 200 {
						t.Fatal("unproved original status")
					}
					raw, e := base64.StdEncoding.DecodeString(x.Body)
					if e != nil {
						t.Fatal(e)
					}
					return raw, nil
				}
			}
			got, e := Discover(context.Background(), o, fetch)
			if e != nil || got.Truncated || len(got.Jobs) != len(c.Jobs) || cursor != len(c.Exchanges) {
				t.Fatalf("complete inventory changed: %v jobs%d/%d requests%d/%d", e, len(got.Jobs), len(c.Jobs), cursor, len(c.Exchanges))
			}
			sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
			sort.Slice(c.Jobs, func(i, j int) bool { return c.Jobs[i].URL < c.Jobs[j].URL })
			for i, want := range c.Jobs {
				actual := got.Jobs[i]
				if actual.URL != want.URL || !reflect.DeepEqual(actual.Title, want.Title) || !reflect.DeepEqual(actual.Locations, want.Locations) || !reflect.DeepEqual(actual.DatePosted, want.DatePosted) || !reflect.DeepEqual(actual.EmploymentType, want.EmploymentType) {
					t.Fatalf("original job fields changed at index%d", i)
				}
			}
		})
	}
}
