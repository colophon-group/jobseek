package recruitee

import (
	"encoding/json"
	"testing"
)

func TestParsePublishedRichOffer(t *testing.T) {
	body := []byte(`{"offers":[
		{"status":"published","careers_url":"https://acme.recruitee.com/o/engineer","title":"Engineer",
		 "description":"<p>Build</p>","requirements":"<p>Test</p>",
		 "locations":[{"city":"Zurich","country":"CH"},{"city":"Zurich","country":"CH"}],
		 "remote":true,"employment_type_code":"fulltime_permanent","published_at":"2026-09-24",
		 "salary":{"min":100000,"max":120000,"currency":"CHF","period":"annual"},
		 "department":"Engineering","tags":["Go"],"category_code":"ENG","id":123},
		{"status":"draft","careers_url":"https://acme.recruitee.com/o/draft"}
	]}`)
	inventory, err := Parse(body)
	if err != nil || len(inventory.Jobs) != 1 || inventory.Truncated {
		t.Fatalf("unexpected inventory: %#v, %v", inventory, err)
	}
	job := inventory.Jobs[0]
	if job.URL != "https://acme.recruitee.com/o/engineer" ||
		job.Description != "<p>Build</p>\n<p>Test</p>" ||
		len(job.Locations) != 1 || job.Locations[0] != "Zurich, CH" ||
		job.JobLocationType == nil || *job.JobLocationType != "remote" ||
		job.BaseSalary["unit"] != "year" || job.Metadata["category"] != "ENG" {
		t.Fatalf("unexpected rich job: %#v", job)
	}
	if job.BaseSalary["min"] != json.Number("100000") {
		t.Fatalf("salary number changed: %#v", job.BaseSalary)
	}
}

func TestParseFailsClosedOnMalformedOffers(t *testing.T) {
	for _, body := range []string{
		`{"offers":null}`,
		`{"offers":"not-an-array"}`,
		`{"offers":[{"status":"published","careers_url":"https://example.com","locations":{}}]}`,
		`{"offers":[{"status":"published","careers_url":"https://example.com","salary":"unknown"}]}`,
		`{"offers":[{"status":"published","careers_url":"https://example.com","salary":{"min":1,"period":5}}]}`,
		`{"offers":[]} trailing`,
	} {
		if _, err := Parse([]byte(body)); err == nil {
			t.Fatalf("accepted malformed response: %s", body)
		}
	}
}
