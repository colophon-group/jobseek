package dom

import (
	"encoding/json"
	"os"
	"testing"
	"time"
)

func TestOriginalListingPDFOwnershipAndDeadlineDecisions(t *testing.T) {
	var cases []struct {
		Name, Text, Now, Deadline string
		Config                    Object
		Keep, Error               bool
	}
	raw, e := os.ReadFile("testdata/python_listing_pdf_proofs.json")
	if e != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 22 {
		t.Fatal("original PDF reference missing", e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			p, e := PDFListingProofOptions(c.Config)
			if e != nil {
				t.Fatal(e)
			}
			now, e := time.Parse(time.RFC3339, c.Now)
			if e != nil {
				t.Fatal(e)
			}
			keep, date, e := p.Classify(c.Text, now)
			if (e != nil) != c.Error || e == nil && (keep != c.Keep || date != c.Deadline) {
				t.Fatal("original PDF classification differs", keep, date, e, c.Keep, c.Deadline, c.Error)
			}
		})
	}
}

func TestListingPDFRejectsIncompleteAndUnknownControls(t *testing.T) {
	for _, raw := range []string{`{"require_pdf_text":{"include":"a"}}`, `{"require_pdf_text":{"include":"a","exclude":"b","extra":true}}`, `{"require_unexpired_pdf":{"pattern":"x","date_format":"%Y"}}`, `{"require_unexpired_pdf":{"pattern":"(x)","date_format":"%Q"}}`, `{"require_unexpired_pdf":{"rules":[]}}`, `{"require_unexpired_pdf":{"active_window":{"pattern":"(?P<month>x)","date_format":"%B %d %Y"}}}`, `{"require_pdf_pattern":"Employer"}`} {
		m := Object{}
		if json.Unmarshal([]byte(raw), &m) != nil {
			t.Fatal("test JSON")
		}
		if _, e := PDFListingProofOptions(m); e == nil {
			t.Fatal("invalid PDF proof admitted", raw)
		}
	}
}
