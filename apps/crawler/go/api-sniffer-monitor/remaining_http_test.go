package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"sort"
	"strconv"
	"testing"
)

type remainingFixtureStatus int

func (s remainingFixtureStatus) Error() string       { return "fixture HTTP status" }
func (s remainingFixtureStatus) HTTPStatusCode() int { return int(s) }

func TestRemainingHTTPOriginalInventories(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_remaining_http_inventory.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Provider, Mode string
		Board          struct {
			URL      string `json:"board_url"`
			Metadata json.RawMessage
		}
		Exchanges []struct {
			Method, URL, Body string
			RequestBody       string `json:"request_body"`
			Headers           map[string]string
			Status            int
		}
		Jobs             json.RawMessage
		Truncated, Error bool
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 29 {
		t.Fatal("original corpus unavailable")
	}
	if directory := os.Getenv("JOBSEEK_REMAINING_HTTP_PUBLIC_CAPTURE_DIR"); directory != "" {
		for _, slug := range []string{"city-of-montreux-careers", "fei-careers", "kraft-heinz-careers-ru", "mindlance-job-board", "ntt-data-it-contract", "sucden-russia-hh"} {
			body, e := os.ReadFile(directory + "/native999-remaining-http-" + slug + "-public-capture1-2026-10-10.json")
			if e != nil {
				t.Fatal("private public capture missing", slug)
			}
			extra := cases[:0:0]
			if json.Unmarshal(append(append([]byte{'['}, body...), ']'), &extra) != nil || len(extra) != 1 {
				t.Fatal("private capture invalid", slug)
			}
			cases = append(cases, extra[0])
		}
	}
	for _, c := range cases {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			o, e := RemainingHTTPOptionsFromMetadata(c.Provider, c.Board.URL, string(c.Board.Metadata))
			if e != nil {
				t.Fatal(e)
			}
			used := make([]bool, len(c.Exchanges))
			fetch := func(ctx context.Context, r Request) ([]byte, error) {
				if ctx.Err() != nil {
					return nil, ctx.Err()
				}
				if !o.ResourceMatches(r.URL) {
					return nil, ErrOptions
				}
				for i, x := range c.Exchanges {
					gotURL, _ := url.Parse(r.URL)
					wantURL, _ := url.Parse(x.URL)
					if used[i] || r.Method != x.Method || gotURL.Scheme != wantURL.Scheme || gotURL.Host != wantURL.Host || gotURL.Path != wantURL.Path || !reflect.DeepEqual(gotURL.Query(), wantURL.Query()) {
						continue
					}
					gotBody, _ := url.ParseQuery(r.Body)
					wantBody, _ := url.ParseQuery(x.RequestBody)
					if !reflect.DeepEqual(gotBody, wantBody) {
						continue
					}
					for _, key := range []string{"accept", "user-agent", "content-type", "authorization", "portalid", "a", "compid", "token", "referer"} {
						if key == "user-agent" && c.Provider != "headhunter" || key == "accept" && r.Headers.Get(key) == "" {
							continue
						}
						if value := x.Headers[key]; value != "" && r.Headers.Get(key) != value {
							return nil, fmt.Errorf("original %s header changed", key)
						}
					}
					used[i] = true
					if x.Status != 200 {
						return nil, remainingFixtureStatus(x.Status)
					}
					return []byte(x.Body), nil
				}
				return nil, errors.New("original request mismatch")
			}
			var jobs any
			truncated := false
			switch c.Provider {
			case "johdi":
				jobs, truncated, e = DiscoverJohdi(context.Background(), o, fetch)
			case "jobdiva":
				jobs, truncated, e = DiscoverJobDiva(context.Background(), o, fetch)
			case "headhunter":
				var fields []map[string]any
				fields, truncated, e = DiscoverHeadHunter(context.Background(), o, fetch)
				sort.Slice(fields, func(i, j int) bool { return fields[i]["url"].(string) < fields[j]["url"].(string) })
				jobs = fields
			}
			if c.Error {
				if e == nil {
					t.Fatal("original failure accepted")
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			encoded, _ := json.Marshal(jobs)
			var got, want any
			json.Unmarshal(encoded, &got)
			json.Unmarshal(c.Jobs, &want)
			if !reflect.DeepEqual(got, want) || truncated != c.Truncated {
				t.Fatal("original inventory/fields/truncation changed", c.Mode)
			}
			for _, consumed := range used {
				if !consumed {
					t.Fatal("original pagination exchange skipped")
				}
			}
		})
	}
}

func TestHeadHunterOriginalFields(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_remaining_http_fields.json")
	if e != nil {
		t.Fatal(e)
	}
	d, e := Decode(raw)
	if e != nil {
		t.Fatal(e)
	}
	for _, value := range d.Value.([]any) {
		c := value.(map[string]any)
		t.Run(c["mode"].(string), func(t *testing.T) {
			fields, e := HeadHunterFields(c["row"].(map[string]any), "42", "hh.ru")
			if c["expected"] == nil {
				if e == nil {
					t.Fatal("foreign identity accepted")
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			got, _ := json.Marshal(fields)
			want, _ := json.Marshal(c["expected"])
			var a, b any
			json.Unmarshal(got, &a)
			json.Unmarshal(want, &b)
			if !reflect.DeepEqual(a, b) {
				t.Fatalf("original fields changed: %s != %s", got, want)
			}
		})
	}
}

func TestRemainingHTTPOriginalDetailFields(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_remaining_http_detail_fields.json")
	if e != nil {
		t.Fatal(e)
	}
	d, e := Decode(raw)
	if e != nil {
		t.Fatal(e)
	}
	for _, value := range d.Value.([]any) {
		c := value.(map[string]any)
		t.Run(c["provider"].(string)+"/"+c["mode"].(string), func(t *testing.T) {
			row := c["row"].(map[string]any)
			var fields map[string]any
			var e error
			if c["provider"] == "johdi" {
				fields, e = JohdiDetailFields(row, "23", "fr-CH")
			} else {
				fields, e = HeadHunterDetailFields(row, "101", "hh.ru")
			}
			if c["error"] == true {
				if e == nil {
					t.Fatal("original detail failure accepted")
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			got, _ := json.Marshal(fields)
			want, _ := json.Marshal(c["expected"])
			var a, b any
			json.Unmarshal(got, &a)
			json.Unmarshal(want, &b)
			if !reflect.DeepEqual(a, b) {
				t.Fatalf("original detail fields changed: %s != %s", got, want)
			}
		})
	}
}

func TestJohdiAboveCapRetainsInventoryAndSuppressesAbsence(t *testing.T) {
	o, e := RemainingHTTPOptionsFromMetadata("johdi", "https://employer.example/careers", `{"company_key":"synthetic_company_key_12345","flow":"web","locale":"fr"}`)
	if e != nil {
		t.Fatal(e)
	}
	rows := make([]map[string]int, 50001)
	for i := range rows {
		rows[i] = map[string]int{"id": i + 1}
	}
	body, e := json.Marshal(rows)
	if e != nil {
		t.Fatal(e)
	}
	fetch := func(_ context.Context, r Request) ([]byte, error) {
		if r.URL == o.BoardURL {
			return []byte(`<div id="ats-offers" data-company-hash-key="synthetic_company_key_12345" data-flow="web" data-locale="fr"></div>`), nil
		}
		if r.URL == o.JohdiListURL() {
			return body, nil
		}
		return nil, ErrOptions
	}
	urls, truncated, e := DiscoverJohdi(context.Background(), o, fetch)
	if e != nil || !truncated || len(urls) != 50001 {
		t.Fatal("above-cap inventory was sliced or granted absence authority", e)
	}
	seen := map[string]bool{}
	for _, source := range urls {
		seen[source] = true
	}
	for _, id := range []int{1, 50000, 50001} {
		if !seen[o.BoardURL+"#/offer/"+strconv.Itoa(id)+"/job"] {
			t.Fatal("collected tail was lost")
		}
	}
}
