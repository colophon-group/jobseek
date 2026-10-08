package apisniffer

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestPDFTextFieldsMatchActualPython(t *testing.T) {
	var cases []struct {
		Name, Text, Source string
		Config             map[string]any
		Output             map[string]any
		Error              bool
	}
	raw, err := os.ReadFile("testdata/python_pdf_text.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 17 {
		t.Fatal("actual PDF reference missing", err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, err := PDFOptionsFromConfig(c.Config)
			if err != nil {
				t.Fatal(err)
			}
			out, err := ParsePDFText(c.Text, c.Source, o)
			if (err != nil) != c.Error {
				t.Fatal(err, c.Error)
			}
			if c.Error {
				return
			}
			for key, want := range c.Output {
				if want != nil && !reflect.DeepEqual(out[key], want) {
					t.Fatal(key, out[key], want)
				}
			}
			for key, actual := range out {
				if !reflect.DeepEqual(actual, c.Output[key]) {
					t.Fatal(key, actual, c.Output[key])
				}
			}
		})
	}
}

func TestPDFCurrentRegistryOptionsAndRejectedContracts(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	h := map[string]int{}
	for i, k := range rows[0] {
		h[k] = i
	}
	count, ocr := 0, 0
	for _, row := range rows[1:] {
		if row[h["scraper_type"]] != "pdf" {
			continue
		}
		m := map[string]any{}
		if raw := row[h["scraper_config"]]; raw != "" && json.Unmarshal([]byte(raw), &m) != nil {
			t.Fatal("config")
		}
		delete(m, "enrich")
		o, err := PDFOptionsFromConfig(m)
		if err != nil {
			t.Fatal(row[h["board_slug"]], err)
		}
		if o.OCR {
			ocr++
		}
		count++
	}
	if count != 50 || ocr != 4 {
		t.Fatal(count, ocr)
	}
	for _, m := range []map[string]any{{"ocr_scale": 0}, {"ocr_scale": 5}, {"ocr_languages": "eng;sh"}, {"title_source": "other"}, {"require_title_pattern": true}, {"request_headers": map[string]any{"Authorization": "token"}}, {"defaults": map[string]any{"locations": []any{}}}, {"defaults": map[string]any{"base_salary": map[string]any{"min": -1}}}, {"other": true}} {
		if _, err := PDFOptionsFromConfig(m); err == nil {
			t.Fatal("unsupported contract admitted", m)
		}
	}
}
