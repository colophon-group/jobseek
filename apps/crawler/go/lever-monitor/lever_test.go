package lever

import (
	"encoding/json"
	"testing"
)

func TestParsePageExcludesOnlyConfiguredBlankPosting(t *testing.T) {
	const blankID = "e0b53a9a-da36-4b09-a336-afb73d9ccffb"
	const otherID = "ff34692c-d3a5-4d41-a0be-49b06d695ea8"
	for _, filled := range []bool{false, true} {
		description := ""
		if filled {
			description = "<p>New duties</p>"
		}
		body, _ := json.Marshal([]map[string]any{
			{"id": blankID, "hostedUrl": "https://jobs.lever.co/rivieradininggroup/" + blankID, "text": "Prep Cook", "description": description, "categories": map[string]any{"location": "Winter Park, Florida"}},
			{"id": otherID, "hostedUrl": "https://jobs.lever.co/rivieradininggroup/" + otherID, "text": "Other Prep Cook", "description": "<p>Real duties</p>", "categories": map[string]any{"location": "Miami, Florida"}},
		})
		jobs, count, err := ParsePageWithBlankExclusions(body, map[string]bool{blankID: true})
		want := 1
		if filled {
			want = 2
		}
		if err != nil || count != 2 || len(jobs) != want || jobs[len(jobs)-1].Metadata["id"] != otherID {
			t.Fatalf("filled=%v count=%d jobs=%+v err=%v", filled, count, jobs, err)
		}
	}
}

func TestParseRichPage(t *testing.T) {
	body := []byte(`[
    {"hostedUrl":"https://jobs.lever.co/acme/1","text":"Engineer","description":"<p>Build</p>","lists":[{"text":"Benefits","content":"<li>Travel</li>"}],"additional":"<p>More</p>","categories":{"allLocations":["Zurich","London"],"commitment":"Full-time","department":"Engineering","team":"Platform"},"workplaceType":"hybrid","salaryRange":{"currency":"CHF","min":80000,"max":120000,"interval":"per-year-salary"},"id":"one"},
    {"text":"Invisible"}
  ]`)
	jobs, count, err := ParsePage(body)
	if err != nil {
		t.Fatal(err)
	}
	if count != 2 || len(jobs) != 1 {
		t.Fatalf("count=%d jobs=%d", count, len(jobs))
	}
	job := jobs[0]
	if job.URL != "https://jobs.lever.co/acme/1" || job.Description == nil || *job.Description != "<p>Build</p>\n<h3>Benefits</h3><ul><li>Travel</li></ul>\n<p>More</p>" {
		t.Fatalf("unexpected rich projection: %#v", job)
	}
	if job.BaseSalary["unit"] != "year" || job.Metadata["id"] != "one" || len(job.Locations) != 2 {
		t.Fatalf("unexpected salary, metadata or locations: %#v", job)
	}
}

func TestRejectPartialOrMalformedPage(t *testing.T) {
	for _, body := range []string{`{"jobs":[]}`, `[null]`, `[{"hostedUrl":"https://jobs.lever.co/acme/1","categories":{"allLocations":"bad"}}]`} {
		if _, _, err := ParsePage([]byte(body)); err == nil {
			t.Fatalf("accepted malformed page %s", body)
		}
	}
}

func TestProjectionJSONHasExpectedFields(t *testing.T) {
	jobs, _, err := ParsePage([]byte(`[{"hostedUrl":"https://jobs.lever.co/acme/1"}]`))
	if err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(jobs[0])
	if err != nil {
		t.Fatal(err)
	}
	var result map[string]any
	if err := json.Unmarshal(body, &result); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"url", "title", "description", "locations", "employment_type", "job_location_type", "base_salary", "metadata"} {
		if _, ok := result[field]; !ok {
			t.Fatalf("missing %s", field)
		}
	}
}
