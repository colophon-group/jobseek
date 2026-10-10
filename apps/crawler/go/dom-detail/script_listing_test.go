package dom

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func TestOriginalPythonInlineListingParity(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_inline_listings.json")
	if err != nil {
		t.Fatal(err)
	}
	type job struct {
		URL       string   `json:"url"`
		Title     string   `json:"title,omitempty"`
		Locations []string `json:"locations,omitempty"`
	}
	var cases []struct {
		Name, Source, Base, Include string
		Config                      map[string]any
		Error                       bool
		Jobs                        []job
	}
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.UseNumber()
	if err := d.Decode(&cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			config, err := ScriptLinksOptions(c.Config)
			var rows []RichRowJob
			if err == nil {
				rows, err = ParseScriptLinks(context.Background(), c.Source, c.Base, config, c.Include)
			}
			if (err != nil) != c.Error {
				t.Fatalf("failure=%v want %v", err, c.Error)
			}
			if c.Error {
				return
			}
			got := make([]job, 0, len(rows))
			for _, r := range rows {
				got = append(got, job{r.URL, r.Title, r.Locations})
			}
			if !config.Rich() {
				sort.Slice(got, func(i, j int) bool { return got[i].URL < got[j].URL })
			}
			if !reflect.DeepEqual(got, c.Jobs) {
				t.Fatalf("got %#v want %#v", got, c.Jobs)
			}
		})
	}
}
