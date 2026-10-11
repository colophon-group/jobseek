package queue

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
)

func TestOrderedDetailOriginalPublicStageFields(t *testing.T) {
	dir := os.Getenv("JOBSEEK_ORDERED_DETAIL_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original public stage captures")
	}
	var rows []struct {
		Slug, URL string
		Canonical map[string]string
		Step      int
		HTMLFile  string `json:"html_file"`
		Expected  map[string]any
	}
	body, err := os.ReadFile(filepath.Join(dir, "python-gold.json"))
	if err != nil || json.Unmarshal(body, &rows) != nil || len(rows) != 34 {
		t.Fatal("complete public stage gold unavailable", err)
	}
	for n, row := range rows {
		t.Run(fmt.Sprintf("%s-%d-%d", row.Slug, row.Step, n), func(t *testing.T) {
			chain, err := InspectOrderedDetail(jsonldBoardID, row.Canonical, row.URL, Simple)
			if err != nil || row.Step < 0 || row.Step >= len(chain.Stages) {
				t.Fatal("original stage unsupported", err)
			}
			f, err := os.Open(filepath.Join(dir, row.HTMLFile))
			if err != nil {
				t.Fatal(err)
			}
			defer f.Close()
			z, err := gzip.NewReader(f)
			if err != nil {
				t.Fatal(err)
			}
			defer z.Close()
			html, err := io.ReadAll(io.LimitReader(z, 4<<20+1))
			if err != nil || len(html) > 4<<20 {
				t.Fatal("bounded public HTML unavailable", err)
			}
			stage := chain.Stages[row.Step]
			var actual map[string]any
			if stage.JSONLDConfig != nil {
				actual, err = jsonld.Parse(row.URL, html, stage.JSONLDConfig)
			} else {
				actual, err = dom.Parse(string(html), stage.DOMConfig, &row.URL)
			}
			if err != nil {
				t.Fatal("public stage parse failed", err)
			}
			b, err := json.Marshal(actual)
			if err != nil {
				t.Fatal(err)
			}
			json.Unmarshal(b, &actual)
			if !reflect.DeepEqual(actual, row.Expected) {
				keys := []string{}
				for key, want := range row.Expected {
					if !reflect.DeepEqual(actual[key], want) {
						keys = append(keys, key)
					}
				}
				t.Fatal("original public stage fields differ", keys)
			}
		})
	}
}
