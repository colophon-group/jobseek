package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestPracticeMatchActualPythonParsersFormsAndTraversal(t *testing.T) {
	var corpus struct {
		Parsers []struct {
			Name, Body string
			Hidden     map[string]string
			URLs       []string
		}
		Forms []struct {
			Hidden           map[string]string
			Profession, Body string
			Page             int
		}
		Inventories []struct {
			Name, Board       string
			Metadata          map[string]any
			URLs              []string
			Truncated, Failed bool
			Calls             []struct {
				Method, URL, Body string
				Headers           map[string]string
			}
		}
	}
	raw, err := os.ReadFile("testdata/python_practicematch.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Parsers) != 12 || len(corpus.Forms) != 8 || len(corpus.Inventories) != 6 {
		t.Fatal("Python corpus missing", err)
	}
	for _, c := range corpus.Parsers {
		t.Run("parse/"+c.Name, func(t *testing.T) {
			hidden, urls, err := ParsePracticeMatchLanding([]byte(c.Body))
			if err != nil || !reflect.DeepEqual(hidden, c.Hidden) || !reflect.DeepEqual(urls, c.URLs) {
				t.Fatal(hidden, urls, err, c.Hidden, c.URLs)
			}
		})
	}
	o, err := PracticeMatchOptionsFromMetadata("https://employer.practicematch.com/employer/fixture/", `{"proxy":true}`)
	if err != nil {
		t.Fatal(err)
	}
	for i, c := range corpus.Forms {
		r, err := PracticeMatchPageRequest(o, c.Hidden, c.Profession, c.Page)
		if err != nil || r.Body != c.Body {
			t.Fatal(i, r.Body, c.Body, err)
		}
	}
	for _, c := range corpus.Inventories {
		t.Run("inventory/"+c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Metadata)
			o, err := PracticeMatchOptionsFromMetadata(c.Board, string(md))
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			out, err := DiscoverPracticeMatch(context.Background(), o, func(ctx context.Context, r Request) ([]byte, error) {
				if calls >= len(c.Calls) {
					return nil, errors.New("unexpected native request")
				}
				want := c.Calls[calls]
				calls++
				if r.URL != want.URL || r.Method != want.Method || r.Body != want.Body {
					t.Error("request differs", r, want)
				}
				for k, v := range want.Headers {
					if r.Headers.Get(k) != v {
						t.Error("request header differs", k)
					}
				}
				if r.Method == "GET" {
					if c.Name == "missing-facility" {
						return []byte("<p>No facility</p>"), nil
					}
					body := `<input id="facilityID" value="42"><input id="facilityLandingURL" value="A &amp; B">`
					if c.Name != "empty" && c.Name != "cap" {
						body += practiceFixtureLink("1")
					}
					return []byte(body), nil
				}
				if c.Name == "late-failure" && strings.Contains(r.Body, "professionID=-1") {
					return nil, errors.New("fixture later resource failure")
				}
				rows := ""
				if c.Name == "cap" {
					if strings.Contains(r.Body, "professionID=1") {
						rows = practiceFixtureLink("2")
					} else {
						rows = practiceFixtureLink("3")
					}
				} else if c.Name == "complete" || c.Name == "late-failure" {
					if strings.Contains(r.Body, "professionID=1") && strings.Contains(r.Body, "pageNum=2") {
						rows = practiceFixtureLink("2")
					}
					if strings.Contains(r.Body, "professionID=-1") && strings.Contains(r.Body, "pageNum=1") {
						rows = practiceFixtureLink("3")
					}
				} else if c.Name == "repeat" {
					rows = practiceFixtureLink("1")
				}
				return json.Marshal(map[string]any{"OPPLISTINGSHTML": rows})
			})
			if (err != nil) != c.Failed || err == nil && (!reflect.DeepEqual(out.URLs, c.URLs) || out.Truncated != c.Truncated) || calls != len(c.Calls) {
				t.Fatal(out, err, c.URLs, c.Truncated, calls, len(c.Calls))
			}
		})
	}
}

func practiceFixtureLink(id string) string {
	return `<a href="https://www.practicematch.com/physicians/job-details.cfm/` + id + `/role?tracking=1">Job</a>`
}
