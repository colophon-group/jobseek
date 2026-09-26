package pinpoint

import (
	"strings"
	"testing"
)

func TestParseRichPosting(t *testing.T) {
	input := `{"data":[{"url":"https://acme.pinpointhq.com/postings/1","title":"Engineer","description":"<p>Build</p>","key_responsibilities":"<p>Ship</p>","key_responsibilities_header":"Responsibilities","location":{"city":"Zürich","province":"ZH"},"employment_type":"full_time","workplace_type":"hybrid","deadline_at":"2026-10-01","compensation_minimum":100000,"compensation_maximum":120000,"compensation_currency":"CHF","compensation_frequency":"two_weeks","job":{"department":{"name":"Engineering"},"requisition_id":"REQ-1"}},{"title":"no URL"}]}`
	result, err := Parse([]byte(input))
	if err != nil || len(result.Jobs) != 1 || result.Truncated {
		t.Fatalf("unexpected inventory: %#v, %v", result, err)
	}
	job := result.Jobs[0]
	if job.URL != "https://acme.pinpointhq.com/postings/1" ||
		job.Description != "<p>Build</p>\n<h3>Responsibilities</h3>\n<p>Ship</p>" ||
		len(job.Locations) != 1 || job.Locations[0] != "Zürich, ZH" ||
		job.JobLocationType == nil || *job.JobLocationType != "hybrid" ||
		job.BaseSalary["unit"] != "week" || job.Metadata["requisition_id"] != "REQ-1" {
		t.Fatalf("unexpected rich fields: %#v", job)
	}
}

func TestParseRejectsMalformedWholeInventory(t *testing.T) {
	for _, input := range []string{
		`{"data":{}}`, `{"data":[{},true]}`, `{"data":[{"url":"x","location":[1]}]}`,
		`{"data":[]}{"data":[]}`,
	} {
		if _, err := Parse([]byte(input)); err == nil {
			t.Fatalf("accepted malformed inventory %s", input)
		}
	}
	if _, err := Parse([]byte(`{"data":[{"url":"x","description":42}]}`)); err == nil || !strings.Contains(err.Error(), "description") {
		t.Fatalf("accepted malformed description: %v", err)
	}
}
