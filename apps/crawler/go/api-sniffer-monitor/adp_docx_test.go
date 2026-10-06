package apisniffer

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"testing"
)

func TestADPDocxFallbackMatchesActualPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_adp_docx.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, Body string
		Expected   *string
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 10 {
		t.Fatal("actual Python DOCX corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			body, e := base64.StdEncoding.DecodeString(c.Body)
			if e != nil {
				t.Fatal(e)
			}
			want := ""
			if c.Expected != nil {
				want = *c.Expected
			}
			if got := ADPDocxToHTML(body); got != want {
				t.Fatalf("DOCX fallback differs: actual=%q expected=%q", got, want)
			}
		})
	}
}
