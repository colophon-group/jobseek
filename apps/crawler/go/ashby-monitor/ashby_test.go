package ashby

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestParseRichJobsAndCompensation(t *testing.T) {
	body := []byte(`{"jobs":[{"jobUrl":"https://jobs.ashbyhq.com/acme/1","title":"Engineer","descriptionHtml":"<p>Build</p>","location":"Zurich","secondaryLocations":[{"location":"London"},"Zurich"],"employmentType":"FullTime","workplaceType":"Hybrid","publishedAt":"2026-09-01","department":"Engineering","id":"one","compensationTierSummary":"tier-1"},{"jobUrl":"https://jobs.ashbyhq.com/acme/2","isListed":false}],"compensation":{"compensationTierSummary":[{"id":"tier-1","min":80000,"max":120000,"currency":"CHF","interval":"annually"}]}}`)
	inventory, err := Parse(body)
	if err != nil {
		t.Fatal(err)
	}
	if len(inventory.Jobs) != 1 || inventory.Truncated {
		t.Fatalf("inventory=%+v", inventory)
	}
	encoded, err := json.Marshal(inventory.Jobs[0])
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(encoded, &got); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"url": "https://jobs.ashbyhq.com/acme/1", "title": "Engineer", "description": "<p>Build</p>", "locations": []any{"Zurich", "London"}, "employment_type": "FullTime", "job_location_type": "hybrid", "date_posted": "2026-09-01", "base_salary": map[string]any{"currency": "CHF", "min": float64(80000), "max": float64(120000), "unit": "year"}, "metadata": map[string]any{"department": "Engineering", "id": "one"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got=%#v want=%#v", got, want)
	}
}

func TestParseMalformedInputFailsClosed(t *testing.T) {
	for _, body := range []string{`[]`, `{"jobs":null}`, `{"jobs":[null]}`, `{"jobs":[{"jobUrl":"https://example.test/j","secondaryLocations":null}]}`, `{"jobs":[{"jobUrl":"https://example.test/j","address":{"city":4}}]}`} {
		if _, err := Parse([]byte(body)); err == nil {
			t.Errorf("accepted %s", body)
		}
	}
}

func TestSalaryUnits(t *testing.T) {
	for _, test := range []struct{ raw, want string }{{"annually", "year"}, {"per-hour-wage", "hour"}, {"monthly", "month"}, {"biweekly", "week"}, {"daily", "day"}, {"unknown", "year"}} {
		if got := salaryUnit(test.raw); got != test.want {
			t.Errorf("%s -> %s want %s", test.raw, got, test.want)
		}
	}
}
